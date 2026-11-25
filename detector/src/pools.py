"""Build the commit pools and the repository split for RQ5 and RQ6.

Positives: declared commits that resolve on the default branch and are not merges.
Unlabeled: up to N other non-merge, non-root commits, drawn uniformly from the history
up to the analyzed revision. Writes pools.csv, split.csv, and counts.json to --out.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
from pathlib import Path

from common import CONFIG, ROOT, clone_dir, git, seed_for


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=0, help="first N repositories only (smoke test)")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    declared: dict[str, set[str]] = {}
    with open(ROOT / CONFIG["declared_csv"]) as f:
        for row in csv.DictReader(f):
            declared.setdefault(row["repo"], set()).add(row["sha"])
    repos = sorted(declared)
    if args.limit:
        repos = repos[: args.limit]

    # Repository split: seeded shuffle of the sorted list, 40% to final evaluation.
    rng = random.Random(seed_for("split"))
    order = repos[:] if args.limit else sorted(declared)
    rng.shuffle(order)
    n_final = round(len(order) * CONFIG["final_fraction"])
    final = set(order[:n_final])

    counts = {"repos": len(repos), "declared_rows": sum(len(declared[r]) for r in repos),
              "positives": 0, "positives_merge_or_root": 0, "unlabeled": 0, "failed": []}
    with open(out / "pools.csv", "w", newline="") as fp, open(out / "split.csv", "w", newline="") as fs:
        wp, ws = csv.writer(fp), csv.writer(fs)
        wp.writerow(["repo", "sha", "label"])
        ws.writerow(["repo", "role"])
        for repo in repos:
            ws.writerow([repo, "final" if repo in final else "development"])
            try:
                study = json.loads((ROOT / CONFIG["study_dir"] / (repo.replace("/", "__") + ".json")).read_text())
                head = study["head"]
                path = clone_dir(repo)
                lines = git(path, "rev-list", "--parents", head).decode().split("\n")
            except Exception as exc:  # noqa: BLE001 - record and continue
                counts["failed"].append([repo, repr(exc)[:200]])
                continue
            single_parent = [l.split()[0] for l in lines if len(l.split()) == 2]
            single = set(single_parent)
            pos = sorted(s for s in declared[repo] if s in single)
            counts["positives"] += len(pos)
            counts["positives_merge_or_root"] += len(declared[repo]) - len(pos)
            others = [s for s in single_parent if s not in declared[repo]]
            r = random.Random(seed_for("pool", repo))
            sample = r.sample(others, min(CONFIG["pool_unlabeled_per_repo"], len(others)))
            counts["unlabeled"] += len(sample)
            for s in pos:
                wp.writerow([repo, s, 1])
            for s in sorted(sample):
                wp.writerow([repo, s, 0])
    counts["final_repos"] = len([r for r in repos if r in final])
    counts["development_repos"] = len(repos) - counts["final_repos"]
    (out / "counts.json").write_text(json.dumps(counts, indent=2))
    print(json.dumps({k: v for k, v in counts.items() if k != "failed"}, indent=2), "failed:", len(counts["failed"]))


if __name__ == "__main__":
    main()
