"""Development run under decision D-005: size-matched PU-AUC is the selection measure.

Training data of each fold: positives, the uniformly drawn unlabeled commits, and the
size-matched unlabeled commits of the training repositories. Measures per repository:
size-matched PU-AUC (positives against their matched commits) and uniform PU-AUC.
The threshold flags 1% of the uniformly drawn unlabeled commits (out-of-fold).
"""

from __future__ import annotations

import argparse
import json
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import StratifiedGroupKFold

from common import CONFIG, FEATURES, MECHANICAL_GROUPS, seed_for
from develop import FEATURE_SETS, GRID, make_model

SIZE_FREE = [f for f in FEATURES if f not in ("log_files", "log_lines")]
FEATURE_SETS = {**FEATURE_SETS, "no_size": SIZE_FREE}


def combined(features, matched_features, split, role):
    u = pd.read_csv(features)
    m = pd.read_csv(matched_features)
    sp = pd.read_csv(split)
    keep = set(sp.loc[sp.role == role, "repo"])
    u, m = u[u.repo.isin(keep) & (u.status == "ok")], m[m.repo.isin(keep) & (m.status == "ok")]
    u = u.assign(uniform=1)
    m = m.assign(uniform=0)
    # One row per commit for training; a commit in both pools keeps uniform=1.
    allrows = pd.concat([u, m.drop(columns=["matched_to"])], ignore_index=True)
    allrows = allrows.sort_values("uniform", ascending=False).drop_duplicates(["repo", "sha"]).reset_index(drop=True)
    pairs = m[["repo", "sha", "matched_to"]]
    return allrows, pairs


def baselines(df, seed):
    rng = np.random.default_rng(seed)
    return {
        "keyword": df[[f"kw_{g}" for g in MECHANICAL_GROUPS]].max(axis=1).to_numpy(float),
        "size_lines": df["log_lines"].to_numpy(float),
        "size_files": df["log_files"].to_numpy(float),
        "whitespace_only": df["ws_only"].to_numpy(float),
        "random": rng.random(len(df)),
    }


def repo_measures(df, pairs, scores, approach, role):
    tmp = df[["repo", "sha", "label", "uniform"]].copy()
    tmp["s"] = scores
    rows = []
    matched_set = set(zip(pairs.repo, pairs.sha))
    for repo, g in tmp.groupby("repo"):
        pos = g[g.label == 1]
        mm = g[[(r, s) in matched_set for r, s in zip(g.repo, g.sha)]]
        if len(pos) and len(mm):
            y = np.r_[np.ones(len(pos)), np.zeros(len(mm))]
            rows.append({"repo": repo, "approach": approach, "role": role, "metric": "matched_pu_auc",
                         "value": roc_auc_score(y, np.r_[pos.s, mm.s])})
        uni = g[(g.label == 1) | (g.uniform == 1)]
        if uni.label.nunique() == 2:
            rows.append({"repo": repo, "approach": approach, "role": role, "metric": "uniform_pu_auc",
                         "value": roc_auc_score(uni.label, uni.s)})
    return rows


def oof(df, cols, kind, params):
    seed = seed_for("cv")
    cv = StratifiedGroupKFold(n_splits=CONFIG["cv_folds"], shuffle=True, random_state=seed % (2**31))
    out = np.zeros(len(df))
    X, y, groups = df[cols].to_numpy(float), df["label"].to_numpy(int), df["repo"].to_numpy()
    for tr, te in cv.split(X, y, groups):
        m = make_model(kind, params, seed)
        m.fit(X[tr], y[tr])
        out[te] = m.predict_proba(X[te])[:, 1]
    return out


def med(rows, approach, metric):
    v = [r["value"] for r in rows if r["approach"] == approach and r["metric"] == metric]
    return float(np.median(v)) if v else float("nan")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--features", required=True)
    ap.add_argument("--matched-features", required=True)
    ap.add_argument("--split", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    df, pairs = combined(args.features, args.matched_features, args.split, "development")
    rows = []
    for name, s in baselines(df, seed_for("random-dev")).items():
        rows += repo_measures(df, pairs, s, name, "development")
    selection, best = [], None
    for kind, grid in GRID.items():
        for params in grid:
            s = oof(df, FEATURE_SETS["full"], kind, params)
            label = f"{kind}:{json.dumps(params, sort_keys=True)}"
            r = repo_measures(df, pairs, s, label, "development")
            rows += r
            m = med(r, label, "matched_pu_auc")
            selection.append({"kind": kind, "params": params, "median_matched_pu_auc": m,
                              "median_uniform_pu_auc": med(r, label, "uniform_pu_auc")})
            if best is None or m > best[2]:
                best = (kind, params, m, s)
    kind, params, m, best_oof = best
    for name in ("no_keywords", "no_content", "no_size"):
        s = oof(df, FEATURE_SETS[name], kind, params)
        rows += repo_measures(df, pairs, s, f"ablation:{name}", "development")
    uni_unl = best_oof[(df.uniform == 1).to_numpy() & (df.label == 0).to_numpy()]
    threshold = float(np.quantile(uni_unl, 1 - CONFIG["flag_rate"]))
    recall = float((best_oof[(df.label == 1).to_numpy()] >= threshold).mean())
    models = {}
    for name, cols in FEATURE_SETS.items():
        mdl = make_model(kind, params, seed_for("final-fit"))
        mdl.fit(df[cols].to_numpy(float), df["label"].to_numpy(int))
        models[name] = {"model": mdl, "columns": cols}
    with open(out / "models.pkl", "wb") as f:
        pickle.dump(models, f)
    pd.DataFrame(rows).to_csv(out / "dev_repo_measures.csv", index=False)
    approaches = ["keyword", "size_lines", "size_files", "whitespace_only", "random",
                  "ablation:no_keywords", "ablation:no_content", "ablation:no_size"]
    summary = {
        "development_repos": int(df.repo.nunique()), "rows": int(len(df)), "positives": int(df.label.sum()),
        "matched_rows": int(len(pairs)), "chosen": {"kind": kind, "params": params, "median_matched_pu_auc": m},
        "selection": selection, "threshold": threshold, "flag_rate": CONFIG["flag_rate"],
        "dev_oof_recall_at_threshold": recall,
        "median_matched_pu_auc": {a: med(rows, a, "matched_pu_auc") for a in approaches},
        "median_uniform_pu_auc": {a: med(rows, a, "uniform_pu_auc") for a in approaches},
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps({k: v for k, v in summary.items() if k != "selection"}, indent=2))


if __name__ == "__main__":
    main()
