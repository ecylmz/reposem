"""Flatten analysis.json into numbers.json for make-numbers.py (one LaTeX macro per value).

Macro names start with "Det". Proportions are written as percentages with one decimal,
AUC values and effect sizes with three decimals, p-values as LaTeX fragments.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

NAMES = {"keyword": "Keyword", "size_lines": "SizeLines", "size_files": "SizeFiles",
         "whitespace_only": "Whitespace", "random": "Random", "full": "Full",
         "no_keywords": "NoKeywords", "no_content": "NoContent", "no_size": "NoSize"}


def letters(s):
    return "".join(w[:1].upper() + w[1:] for w in re.split(r"[^A-Za-z]+", s) if w)


def r3(v):
    return {"value": round(float(v), 6), "digits": 3}


def pct(v):
    return {"value": round(100 * float(v), 6), "digits": 1}


def pval(p):
    return {"latex": "$p < 0.001$" if p < 0.001 else f"$p = {p:.3f}$"}


def main(src, dst):
    a = json.loads(Path(src).read_text())
    n = {}
    r5 = a["rq5"]
    n["DetThreshold"] = r3(r5["threshold"])
    for metric, tag in (("matched_pu_auc", "Matched"), ("uniform_pu_auc", "Uniform"), ("matched_pu_auc_layout", "Layout")):
        m = r5[metric]
        n[f"Det{tag}Repos"] = m["repos"]
        for appr, v in m["medians"].items():
            n[f"Det{tag}Median{NAMES.get(appr, letters(appr))}"] = r3(v)
        for appr, t in {**m["tests"], **m["ablations"]}.items():
            k = NAMES.get(appr, letters(appr))
            n[f"Det{tag}Diff{k}"] = r3(t["median_diff"])
            n[f"Det{tag}R{k}"] = r3(t["r"])
            n[f"Det{tag}P{k}"] = pval(t.get("p_holm", t["p"]))
    for key, tag in (("recall", "Recall"), ("flagged", "Flagged")):
        mean, lo, hi = r5[key]
        n[f"Det{tag}"], n[f"Det{tag}Lo"], n[f"Det{tag}Hi"] = pct(mean), pct(lo), pct(hi)
    if "rq6" in a:
        r6 = a["rq6"]
        n["DetSixRepos"] = r6["repos"]
        n["DetSixReposReattributed"] = r6["repos_with_reattributed"]
        for col, (mean, lo, hi) in r6["means"].items():
            k = "DetSix" + letters(col.replace("RAW", "Raw").replace("WMC", "Wmc").replace("DECLARED", "Declared")
                                   .replace("DETECTED", "Detected"))
            n[k], n[k + "Lo"], n[k + "Hi"] = pct(mean), pct(lo), pct(hi)
        for t, v in r6["tests"].items():
            k = "DetSix" + letters(t.replace("WMC", "Wmc"))
            n[k + "Diff"] = pct(v["median_diff"])
            n[k + "MeanDiff"] = pct(v["mean_diff"])
            n[k + "R"] = r3(v["r"])
            n[k + "P"] = pval(v["p_holm"])
        n["DetSixDetectedMedian"] = r3(r6["detected_median"])
        n["DetSixDetectedShare"] = pct(r6["detected_share_of_candidates"])
        n["DetSixDeclaredRecall"] = pct(r6["declared_recall_in_candidates"])
    if "rq7" in a:
        r7 = a["rq7"]
        n["DetSevenRepos"] = r7["repos"]
        for key, tag in (("owner_change_DETECTED", "OwnerDetected"), ("owner_change_W", "OwnerW"), ("tvd_DETECTED", "TvdDetected")):
            mean, lo, hi = r7[key]
            n[f"DetSeven{tag}"], n[f"DetSeven{tag}Lo"], n[f"DetSeven{tag}Hi"] = pct(mean), pct(lo), pct(hi)
        n["DetSevenShareWithDetected"] = pct(r7["share_repos_with_detected"])
        n["DetSevenDetectedMedian"] = r3(r7["detected_median"])
    Path(dst).write_text(json.dumps(n, indent=1))
    print(f"{len(n)} values written to {dst}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
