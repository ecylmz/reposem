"""Size-matched unlabeled commits for RQ5 (decision D-005).

For each positive of the pools run, draw up to K unlabeled commits of the same repository
whose size bin matches: floor(log2(changed lines + 1)). Candidates are non-merge,
non-root commits up to the analyzed revision that the ignore file does not declare.
Draws are without replacement within a repository. Writes matched.csv and counts.json.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
from collections import defaultdict
from pathlib import Path

from common import CONFIG, ROOT, clone_dir, git, seed_for

K = 3


def sizes(repo: Path, head: str) -> dict[str, int]:
    out = git(repo, "log", "--no-merges", "--min-parents=1", "--no-renames", "--format=%x00%H", "--numstat", head,
              timeout=3600).decode("utf-8", "replace")
    res = {}
    for block in out.split("\x00")[1:]:
        lines = block.strip().split("\n")
        sha, total = lines[0].strip(), 0
        for l in lines[1:]:
            p = l.split("\t")
            if len(p) == 3 and p[0] != "-":
                total += int(p[0]) + int(p[1])
        res[sha] = total
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pools", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    pos = defaultdict(list)
    declared = defaultdict(set)
    with open(args.pools) as f:
        for r in csv.DictReader(f):
            if r["label"] == "1":
                pos[r["repo"]].append(r["sha"])
    with open(ROOT / CONFIG["declared_csv"]) as f:
        for r in csv.DictReader(f):
            declared[r["repo"]].add(r["sha"])
    counts = {"positives": 0, "matched": 0, "short": 0, "failed": []}
    with open(out / "matched.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["repo", "sha", "label", "matched_to"])
        for repo in sorted(pos):
            try:
                study = json.loads((ROOT / CONFIG["study_dir"] / (repo.replace("/", "__") + ".json")).read_text())
                sz = sizes(clone_dir(repo), study["head"])
            except Exception as exc:  # noqa: BLE001
                counts["failed"].append([repo, repr(exc)[:200]])
                continue
            bins = defaultdict(list)
            for sha, n in sz.items():
                if sha not in declared[repo] and n > 0:
                    bins[int(math.log2(n + 1))].append(sha)
            rng = random.Random(seed_for("matched", repo))
            for b in bins.values():
                b.sort()
                rng.shuffle(b)
            for p in sorted(pos[repo]):
                counts["positives"] += 1
                n = sz.get(p, 0)
                pool = bins.get(int(math.log2(n + 1)), [])
                take = [pool.pop() for _ in range(min(K, len(pool)))]
                counts["matched"] += len(take)
                counts["short"] += K - len(take)
                for s in take:
                    w.writerow([repo, s, 0, p])
    (out / "counts.json").write_text(json.dumps(counts, indent=2))
    print(json.dumps({k: v if not isinstance(v, list) else len(v) for k, v in counts.items()}))


if __name__ == "__main__":
    main()
