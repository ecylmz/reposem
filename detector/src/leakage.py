"""Leakage checks on the development data (D-005 design).

1. Label shuffle: shuffle labels within each repository, rerun the out-of-fold scoring with the
   chosen model, and report the median size-matched PU-AUC (expected near 0.5).
2. Permutation importance: out-of-fold drop in pooled size-matched AUC when one feature is permuted.
3. Overlap: commit ids that occur in both development and final repositories (forks or shared history),
   counted only, with no labels or scores of final repositories read.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import StratifiedGroupKFold

from common import CONFIG, seed_for
from develop import FEATURE_SETS, make_model
from develop_matched import combined, oof, repo_measures


def pooled_matched_auc(df, pairs, s):
    matched = set(zip(pairs.repo, pairs.sha))
    is_m = np.array([(r, x) in matched for r, x in zip(df.repo, df.sha)])
    pos = df.label.to_numpy() == 1
    keep = pos | is_m
    return float(roc_auc_score(pos[keep].astype(int), s[keep]))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--features", required=True)
    ap.add_argument("--matched-features", required=True)
    ap.add_argument("--split", required=True)
    ap.add_argument("--develop", required=True, help="results folder of develop_matched.py")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    chosen = json.loads((Path(args.develop) / "summary.json").read_text())["chosen"]
    kind, params = chosen["kind"], chosen["params"]
    df, pairs = combined(args.features, args.matched_features, args.split, "development")
    cols = FEATURE_SETS["full"]
    res = {"chosen": chosen}

    rng = np.random.default_rng(seed_for("shuffle"))
    shuf = df.copy()
    shuf["label"] = shuf.groupby("repo")["label"].transform(lambda v: rng.permutation(v.to_numpy()))
    s = oof(shuf, cols, kind, params)
    # Measure against the true labels and pairs: a model trained on shuffled labels has no signal.
    r = repo_measures(df, pairs, s, "shuffled", "development")
    res["shuffle_median_matched_pu_auc"] = float(np.median([x["value"] for x in r if x["metric"] == "matched_pu_auc"]))
    res["shuffle_median_uniform_pu_auc"] = float(np.median([x["value"] for x in r if x["metric"] == "uniform_pu_auc"]))

    base = oof(df, cols, kind, params)
    res["pooled_matched_auc"] = pooled_matched_auc(df, pairs, base)
    seed = seed_for("cv")
    cv = StratifiedGroupKFold(n_splits=CONFIG["cv_folds"], shuffle=True, random_state=seed % (2**31))
    X, y, g = df[cols].to_numpy(float), df.label.to_numpy(int), df.repo.to_numpy()
    folds = list(cv.split(X, y, g))
    fitted = []
    for tr, te in folds:
        m = make_model(kind, params, seed)
        m.fit(X[tr], y[tr])
        fitted.append((m, te))
    imp = {}
    prng = np.random.default_rng(seed_for("perm"))
    for j, c in enumerate(cols):
        s = np.zeros(len(df))
        for m, te in fitted:
            Xp = X[te].copy()
            Xp[:, j] = prng.permutation(Xp[:, j])
            s[te] = m.predict_proba(Xp)[:, 1]
        imp[c] = res["pooled_matched_auc"] - pooled_matched_auc(df, pairs, s)
    res["permutation_importance"] = dict(sorted(imp.items(), key=lambda kv: -kv[1]))

    sp = pd.read_csv(args.split)
    allf = pd.concat([pd.read_csv(args.features, usecols=["repo", "sha"]),
                      pd.read_csv(args.matched_features, usecols=["repo", "sha"])]).merge(sp, on="repo")
    dev = set(allf.loc[allf.role == "development", "sha"])
    fin = set(allf.loc[allf.role == "final", "sha"])
    res["sha_overlap_dev_final"] = len(dev & fin)
    (out / "leakage.json").write_text(json.dumps(res, indent=2))
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
