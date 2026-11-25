"""Frozen analysis of RQ5 to RQ7. Writes tidy CSV files and numbers.json to --out.

--role development runs the same code on development repositories to test the pipeline.
--role final runs the planned analysis on the final repositories (after the freeze).
"""

from __future__ import annotations

import argparse
import json
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.metrics import roc_auc_score

from common import CONFIG, MECHANICAL_GROUPS, ROOT, seed_for

B = CONFIG["bootstrap"]


def boot_ci(values, seed):
    v = np.asarray([x for x in values if np.isfinite(x)], float)
    rng = np.random.default_rng(seed)
    means = rng.choice(v, size=(B, len(v)), replace=True).mean(axis=1)
    return float(v.mean()), float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


def paired(a, b):
    """Two-sided Wilcoxon signed-rank test (zeros discarded), median difference, rank-biserial r."""
    d = np.asarray(a, float) - np.asarray(b, float)
    d = d[np.isfinite(d)]
    nz = d[d != 0]
    ranks = stats.rankdata(np.abs(nz))
    r = float((ranks[nz > 0].sum() - ranks[nz < 0].sum()) / ranks.sum()) if len(nz) else 0.0
    p = float(stats.wilcoxon(nz, zero_method="wilcox", alternative="two-sided").pvalue) if len(nz) else 1.0
    return {"n": int(len(d)), "median_diff": float(np.median(d)), "mean_diff": float(np.mean(d)), "r": r, "p": p}


def holm(ps):
    order = np.argsort(ps)
    adj = np.empty(len(ps))
    running = 0.0
    for k, i in enumerate(order):
        running = max(running, (len(ps) - k) * ps[i])
        adj[i] = min(1.0, running)
    return adj.tolist()


def rq5(features, split, models_dir, role):
    df = pd.read_csv(features)
    sp = pd.read_csv(split)
    df = df[df.repo.isin(set(sp.loc[sp.role == role, "repo"]))]
    failed = int((df.status != "ok").sum())
    df = df[df.status == "ok"].reset_index(drop=True)
    with open(Path(models_dir) / "models.pkl", "rb") as f:
        models = pickle.load(f)
    threshold = json.loads((Path(models_dir) / "summary.json").read_text())["threshold"]
    cat = pd.read_csv(ROOT / CONFIG["declared_csv"])[["repo", "sha", "category"]]
    df = df.merge(cat, on=["repo", "sha"], how="left")
    scores = {name: m["model"].predict_proba(df[m["columns"]].to_numpy(float))[:, 1] for name, m in models.items()}
    scores["keyword"] = df[[f"kw_{g}" for g in MECHANICAL_GROUPS]].max(axis=1).to_numpy(float)
    scores["whitespace_only"] = df["ws_only"].to_numpy(float)
    scores["random"] = np.random.default_rng(seed_for("random", role)).random(len(df))
    rows = []
    for name, s in scores.items():
        tmp = df[["repo", "label", "category"]].copy()
        tmp["s"] = s
        for repo, g in tmp.groupby("repo"):
            if g.label.nunique() == 2:
                rows.append({"repo": repo, "approach": name, "metric": "pu_auc", "value": roc_auc_score(g.label, g.s)})
                lay = g[(g.label == 0) | g.category.isin(["formatter", "whitespace"])]
                if lay.label.nunique() == 2:
                    rows.append({"repo": repo, "approach": name, "metric": "pu_auc_layout", "value": roc_auc_score(lay.label, lay.s)})
            if name == "full":
                rows.append({"repo": repo, "approach": name, "metric": "recall_at_threshold",
                             "value": float((g.s[g.label == 1] >= threshold).mean()) if (g.label == 1).any() else np.nan})
                rows.append({"repo": repo, "approach": name, "metric": "flagged_unlabeled",
                             "value": float((g.s[g.label == 0] >= threshold).mean())})
    long = pd.DataFrame(rows)
    long["role"] = role
    wide = long[long.metric == "pu_auc"].pivot(index="repo", columns="approach", values="value")
    tests = {}
    for base in ("keyword", "whitespace_only", "random"):
        tests[base] = paired(wide["full"], wide[base])
    adj = holm([tests[b]["p"] for b in tests])
    for b, a in zip(tests, adj):
        tests[b]["p_holm"] = a
    abl = {a: paired(wide["full"], wide[a]) for a in ("no_keywords", "no_content")}
    lay = long[long.metric == "pu_auc_layout"].pivot(index="repo", columns="approach", values="value")
    return long, {"repos": int(wide.shape[0]), "failed_commits": failed, "threshold": threshold,
                  "means": {a: boot_ci(wide[a], seed_for("ci", a)) for a in wide.columns},
                  "medians": {a: float(wide[a].median()) for a in wide.columns},
                  "tests": tests, "ablations": abl,
                  "layout_medians": {a: float(lay[a].median()) for a in lay.columns},
                  "layout_test_keyword": paired(lay["full"], lay["keyword"]),
                  "recall": boot_ci(long[long.metric == "recall_at_threshold"].value, seed_for("ci", "recall")),
                  "flagged": boot_ci(long[long.metric == "flagged_unlabeled"].value, seed_for("ci", "flag"))}


def load_lanes(d):
    recs = [json.loads(p.read_text()) for p in sorted(Path(d).glob("*.json"))]
    return recs


def rq6(lanes_dir):
    recs = load_lanes(lanes_dir)
    rows, status = [], {}
    for r in recs:
        status[r["status"]] = status.get(r["status"], 0) + 1
        if r["status"] != "OK":
            continue
        files = [f for f in r["samples"].get("touched", []) if f.get("status") == "OK"]
        re_n = sum(f["reattributed"] for f in files)
        unc = sum(f["uncaptured"] for f in files)
        row = {"repo": r["repo"], "files": len(files), "reattributed": re_n, "uncaptured": unc,
               "detected": r["detected"], "candidates": r["candidates"], "detected_declared": r["detected_declared"],
               "declared": r["declared"]}
        for lane in ("W", "WMC", "DETECTED"):
            row[f"recovery_{lane}"] = sum(f["recovered"][lane] for f in files) / re_n if re_n else np.nan
        for lane in ("W", "WMC", "DETECTED", "DECLARED"):
            row[f"reassign_{lane}"] = sum(f["reassigned"][lane] for f in files) / unc if unc else np.nan
        for lane in ("RAW", "W", "WMC", "DETECTED"):
            oc = [f["owner_change"][f"{lane}|DECLARED"] for f in files]
            row[f"owner_vs_decl_{lane}"] = float(np.mean(oc)) if oc else np.nan
        rnd = [f for f in r["samples"].get("random", []) if f.get("status") == "OK"]
        for lane in ("DECLARED", "DETECTED", "W"):
            oc = [f["owner_change"][f"{lane}|RAW"] for f in rnd]
            row[f"random_owner_vs_raw_{lane}"] = float(np.mean(oc)) if oc else np.nan
        rows.append(row)
    t = pd.DataFrame(rows)
    rec = t.dropna(subset=["recovery_DETECTED"])
    tests = {
        "recovery_vs_W": paired(rec.recovery_DETECTED, rec.recovery_W),
        "recovery_vs_WMC": paired(rec.recovery_DETECTED, rec.recovery_WMC),
        "reassign_vs_W": paired(t.reassign_DETECTED, t.reassign_W),
        "reassign_vs_WMC": paired(t.reassign_DETECTED, t.reassign_WMC),
    }
    adj = holm([v["p"] for v in tests.values()])
    for k, a in zip(tests, adj):
        tests[k]["p_holm"] = a
    summary = {"status": status, "repos": int(len(t)), "repos_with_reattributed": int(len(rec)),
               "means": {c: boot_ci(t[c], seed_for("ci6", c)) for c in t.columns if c.startswith(("recovery_", "reassign_", "owner_vs_decl_", "random_owner"))},
               "tests": tests,
               "detected_median": float(t.detected.median()), "detected_share_of_candidates": float((t.detected / t.candidates.replace(0, np.nan)).median()),
               "declared_recall_in_candidates": float((t.detected_declared.sum() / t.declared.sum()) if t.declared.sum() else np.nan)}
    return t, summary


def rq7(lanes_dir):
    recs = load_lanes(lanes_dir)
    rows, status = [], {}
    for r in recs:
        status[r["status"]] = status.get(r["status"], 0) + 1
        if r["status"] != "OK":
            continue
        rnd = [f for f in r["samples"].get("random", []) if f.get("status") == "OK"]
        if not rnd:
            continue
        rows.append({"repo": r["repo"], "files": len(rnd), "detected": r["detected"], "candidates": r["candidates"],
                     "ignore_file_at_head": r.get("ignore_file_at_head"),
                     "owner_change_DETECTED": float(np.mean([f["owner_change"]["DETECTED|RAW"] for f in rnd])),
                     "owner_change_W": float(np.mean([f["owner_change"]["W|RAW"] for f in rnd])),
                     "tvd_DETECTED": float(np.mean([f["tvd"]["DETECTED|RAW"] for f in rnd])),
                     "any_detected": int(r["detected"] > 0)})
    t = pd.DataFrame(rows)
    return t, {"status": status, "repos": int(len(t)),
               "owner_change_DETECTED": boot_ci(t.owner_change_DETECTED, seed_for("ci7", "det")),
               "owner_change_W": boot_ci(t.owner_change_W, seed_for("ci7", "w")),
               "tvd_DETECTED": boot_ci(t.tvd_DETECTED, seed_for("ci7", "tvd")),
               "share_repos_with_detected": float(t.any_detected.mean()),
               "detected_median": float(t.detected.median())}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--role", choices=["development", "final"], required=True)
    ap.add_argument("--features", required=True)
    ap.add_argument("--split", required=True)
    ap.add_argument("--models", required=True)
    ap.add_argument("--rq6", default="")
    ap.add_argument("--rq7", default="")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    long5, s5 = rq5(args.features, args.split, args.models, args.role)
    long5.to_csv(out / "rq5_repo.csv", index=False)
    result = {"role": args.role, "rq5": s5}
    if args.rq6:
        t6, s6 = rq6(args.rq6)
        t6.to_csv(out / "rq6_repo.csv", index=False)
        result["rq6"] = s6
    if args.rq7:
        t7, s7 = rq7(args.rq7)
        t7.to_csv(out / "rq7_repo.csv", index=False)
        result["rq7"] = s7
    (out / "analysis.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=1)[:6000])


if __name__ == "__main__":
    main()
