"""Phases 3 and 4 on development repositories only: baselines, model choice, ablations, threshold.

Reads the features run and the split. Never reads rows of final repositories.
Writes per-repository PU-AUC values (tidy CSV), the chosen configuration, the threshold,
and the fitted models to --out.
"""

from __future__ import annotations

import argparse
import json
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from common import CONFIG, CONTENT_FEATURES, FEATURES, KEYWORD_FEATURES, MECHANICAL_GROUPS, seed_for

GRID = {
    "logreg": [{"C": c} for c in (0.1, 1.0, 10.0)],
    "hgb": [{"learning_rate": lr, "max_leaf_nodes": m} for lr in (0.05, 0.1) for m in (15, 31)],
}
FEATURE_SETS = {
    "full": FEATURES,
    "no_keywords": [f for f in FEATURES if f not in KEYWORD_FEATURES],
    "no_content": [f for f in FEATURES if f not in CONTENT_FEATURES],
}


def make_model(kind: str, params: dict, seed: int):
    if kind == "logreg":
        return make_pipeline(StandardScaler(), LogisticRegression(C=params["C"], class_weight="balanced", max_iter=5000))
    return HistGradientBoostingClassifier(class_weight="balanced", random_state=seed, **params)


def load(features_csv: str, split_csv: str, role: str) -> pd.DataFrame:
    df = pd.read_csv(features_csv)
    split = pd.read_csv(split_csv)
    keep = set(split.loc[split.role == role, "repo"])
    df = df[df.repo.isin(keep)]
    return df


def baseline_scores(df: pd.DataFrame, seed: int) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    kw = df[[f"kw_{g}" for g in MECHANICAL_GROUPS]].max(axis=1).to_numpy(float)
    return {"keyword": kw, "whitespace_only": df["ws_only"].to_numpy(float), "random": rng.random(len(df))}


def per_repo_auc(df: pd.DataFrame, scores: np.ndarray, approach: str, role: str) -> list[dict]:
    rows = []
    tmp = df[["repo", "label"]].copy()
    tmp["score"] = scores
    for repo, g in tmp.groupby("repo"):
        if g.label.nunique() < 2:
            rows.append({"repo": repo, "approach": approach, "role": role, "metric": "pu_auc", "value": np.nan,
                         "status": "skipped", "reason": "one class only"})
            continue
        rows.append({"repo": repo, "approach": approach, "role": role, "metric": "pu_auc",
                     "value": roc_auc_score(g.label, g.score), "status": "ok", "reason": ""})
    return rows


def oof_scores(df: pd.DataFrame, cols: list[str], kind: str, params: dict) -> np.ndarray:
    seed = seed_for("cv")
    cv = StratifiedGroupKFold(n_splits=CONFIG["cv_folds"], shuffle=True, random_state=seed % (2**31))
    out = np.zeros(len(df))
    X, y, groups = df[cols].to_numpy(float), df["label"].to_numpy(int), df["repo"].to_numpy()
    for train, test in cv.split(X, y, groups):
        m = make_model(kind, params, seed)
        m.fit(X[train], y[train])
        out[test] = m.predict_proba(X[test])[:, 1]
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--features", required=True)
    ap.add_argument("--split", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    df = load(args.features, args.split, "development")
    failed = int((df.status != "ok").sum())
    df = df[df.status == "ok"].reset_index(drop=True)
    rows = []
    for name, s in baseline_scores(df, seed_for("random-dev")).items():
        rows += per_repo_auc(df, s, name, "development")

    # Model choice: median per-repository PU-AUC of out-of-fold scores, full feature set.
    selection = []
    best = None
    for kind, grid in GRID.items():
        for params in grid:
            s = oof_scores(df, FEATURE_SETS["full"], kind, params)
            r = per_repo_auc(df, s, f"{kind}:{json.dumps(params, sort_keys=True)}", "development")
            med = float(np.nanmedian([x["value"] for x in r]))
            selection.append({"kind": kind, "params": params, "median_pu_auc": med})
            rows += r
            if best is None or med > best[2]:
                best = (kind, params, med, s)
    kind, params, med, best_oof = best

    # Ablations with the chosen configuration.
    ablation_oof = {}
    for name in ("no_keywords", "no_content"):
        s = oof_scores(df, FEATURE_SETS[name], kind, params)
        ablation_oof[name] = s
        rows += per_repo_auc(df, s, f"ablation:{name}", "development")

    # Threshold: flag at most the configured share of unlabeled commits (out-of-fold scores).
    unl = best_oof[df.label.to_numpy() == 0]
    threshold = float(np.quantile(unl, 1 - CONFIG["flag_rate"]))
    pos = best_oof[df.label.to_numpy() == 1]
    dev_recall = float((pos >= threshold).mean())

    models = {}
    for name, cols in FEATURE_SETS.items():
        m = make_model(kind, params, seed_for("final-fit"))
        m.fit(df[cols].to_numpy(float), df["label"].to_numpy(int))
        models[name] = {"model": m, "columns": cols}
    with open(out / "models.pkl", "wb") as f:
        pickle.dump(models, f)
    pd.DataFrame(rows).to_csv(out / "dev_repo_auc.csv", index=False)
    summary = {
        "development_repos": int(df.repo.nunique()), "commits": int(len(df)), "failed_commits": failed,
        "positives": int(df.label.sum()), "chosen": {"kind": kind, "params": params, "median_pu_auc": med},
        "selection": selection, "threshold": threshold, "flag_rate": CONFIG["flag_rate"],
        "dev_oof_recall_at_threshold": dev_recall,
        "baseline_median_pu_auc": {b: float(pd.DataFrame(rows).query("approach == @b").value.median())
                                   for b in ("keyword", "whitespace_only", "random")},
        "ablation_median_pu_auc": {a: float(pd.DataFrame(rows).query("approach == @x", local_dict={"x": f"ablation:{a}"}).value.median())
                                   for a in ("no_keywords", "no_content")},
    }
    if kind == "logreg":
        lr = models["full"]["model"][-1]
        summary["coefficients"] = dict(zip(FEATURE_SETS["full"], map(float, lr.coef_[0])))
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps({k: v for k, v in summary.items() if k != "selection"}, indent=2))


if __name__ == "__main__":
    main()
