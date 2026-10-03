"""RQ4 sensitivity: later-commit ledger without bulk commits.

For every historical-sample file, count later non-merge commits per developer
excluding declared commits and commits that touch more than BULK files.

Usage: bulk.py <owner/name> <measurement_dir> <out_dir>
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from measurement_contract import GitRunner, IdentityNormalizer  # noqa: E402
from measure import CLONES, MAX_FILES, eligible, git, text_files, touched_by  # noqa: E402

BULK = 20


def main():
    full_name, src, out = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
    out.mkdir(parents=True, exist_ok=True)
    target = out / (full_name.replace("/", "__") + ".json")
    if target.exists():
        return
    record = json.loads((src / (full_name.replace("/", "__") + ".json")).read_text())
    if record.get("status") != "OK" or not record.get("past"):
        return
    repo = CLONES / full_name.replace("/", "__")
    runner = GitRunner(repo)
    identity = IdentityNormalizer(runner, record["head"], scope_id=full_name)
    past, head = record["past_commit"], record["head"]
    declared = {m["sha"] for m in record["declared_meta"]}
    past_declared = [s for s in declared if runner.is_ancestor(s, past)]
    paths = eligible(touched_by(repo, past_declared), text_files(repo, past), "past")[:MAX_FILES]
    by_hash = {hashlib.sha256(p.encode()).hexdigest()[:16]: p for p in paths}
    # Size of every later commit, once per repository.
    size = Counter()
    sha = None
    for line in git(repo, "log", "--no-merges", "--format=@%H", "--name-only", f"{past}..{head}").decode(errors="replace").splitlines():
        if line.startswith("@"):
            sha = line[1:]
        elif line.strip() and sha:
            size[sha] += 1
    rows = []
    for f in record["past"]:
        path = by_hash.get(f.get("path_hash"))
        if f.get("status") != "OK" or path is None:
            continue
        counts = Counter()
        for line in git(repo, "log", "--no-merges", "--format=%H%x00%an%x00%ae", f"{past}..{head}", "--", path).decode(errors="replace").splitlines():
            c, n, e = line.split("\0")
            if c in declared or size[c] > BULK:
                continue
            counts[identity.developer_id(n, e)] += 1
        rows.append({"path_hash": f["path_hash"], "future_no_bulk": dict(counts)})
    target.write_text(json.dumps({"full_name": full_name, "files": rows}))


if __name__ == "__main__":
    main()
