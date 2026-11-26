"""Exploratory: paired tests of the detected list against the simple lists (results/exploratory/rq6-lists)."""

import json
import sys
from pathlib import Path

import pandas as pd

from analyze import holm, paired

src, dst = Path(sys.argv[1]), Path(sys.argv[2])
t = pd.read_csv(src)
rec = t.dropna(subset=["recovery_DETECTED"])
tests = {}
for b in ("KEYWORD", "SIZE"):
    tests[f"recovery_vs_{b}"] = paired(rec.recovery_DETECTED, rec[f"recovery_{b}"])
    tests[f"reassign_vs_{b}"] = paired(t.reassign_DETECTED, t[f"reassign_{b}"])
for k, a in zip(tests, holm([v["p"] for v in tests.values()])):
    tests[k]["p_holm"] = a
dst.write_text(json.dumps(tests, indent=2))
print(json.dumps(tests, indent=2))
