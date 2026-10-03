"""Supplementary measurements requested in review.

Usage: supplement.py <owner/name> <measurement_dir> <out_dir>

For one measured repository this script re-derives the deterministic file
samples of measure.py and records:

  A  line-level comparisons on the touched sample: how often W and WMC recover
     the developer that DECLARED re-attributes, how often they reassign lines
     DECLARED leaves alone, the effect of the declaration on top of -w
     (lane DECLARED_W), and whether each re-attributed line maps to an earlier
     line with the same content;
  B  persistence of every declared commit (up to 40 per repository) and of a
     time- and size-matched undeclared commit: the share of the lines each
     commit added that plain blame still credits to it;
  C  for the historical sample: recency and activity baselines, developer
     names for identity checks, bot flags, and later bug-fix commits.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from measurement_contract import GitRunner, IdentityNormalizer  # noqa: E402
from measure import (  # noqa: E402
    BLAME_TIMEOUT, CLONES, HEADER, MAX_FILES, eligible, git, text_files, touched_by,
)

MAX_PERSIST_COMMITS = 40
PERSIST_FILES = 3
BOT = re.compile(r"\[bot\]|(^|[-_.\s<])bot($|[-_.@\s>])|github-actions|dependabot|renovate|pre-commit-ci|"
                 r"actions@github\.com", re.I)
FIX = re.compile(r"\bfix(e[sd])?\b|\bbug|\bdefect|\bfault|\bcrash|\bregression|\bpatch", re.I)


def is_bot(name: str, email: str) -> bool:
    return bool(BOT.search(name) or BOT.search(email))


def iso(ts: int) -> str:
    import datetime
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def porcelain(repo, commit, path, extra, ignore_file=None):
    args = ["-c", "blame.markIgnoredLines=true", "-c", "blame.markUnblamableLines=true",
            "blame", "--line-porcelain", *extra]
    if ignore_file:
        args += ["--ignore-revs-file", ignore_file]
    data = git(repo, *args, commit, "--", path, timeout=BLAME_TIMEOUT)
    out, lines, i = [], data.split(b"\n"), 0
    while i < len(lines):
        if not lines[i]:
            i += 1
            continue
        m = HEADER.match(lines[i])
        sha = m.group(1).decode()
        orig = int(lines[i].split(b" ")[1])
        meta, flags = {}, set()
        i += 1
        while i < len(lines) and not lines[i].startswith(b"\t"):
            k, _, v = lines[i].partition(b" ")
            if v:
                meta[k] = v
            else:
                flags.add(k)
            i += 1
        content = lines[i][1:] if i < len(lines) else b""
        i += 1
        out.append({
            "sha": sha, "orig": orig, "file": meta.get(b"filename", b"").decode(errors="replace"),
            "name": meta.get(b"author", b"").decode(errors="replace"),
            "mail": meta.get(b"author-mail", b"").decode(errors="replace"),
            "unbl": b"unblamable" in flags, "content": content,
        })
    return out


def norm_ws(b: bytes) -> bytes:
    return re.sub(rb"\s+", b"", b)


def norm_alnum(b: bytes) -> bytes:
    return re.sub(rb"[^A-Za-z0-9]", b"", b)


def shares(c):
    t = sum(c.values())
    return {k: v / t for k, v in c.items()} if t else {}


def tvd(a, b):
    pa, pb = shares(a), shares(b)
    return 0.5 * sum(abs(pa.get(k, 0) - pb.get(k, 0)) for k in set(pa) | set(pb))


def top(c):
    if not c:
        return set()
    m = max(c.values())
    return {k for k, v in c.items() if v == m}


def part_a(repo, record, identity, declared, ignore_file, sizes):
    paths = eligible(touched_by(repo, declared), sizes, "touched")[:MAX_FILES]
    dset = set(declared)
    blob_cache = {}
    rows = []
    for path in paths:
        try:
            lanes = {
                "RAW": porcelain(repo, record["head"], path, []),
                "W": porcelain(repo, record["head"], path, ["-w"]),
                "WMC": porcelain(repo, record["head"], path, ["-w", "-M", "-C"]),
                "DECLARED": porcelain(repo, record["head"], path, [], ignore_file),
                "DECLARED_W": porcelain(repo, record["head"], path, ["-w"], ignore_file),
            }
        except Exception:  # noqa: BLE001
            continue
        n = len(lanes["RAW"])
        if n == 0 or any(len(v) != n for v in lanes.values()):
            continue
        dev = {k: [None if r["unbl"] else identity.developer_id(r["name"], r["mail"]) for r in v] for k, v in lanes.items()}
        row = {"path_hash": hashlib.sha256(path.encode()).hexdigest()[:16], "lines": n}
        captured = [lanes["RAW"][i]["sha"] in dset for i in range(n)]
        reattr = [i for i in range(n) if captured[i] and dev["DECLARED"][i] is not None and dev["DECLARED"][i] != dev["RAW"][i]]
        row["captured"] = sum(captured)
        row["reattributed"] = len(reattr)
        for lane in ("W", "WMC"):
            row[f"{lane}_recovers"] = sum(1 for i in reattr if dev[lane][i] == dev["DECLARED"][i])
            unc = [i for i in range(n) if not captured[i]]
            row[f"{lane}_uncaptured"] = len(unc)
            row[f"{lane}_uncaptured_changed"] = sum(1 for i in unc if dev[lane][i] != dev["RAW"][i])
        # Effect of the declaration on top of -w.
        cw = Counter(d for d in dev["W"] if d)
        cdw = Counter(d for d in dev["DECLARED_W"] if d)
        row["dw_tvd"] = tvd(cw, cdw)
        row["dw_flip"] = int(top(cw) != top(cdw))
        # Content check of re-attributed lines: does the credited earlier line
        # have the same content up to whitespace / up to punctuation?
        ws_eq = alnum_eq = checked = 0
        for i in reattr:
            r = lanes["DECLARED"][i]
            if r["sha"] in dset:
                continue
            key = (r["sha"], r["file"])
            if key not in blob_cache:
                try:
                    blob_cache[key] = git(repo, "show", f"{r['sha']}:{r['file']}").split(b"\n")
                except Exception:  # noqa: BLE001
                    blob_cache[key] = None
            blob = blob_cache[key]
            if blob is None or r["orig"] - 1 >= len(blob):
                continue
            before, now = blob[r["orig"] - 1], lanes["RAW"][i]["content"]
            if not norm_alnum(now):
                continue  # punctuation-only lines match trivially
            checked += 1
            ws_eq += norm_ws(before) == norm_ws(now)
            alnum_eq += norm_alnum(before) == norm_alnum(now)
        row.update(content_checked=checked, content_ws_equal=ws_eq, content_alnum_equal=alnum_eq)
        # Same-name check and bots for owners.
        raw_owner, decl_owner = top(Counter(d for d in dev["RAW"] if d)), top(Counter(d for d in dev["DECLARED"] if d))
        row["flip"] = int(raw_owner != decl_owner)
        rows.append(row)
    return rows


def survival(repo, head, sha, files, identity_unused=None):
    """Share of the lines a commit added in the given files that blame still credits to it."""
    added = credited = 0
    for path in files:
        numstat = git(repo, "show", "--numstat", "--format=", sha, "--", path).decode(errors="replace").split()
        if len(numstat) < 2 or numstat[0] == "-":
            continue
        try:
            data = git(repo, "blame", "--porcelain", head, "--", path, timeout=BLAME_TIMEOUT)
        except Exception:  # noqa: BLE001
            continue
        added += int(numstat[0])
        credited += sum(1 for l in data.split(b"\n") if (m := HEADER.match(l)) and m.group(1).decode() == sha)
    return added, credited


def part_b(repo, record, declared, sizes):
    rng = random.Random(record["full_name"])
    head = record["head"]
    dset = set(declared)
    log = git(repo, "log", "--no-merges", "--format=@%H %ct", "--numstat", head).decode(errors="replace")
    commits, cur = [], None
    for line in log.splitlines():
        if line.startswith("@"):
            sha, ts = line[1:].split()
            cur = {"sha": sha, "time": int(ts), "added": 0, "files": [], "per_file": {}}
            commits.append(cur)
        elif line.strip() and cur is not None:
            parts = line.split("\t")
            if len(parts) == 3 and parts[0] != "-":
                cur["added"] += int(parts[0])
                if int(parts[0]) > 0:
                    cur["files"].append(parts[2])
                    cur["per_file"][parts[2]] = int(parts[0])
    by_sha = {c["sha"]: c for c in commits}
    undeclared = [c for c in commits if c["sha"] not in dset and c["added"] > 0]
    chosen = list(declared)
    rng.shuffle(chosen)
    rows = []
    for sha in chosen[:MAX_PERSIST_COMMITS]:
        c = by_sha.get(sha)
        if not c or c["added"] == 0:
            continue
        files = eligible([f for f in c["files"]], sizes, sha)[:PERSIST_FILES]
        if not files:
            continue
        added, credited = survival(repo, head, sha, files)
        if added == 0:
            continue
        # Matched undeclared commit: nearest in time whose measured files have
        # 0.5x-2x the added lines of the declared commit's measured files.
        m_added = m_credited = None
        for u in sorted(undeclared, key=lambda u: abs(u["time"] - c["time"]))[:200]:
            m_files = eligible(u["files"], sizes, u["sha"])[:PERSIST_FILES]
            planned = sum(u["per_file"].get(f, 0) for f in m_files)
            if m_files and 0.5 * added <= planned <= 2 * added:
                m_added, m_credited = survival(repo, head, u["sha"], m_files)
                break
        rows.append({"sha": sha, "age_years": (record["head_time"] - c["time"]) / (365.25 * 86400),
                     "added": added, "credited": credited, "match_added": m_added, "match_credited": m_credited})
    return rows


def part_c(repo, record, identity):
    past = record.get("past_commit")
    if not past or not record.get("past"):
        return [], {}
    declared = [m["sha"] for m in record["declared_meta"]]
    runner = GitRunner(repo)
    past_declared = [s for s in declared if runner.is_ancestor(s, past)]
    sizes = text_files(repo, past)
    paths = eligible(touched_by(repo, past_declared), sizes, "past")[:MAX_FILES]
    by_hash = {hashlib.sha256(p.encode()).hexdigest()[:16]: p for p in paths}
    past_time = int(git(repo, "show", "-s", "--format=%ct", past).decode().strip())
    before = past_time - 365 * 86400
    dset = set(declared)
    names, bots = {}, set()
    log = git(repo, "log", "--format=%an%x00%ae", record["head"]).decode(errors="replace")
    for line in set(log.splitlines()):
        if "\0" not in line:
            continue
        n, e = line.split("\0", 1)
        d = identity.developer_id(n, e)
        names.setdefault(d, set()).add(" ".join(n.lower().split()))
        if is_bot(n, e):
            bots.add(d)
    repo_prior = Counter()
    for line in git(repo, "log", "--no-merges", f"--since={iso(before)}", "--format=%an%x00%ae", past).decode(errors="replace").splitlines():
        if "\0" in line:
            repo_prior[identity.developer_id(*line.split("\0", 1))] += 1
    rows = []
    for f in record["past"]:
        path = by_hash.get(f.get("path_hash"))
        if f.get("status") != "OK" or path is None:
            continue
        prior, last = Counter(), None
        for line in git(repo, "log", "--no-merges", f"--since={iso(before)}", "--format=%H%x00%an%x00%ae", past, "--", path).decode(errors="replace").splitlines():
            sha, n, e = line.split("\0")
            if sha in dset:
                continue
            d = identity.developer_id(n, e)
            prior[d] += 1
            if last is None:
                last = d
        if last is None:
            for line in git(repo, "log", "--no-merges", "-n", "50", "--format=%H%x00%an%x00%ae", past, "--", path).decode(errors="replace").splitlines():
                sha, n, e = line.split("\0")
                if sha not in dset:
                    last = identity.developer_id(n, e)
                    break
        fixes = 0
        future_nobot = Counter()
        for line in git(repo, "log", "--no-merges", "--format=%H%x00%an%x00%ae%x00%s", f"{past}..{record['head']}", "--", path).decode(errors="replace").splitlines():
            sha, n, e, s = line.split("\0", 3)
            if sha in dset:
                continue
            fixes += bool(FIX.search(s))
            if not is_bot(n, e):
                future_nobot[identity.developer_id(n, e)] += 1
        rows.append({"path_hash": f["path_hash"], "prior": dict(prior), "last": last, "fixes": fixes,
                     "future_nobot": dict(future_nobot)})
    meta = {"names": {d: sorted(v) for d, v in names.items()}, "bots": sorted(bots),
            "repo_prior": dict(repo_prior), "ignore_file_at_past": runner.file_exists(past, ".git-blame-ignore-revs")}
    return rows, meta


def main():
    full_name, src, out = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
    out.mkdir(parents=True, exist_ok=True)
    target = out / (full_name.replace("/", "__") + ".json")
    parts = sys.argv[4].split(",") if len(sys.argv) > 4 else ["a", "b", "c"]
    previous = json.loads(target.read_text()) if target.exists() else None
    if previous is not None and all(k in previous and previous.get("_v2", {}).get(k) for k in parts):
        return
    record = json.loads((src / (full_name.replace("/", "__") + ".json")).read_text())
    if record.get("status") != "OK":
        return
    repo = CLONES / full_name.replace("/", "__")
    identity = IdentityNormalizer(GitRunner(repo), record["head"], scope_id=full_name)
    declared = [m["sha"] for m in record["declared_meta"]]
    sizes = text_files(repo, record["head"])
    with tempfile.NamedTemporaryFile("w", suffix=".revs", delete=False) as handle:
        handle.write("\n".join(declared) + "\n")
        ignore_file = handle.name
    result = previous or {"full_name": full_name}
    result.setdefault("_v2", {})
    for key, fn in (("a", lambda: part_a(repo, record, identity, declared, ignore_file, sizes)),
                    ("b", lambda: part_b(repo, record, declared, sizes)),
                    ("c", lambda: part_c(repo, record, identity))):
        if key not in parts:
            continue
        result["_v2"][key] = True
        try:
            result[key] = fn()
        except Exception as exc:  # noqa: BLE001
            result[key + "_error"] = str(exc)[:300]
    target.write_text(json.dumps(result))
    print(full_name, "done", flush=True)


if __name__ == "__main__":
    main()
