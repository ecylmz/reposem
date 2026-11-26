"""Suggest a .git-blame-ignore-revs file for a repository with the frozen detector.

Usage:
  python suggest.py REPO [--rev HEAD] [--paths PATH ...] [--model DIR] [--threshold T] > .git-blame-ignore-revs

The script scores every non-merge commit reachable from --rev (or only those that changed --paths) and
prints the commits at or above the threshold of the frozen model, one per line, each preceded by a comment
with its date, author, and subject. Review the list before you commit it: the detector predicts what
projects declare, and it can flag commits that the project would not declare.
"""

from __future__ import annotations

import argparse
import json
import pickle
import subprocess
import sys
from pathlib import Path

import numpy as np

from common import CONFIG, commit_features

HERE = Path(__file__).resolve().parent
DEFAULT_MODEL = HERE.parent / "results" / "develop-matched-2"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("repo", type=Path)
    ap.add_argument("--rev", default="HEAD")
    ap.add_argument("--paths", nargs="*", default=[])
    ap.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    ap.add_argument("--threshold", type=float, default=None, help="default: the frozen threshold (1%% flag rate)")
    args = ap.parse_args()
    with open(args.model / "models.pkl", "rb") as f:
        model = pickle.load(f)["full"]
    thr = args.threshold if args.threshold is not None else json.loads((args.model / "summary.json").read_text())["threshold"]
    cmd = [CONFIG["git"], "-C", str(args.repo), "log", "--no-merges", "--min-parents=1", "--format=%H", args.rev]
    if args.paths:
        cmd += ["--", *args.paths]
    shas = subprocess.run(cmd, capture_output=True, check=True, text=True).stdout.split()
    feats, keep, failed = [], [], 0
    for sha in shas:
        try:
            fx = commit_features(args.repo, sha)
        except Exception:  # noqa: BLE001 - a commit whose diff cannot be read is skipped and counted
            failed += 1
            continue
        feats.append([fx[c] for c in model["columns"]])
        keep.append(sha)
    scores = model["model"].predict_proba(np.array(feats, float))[:, 1] if feats else np.array([])
    flagged = [(s, sc) for s, sc in zip(keep, scores) if sc >= thr]
    print(f"# Suggested by the REPOSEM detector (threshold {thr:.3f}); {len(flagged)} of {len(keep)} commits flagged.")
    print("# Review each entry before you commit this file.")
    for sha, sc in reversed(flagged):  # oldest first
        meta = subprocess.run([CONFIG["git"], "-C", str(args.repo), "show", "-s", "--format=%as %an: %s", sha],
                              capture_output=True, text=True).stdout.strip()
        print(f"\n# {meta} (score {sc:.3f})\n{sha}")
    if failed:
        print(f"{failed} commits could not be read and were skipped.", file=sys.stderr)


if __name__ == "__main__":
    main()
