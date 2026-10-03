"""Measure blame attribution under four lanes for one repository.

Usage: measure.py <owner/name> <out_dir>

Lanes:
  RAW       git blame
  W         git blame -w
  WMC       git blame -w -M -C
  DECLARED  git blame --ignore-revs-file <revisions declared in .git-blame-ignore-revs>

Three file samples are measured:
  touched   files at HEAD that a declared revision modified (RQ1, RQ4)
  random    files drawn from the whole HEAD tree (repository-wide estimate)
  past      files touched by declared revisions at a snapshot one year before
            HEAD, with the developers who changed each file afterwards (RQ3)

Developer identities are normalized with the HEAD .mailmap and hashed.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from measurement_contract import (  # noqa: E402
    GitRunner,
    IdentityNormalizer,
    declaration_snapshot,
)

GIT = "/opt/homebrew/bin/git"
CLONES = ROOT / "clones"
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
MAX_FILES = 25
MAX_LINES = 5000
BLAME_TIMEOUT = 120
SAMPLE_BUDGET = 15 * 60  # seconds of blame work per file sample and repository
HORIZON_DAYS = 365
LANES = ("RAW", "W", "WMC", "DECLARED")
PAIRS = (("RAW", "DECLARED"), ("W", "DECLARED"), ("WMC", "DECLARED"), ("RAW", "W"), ("RAW", "WMC"))
HEADER = re.compile(rb"^\^?([0-9a-f]{40}) \d+ (\d+)(?: (\d+))?$")
SKIP_NAME = re.compile(
    r"(^|/)(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|poetry\.lock|Cargo\.lock|Gemfile\.lock|"
    r"composer\.lock|go\.sum|uv\.lock)$|\.min\.(js|css)$|(^|/)(vendor|third_party|node_modules)/"
)


def git(repo: Path, *args: str, timeout: int | None = None) -> bytes:
    env = {"LC_ALL": "C", "GIT_CONFIG_NOSYSTEM": "1", "PATH": "/usr/bin:/bin:/opt/homebrew/bin"}
    result = subprocess.run([GIT, *args], cwd=repo, env=env, capture_output=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:3])} failed: {result.stderr.decode(errors='replace')[:300]}")
    return result.stdout


def clone(full_name: str) -> Path:
    target = CLONES / full_name.replace("/", "__")
    if not (target / "HEAD").exists():
        CLONES.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [GIT, "clone", "--bare", "--quiet", f"https://github.com/{full_name}.git", str(target)],
            check=True, timeout=3600, capture_output=True,
        )
    return target


def parse_porcelain(data: bytes):
    """Return (commit, author, email, ignored, unblamable) per final line."""
    lines = data.split(b"\n")
    out = []
    i = 0
    while i < len(lines):
        if not lines[i]:
            i += 1
            continue
        match = HEADER.match(lines[i])
        if match is None:
            raise ValueError(f"bad porcelain header: {lines[i][:80]!r}")
        sha = match.group(1).decode()
        meta = {}
        flags = set()
        i += 1
        while i < len(lines) and not lines[i].startswith(b"\t"):
            key, _, value = lines[i].partition(b" ")
            if value:
                meta[key] = value
            else:
                flags.add(key)
            i += 1
        i += 1  # content line
        out.append((
            sha,
            meta.get(b"author", b"").decode(errors="replace"),
            meta.get(b"author-mail", b"").decode(errors="replace"),
            b"ignored" in flags,
            b"unblamable" in flags,
        ))
    return out


def blame(repo: Path, commit: str, path: str, lane: str, ignore_file: str):
    args = ["-c", "blame.markIgnoredLines=true", "-c", "blame.markUnblamableLines=true", "blame", "--line-porcelain"]
    if lane in ("W", "WMC"):
        args.append("-w")
    if lane == "WMC":
        args += ["-M", "-C"]
    if lane == "DECLARED":
        args += ["--ignore-revs-file", ignore_file]
    args += [commit, "--", path]
    return parse_porcelain(git(repo, *args, timeout=BLAME_TIMEOUT))


def text_files(repo: Path, commit: str) -> dict[str, int]:
    """Map path -> line count for text files in a commit's tree."""
    numstat = git(repo, "diff-tree", "-r", "--numstat", "-z", EMPTY_TREE, commit)
    sizes = {}
    for entry in numstat.split(b"\0"):
        parts = entry.split(b"\t")
        if len(parts) == 3 and parts[0] != b"-":
            sizes[parts[2].decode(errors="replace")] = int(parts[0])
    return sizes


def eligible(paths, sizes, salt: str) -> list[str]:
    keep = [
        p for p in paths
        if p in sizes and 0 < sizes[p] <= MAX_LINES and not SKIP_NAME.search(p) and p != ".git-blame-ignore-revs"
    ]
    keep.sort(key=lambda p: hashlib.sha256((salt + p).encode()).hexdigest())
    return keep


def touched_by(repo: Path, shas) -> set[str]:
    touched = set()
    for sha in shas:
        names = git(repo, "diff-tree", "--root", "-r", "--no-commit-id", "--name-only", "-z", sha)
        touched.update(p.decode(errors="replace") for p in names.split(b"\0") if p)
    return touched


DEADLINE = [float("inf")]


def measure_file(repo, commit, path, ignore_file, identity, declared_set):
    entry = {"path_hash": hashlib.sha256(path.encode()).hexdigest()[:16], "ext": Path(path).suffix.lower()}
    if time.time() > DEADLINE[0]:
        entry["status"] = "BUDGET_EXHAUSTED"
        return entry, None
    try:
        lanes = {lane: blame(repo, commit, path, lane, ignore_file) for lane in LANES}
    except subprocess.TimeoutExpired:
        entry["status"] = "TIMEOUT"
        return entry, None
    except Exception as exc:  # noqa: BLE001
        entry["status"] = "BLAME_FAILED"
        entry["error"] = str(exc)[:200]
        return entry, None
    n = len(lanes["RAW"])
    if n == 0 or any(len(v) != n for v in lanes.values()):
        entry["status"] = "LENGTH_MISMATCH"
        return entry, None
    dev = {
        lane: [None if unbl else identity.developer_id(a, e) for (_, a, e, _, unbl) in rows]
        for lane, rows in lanes.items()
    }
    commits = {lane: [row[0] for row in rows] for lane, rows in lanes.items()}
    entry["status"] = "OK"
    entry["lines"] = n
    entry["raw_declared_lines"] = sum(1 for c in commits["RAW"] if c in declared_set)
    entry["raw_declared_by_rev"] = dict(Counter(c for c in commits["RAW"] if c in declared_set))
    entry["ignored_lines"] = sum(1 for row in lanes["DECLARED"] if row[3])
    entry["unblamable_lines"] = sum(1 for row in lanes["DECLARED"] if row[4])
    entry["dev_counts"] = {lane: dict(Counter(d for d in devs if d is not None)) for lane, devs in dev.items()}
    entry["pairs"] = {}
    for a, b in PAIRS:
        both = [(x, y) for x, y in zip(dev[a], dev[b]) if x is not None and y is not None]
        entry["pairs"][f"{a}|{b}"] = {
            "author_diff": sum(1 for x, y in both if x != y),
            "author_n": len(both),
            "commit_diff": sum(1 for x, y in zip(commits[a], commits[b]) if x != y),
        }
    return entry, dev


def future_commits(repo, start, end, path, identity, declared_all):
    """Developer -> commit counts for non-merge commits touching path in (start, end]."""
    log = git(repo, "log", "--no-merges", "--format=%H%x00%an%x00%ae", f"{start}..{end}", "--", path)
    counts, counts_excl = Counter(), Counter()
    for line in log.decode(errors="replace").splitlines():
        sha, name, email = line.split("\0")
        dev = identity.developer_id(name, email)
        counts[dev] += 1
        if sha not in declared_all:
            counts_excl[dev] += 1
    return dict(counts), dict(counts_excl)


def measure(full_name: str) -> dict:
    started = time.time()
    record = {"full_name": full_name}
    repo = clone(full_name)
    runner = GitRunner(repo)
    head = git(repo, "rev-parse", "HEAD").decode().strip()
    head_time = int(git(repo, "show", "-s", "--format=%ct", head).decode().strip())
    record.update(head=head, head_time=head_time)
    record["commits_total"] = int(git(repo, "rev-list", "--count", head).decode().strip())
    record["first_commit_time"] = int(git(repo, "log", "--reverse", "--format=%ct", "--max-parents=0", head).decode().split()[0])
    snapshot = declaration_snapshot(runner, head)
    declared = [s for s in snapshot.resolved_shas if runner.is_ancestor(s, head)]
    raw_text = runner.show_file(head, ".git-blame-ignore-revs") if runner.file_exists(head, ".git-blame-ignore-revs") else ""
    entries = [l.split("#", 1)[0].strip() for l in raw_text.splitlines()]
    entries = [e for e in entries if e]
    record["declaration"] = {
        "entries": len(entries),
        "resolved": len(snapshot.resolved_shas),
        "invalid": len(snapshot.invalid_entries),
        "ancestors": len(declared),
        "file_added_time": int((git(repo, "log", "--diff-filter=A", "--format=%ct", head, "--", ".git-blame-ignore-revs").decode().split() or ["0"])[-1]),
    }
    record["declared_meta"] = []
    for sha in declared:
        stat = git(repo, "show", "-s", "--format=%ct%x00%an%x00%s", sha).decode(errors="replace").split("\0")
        shortstat = git(repo, "diff-tree", "--root", "--shortstat", "--no-commit-id", sha).decode().strip()
        record["declared_meta"].append({"sha": sha, "time": int(stat[0]), "subject": stat[2][:200], "shortstat": shortstat})
    if not declared:
        record["status"] = "NO_ANCESTOR_DECLARATIONS"
        return record
    identity = IdentityNormalizer(runner, head, scope_id=full_name)
    declared_set = set(declared)
    with tempfile.NamedTemporaryFile("w", suffix=".revs", delete=False) as handle:
        handle.write("\n".join(declared) + "\n")
        ignore_head = handle.name

    # Touched and random samples at HEAD.
    sizes = text_files(repo, head)
    touched = eligible(touched_by(repo, declared), sizes, "touched")
    record["head_text_files"] = len(sizes)
    record["touched_eligible"] = len(touched)
    DEADLINE[0] = time.time() + SAMPLE_BUDGET
    record["touched"] = [measure_file(repo, head, p, ignore_head, identity, declared_set)[0] for p in touched[:MAX_FILES]]
    random_paths = eligible(sizes.keys(), sizes, "random")[:MAX_FILES]
    DEADLINE[0] = time.time() + SAMPLE_BUDGET
    record["random"] = [measure_file(repo, head, p, ignore_head, identity, declared_set)[0] for p in random_paths]

    # Historical snapshot for prospective maintenance alignment.
    cutoff = head_time - HORIZON_DAYS * 86400
    past = git(repo, "rev-list", "--first-parent", "-1", f"--before={cutoff}", head).decode().strip()
    record["past"] = []
    if past:
        past_declared = [s for s in declared if runner.is_ancestor(s, past)]
        record["past_commit"] = past
        record["past_declared"] = len(past_declared)
        if past_declared:
            with tempfile.NamedTemporaryFile("w", suffix=".revs", delete=False) as handle:
                handle.write("\n".join(past_declared) + "\n")
                ignore_past = handle.name
            past_sizes = text_files(repo, past)
            past_paths = eligible(touched_by(repo, past_declared), past_sizes, "past")
            record["past_eligible"] = len(past_paths)
            DEADLINE[0] = time.time() + SAMPLE_BUDGET
            for path in past_paths[:MAX_FILES]:
                entry, _ = measure_file(repo, past, path, ignore_past, identity, set(past_declared))
                if entry["status"] == "OK":
                    entry["future"], entry["future_excl_declared"] = future_commits(
                        repo, past, head, path, identity, declared_set
                    )
                    entry["exists_at_head"] = path in sizes
                record["past"].append(entry)
    record["status"] = "OK"
    record["seconds"] = round(time.time() - started, 1)
    return record


def main():
    full_name, out_dir = sys.argv[1], Path(sys.argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / (full_name.replace("/", "__") + ".json")
    if target.exists():
        print(full_name, "SKIP")
        return
    try:
        record = measure(full_name)
    except Exception as exc:  # noqa: BLE001
        record = {"full_name": full_name, "status": "REPO_FAILED", "error": str(exc)[:500]}
    target.write_text(json.dumps(record))
    print(full_name, record.get("status"), record.get("seconds"), flush=True)


if __name__ == "__main__":
    main()
