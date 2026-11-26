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
        n["DetSixDetectedMedian"] = {"value": float(r6["detected_median"]), "digits": 0 if float(r6["detected_median"]).is_integer() else 1}
        n["DetSixDetectedShare"] = pct(r6["detected_share_of_candidates"])
        n["DetSixDeclaredRecall"] = pct(r6["declared_recall_in_candidates"])
    if "rq7" in a:
        r7 = a["rq7"]
        n["DetSevenRepos"] = r7["repos"]
        for key, tag in (("owner_change_DETECTED", "OwnerDetected"), ("owner_change_W", "OwnerW"), ("tvd_DETECTED", "TvdDetected")):
            mean, lo, hi = r7[key]
            f = r3 if key.startswith("tvd") else pct
            n[f"DetSeven{tag}"], n[f"DetSeven{tag}Lo"], n[f"DetSeven{tag}Hi"] = f(mean), f(lo), f(hi)
        n["DetSevenShareWithDetected"] = pct(r7["share_repos_with_detected"])
        n["DetSevenDetectedMedian"] = {"value": float(r7["detected_median"]), "digits": 0 if float(r7["detected_median"]).is_integer() else 1}
    # Descriptive counts and development facts from the other result files of the package.
    res = Path(src).resolve().parents[1]
    pools = json.loads((res / "pools/counts.json").read_text())
    n["DetPoolRepos"], n["DetPositives"], n["DetMergeRoot"] = pools["repos"], pools["positives"], pools["positives_merge_or_root"]
    n["DetUnlabeled"], n["DetDevRepos"], n["DetFinalRepos"] = pools["unlabeled"], pools["development_repos"], pools["final_repos"]
    m = json.loads((res / "matched/counts.json").read_text())
    n["DetMatchedCommits"], n["DetMatchedShort"] = m["matched"], m["short"]
    dev = json.loads((res / "develop-matched-2/summary.json").read_text())
    n["DetDevPositives"], n["DetDevMatchedRows"] = dev["positives"], dev["matched_rows"]
    n["DetDevRecall"] = pct(dev["dev_oof_recall_at_threshold"])
    for appr, v in dev["median_matched_pu_auc"].items():
        if appr in NAMES:
            n[f"DetDevMatched{NAMES[appr]}"] = r3(v)
    n["DetDevMatchedFull"] = r3(dev["chosen"]["median_matched_pu_auc"])
    n["DetDevLogregBest"] = r3(max(x["median_matched_pu_auc"] for x in dev["selection"] if x["kind"] == "logreg"))
    lk = json.loads((res / "leakage/leakage.json").read_text())
    n["DetShuffleMatched"] = r3(lk["shuffle_median_matched_pu_auc"])
    n["DetSharedCommits"] = lk["sha_overlap_dev_final"]
    f7 = json.loads((res / "rq7-frame/counts.json").read_text())
    n["DetSevenCandidates"], n["DetSevenChecked"] = f7["candidates"], f7["checked"]
    n["DetSevenExclCommits"], n["DetSevenExclContrib"] = f7["excluded_commits"], f7["excluded_contributors"]
    n["DetFinalPositives"], n["DetFinalMatchedRows"] = r5["positives"], r5["matched_rows"]
    # Exploratory analyses after the final evaluation (results/exploratory/), labeled DetX.
    x5 = res / "exploratory/rq5-checks/summary.json"
    if x5.exists():
        e = json.loads(x5.read_text())
        pp = e["positives_per_repo"]
        n["DetXPosMedian"], n["DetXPosQOne"], n["DetXPosQThree"], n["DetXPosMax"] = (
            {"value": pp["median"], "digits": 0}, {"value": pp["q1"], "digits": 0}, {"value": pp["q3"], "digits": 0}, pp["max"])
        n["DetXAtOneFull"], n["DetXAtOneKeyword"] = pct(e["share_at_one"]["full"]), pct(e["share_at_one"]["keyword"])
        for a, k in (("full", "Full"), ("keyword", "Keyword"), ("size_files", "SizeFiles")):
            n[f"DetXPooledAuc{k}"], n[f"DetXPooledAp{k}"] = r3(e["pooled"][a]["auc"]), r3(e["pooled"][a]["ap"])
        n["DetXQTenFull"], n["DetXQTenKeyword"] = r3(e["q10"]["full"]), r3(e["q10"]["keyword"])
        for key, tag in (("logreg", "Logreg"), ("no_shared_owner", "NoOwner"), ("no_bot_positives", "NoBot"),
                         ("no_refinement_commits", "NoRefine")):
            c = e[key]
            n[f"DetX{tag}Repos"], n[f"DetX{tag}MedianFull"], n[f"DetX{tag}MedianKeyword"] = c["repos"], r3(c["median_full"]), r3(c["median_keyword"])
            n[f"DetX{tag}Diff"], n[f"DetX{tag}R"], n[f"DetX{tag}P"] = r3(c["median_diff"]), r3(c["r"]), pval(c["p"])
        n["DetXSharedOwnerRepos"], n["DetXBotPositives"], n["DetXRefineCommits"] = (
            e["shared_owner_final_repos"], e["bot_positives"], e["refinement_commits_in_final"])
        for rate, tag in (("0.005", "Half"), ("0.02", "Two"), ("0.05", "Five")):
            n[f"DetXRecall{tag}"] = pct(e["recall_by_flag_rate"][rate]["recall"])
        c = e["content"]
        n["DetXFlagN"] = c["flagged_unlabeled"]["n"]
        for g, gt in (("flagged_unlabeled", "Flag"), ("declared", "Decl"), ("unlabeled", "Unl")):
            for m, mt in (("ws_only", "Ws"), ("alnum_all", "Alnum"), ("tok_high", "Tok"), ("keyword", "Kw"), ("any_indicator", "Any")):
                n[f"DetX{gt}{mt}"] = pct(c[g][m])
            n[f"DetX{gt}Lines"] = {"value": c[g]["median_lines"], "digits": 0}
        sh = e["shuffle"]
        for mode, mt in (("within_repo", "Within"), ("global", "Global")):
            for st, stt in (("mean", "Mean"), ("min", "Min"), ("max", "Max")):
                n[f"DetXShuffle{mt}{stt}"] = r3(sh[f"{mode}_{st}"])
        n["DetXSixFlagShare"], n["DetXSevenFlagShare"] = pct(e["rq6_flag_share_of_candidates_median"]), pct(e["rq7_flag_share_of_candidates_median"])
    x6 = res / "exploratory/rq6-lists/summary.json"
    if x6.exists():
        e = json.loads(x6.read_text())
        n["DetXListRepos"] = e["repos"]
        n["DetXSizeCutLines"] = int(round(e["size_cut_lines"]))
        for lst, lt in (("DETECTED", "Detected"), ("KEYWORD", "Keyword"), ("SIZE", "Size")):
            n[f"DetXListRecovery{lt}"] = pct(e["mean"][f"recovery_{lst}"])
            n[f"DetXListReassign{lt}"] = pct(e["mean"][f"reassign_{lst}"])
            n[f"DetXListFlagged{lt}"] = {"value": e["median_flagged"][lst], "digits": 0 if float(e["median_flagged"][lst]).is_integer() else 1}
            n[f"DetXListRecall{lt}"] = pct(e["recall_in_candidates"][lst])
            n[f"DetXListPrecision{lt}"] = pct(e["precision_vs_declared"][lst])
    # Integers of 1,000 or more use the thousands separator of the article.
    n = {k: ({"latex": f"{v:,}".replace(",", "{,}")} if type(v) is int and v >= 1000 else v) for k, v in n.items()}
    Path(dst).write_text(json.dumps(n, indent=1))
    print(f"{len(n)} values written to {dst}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
