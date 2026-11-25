"""Compute the diff features of every commit in a pool file, in parallel over repositories.

Input: a CSV with the columns repo and sha (other columns are copied).
Output: --out/features.csv with one row per commit, plus status and reason for failures.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from common import CONFIG, FEATURES, clone_dir, commit_features

EXTRA = ["n_files", "n_lines"]


def run_repo(item):
    repo, rows = item
    out = []
    path = clone_dir(repo)
    for row in rows:
        try:
            f = commit_features(path, row["sha"])
            out.append({**row, **f, "status": "ok", "reason": ""})
        except Exception as exc:  # noqa: BLE001 - recorded as a failed row
            out.append({**row, "status": "failed", "reason": repr(exc)[:200]})
    return repo, out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    groups = defaultdict(list)
    with open(args.pool) as f:
        reader = csv.DictReader(f)
        base_cols = reader.fieldnames
        for row in reader:
            groups[row["repo"]].append(row)
    cols = list(base_cols) + FEATURES + EXTRA + ["status", "reason"]
    done, failed = 0, 0
    with open(out / "features.csv", "w", newline="") as fh, ProcessPoolExecutor(CONFIG["workers"]) as ex:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for repo, rows in ex.map(run_repo, sorted(groups.items()), chunksize=1):
            for r in rows:
                w.writerow(r)
                failed += r["status"] != "ok"
            done += 1
            fh.flush()
            if done % 25 == 0:
                print(f"{done}/{len(groups)} repositories, {failed} failed commits", flush=True)
    summary = {"repositories": len(groups), "commits": sum(len(v) for v in groups.values()), "failed": failed}
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
