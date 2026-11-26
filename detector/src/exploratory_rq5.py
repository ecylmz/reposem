"""Exploratory (after the final evaluation): checks of RQ5 that the mock reviews asked for.

All analyses use the frozen model and threshold. Output: --out/summary.json.
"""

from __future__ import annotations

import argparse
import csv
import json
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score

from analyze import paired
from common import CONFIG, MECHANICAL_GROUPS, ROOT, seed_for
from develop import FEATURE_SETS, make_model
from develop_matched import baselines, combined, oof, repo_measures


def final_frame(features, matched, split):
    df, pairs = combined(features, matched, split, "final")
    dev_df, _ = combined(features, matched, split, "development")
    seen = set(dev_df.sha)
    df = df[~df.sha.isin(seen)].reset_index(drop=True)
    pairs = pairs[~pairs.sha.isin(seen) & ~pairs.matched_to.isin(seen)]
    return df, pairs, dev_df


def matched_table(df, pairs, scores):
    rows = []
    for name, s in scores.items():
        rows += [r for r in repo_measures(df, pairs, s, name, "final") if r["metric"] == "matched_pu_auc"]
    return pd.DataFrame(rows).pivot(index="repo", columns="approach", values="value")


def pooled(df, pairs, s):
    m = set(zip(pairs.repo, pairs.sha))
    is_m = np.array([(r, x) in m for r, x in zip(df.repo, df.sha)])
    pos = df.label.to_numpy() == 1
    keep = pos | is_m
    y = pos[keep].astype(int)
    return float(roc_auc_score(y, s[keep])), float(average_precision_score(y, s[keep]))


def compare(w):
    t = paired(w["full"], w["keyword"])
    return {"repos": int(w["full"].notna().sum()), "median_full": float(w["full"].median()),
            "median_keyword": float(w["keyword"].median()), "median_diff": t["median_diff"], "r": t["r"], "p": t["p"]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--features", required=True)
    ap.add_argument("--matched-features", required=True)
    ap.add_argument("--split", required=True)
    ap.add_argument("--models", required=True)
    ap.add_argument("--rq6", required=True)
    ap.add_argument("--rq7", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    res = {}
    df, pairs, dev = final_frame(args.features, args.matched_features, args.split)
    with open(Path(args.models) / "models.pkl", "rb") as f:
        model = pickle.load(f)["full"]
    summ = json.loads((Path(args.models) / "summary.json").read_text())
    thr, kind, params = summ["threshold"], summ["chosen"]["kind"], summ["chosen"]["params"]
    s_full = model["model"].predict_proba(df[model["columns"]].to_numpy(float))[:, 1]
    base = baselines(df, seed_for("random", "final"))
    scores = {"full": s_full, **base}

    # 1. Ceiling: positives per repository, share of repositories at 1, pooled AUC and average precision.
    w = matched_table(df, pairs, scores)
    pos_per_repo = df[df.label == 1].groupby("repo").size()
    res["positives_per_repo"] = {"median": float(pos_per_repo.median()), "q1": float(pos_per_repo.quantile(.25)),
                                 "q3": float(pos_per_repo.quantile(.75)), "max": int(pos_per_repo.max())}
    res["share_at_one"] = {a: float((w[a] == 1).mean()) for a in ("full", "keyword", "size_files")}
    res["pooled"] = {a: dict(zip(("auc", "ap"), pooled(df, pairs, scores[a]))) for a in ("full", "keyword", "size_files", "size_lines")}
    res["q10"] = {a: float(w[a].quantile(.10)) for a in ("full", "keyword")}

    # 2. Logistic regression (best development setting) on the final repositories.
    best_lr = max((x for x in summ["selection"] if x["kind"] == "logreg"), key=lambda x: x["median_matched_pu_auc"])
    lr = make_model("logreg", best_lr["params"], seed_for("final-fit"))
    lr.fit(dev[FEATURE_SETS["full"]].to_numpy(float), dev.label.to_numpy(int))
    wl = matched_table(df, pairs, {"full": lr.predict_proba(df[FEATURE_SETS["full"]].to_numpy(float))[:, 1], "keyword": base["keyword"]})
    res["logreg"] = {"params": best_lr["params"], **compare(wl)}

    # 3. Subsets: no shared owner with a development repository; no bot positives; no refinement-sample commits.
    dev_owners = {r.split("/")[0] for r in dev.repo.unique()}
    keep = ~df.repo.str.split("/").str[0].isin(dev_owners)
    res["shared_owner_final_repos"] = int(df.loc[~keep, "repo"].nunique())
    sub = df[keep].reset_index(drop=True)
    res["no_shared_owner"] = compare(matched_table(sub, pairs[pairs.repo.isin(set(sub.repo))],
                                                   {"full": s_full[keep.to_numpy()], "keyword": base["keyword"][keep.to_numpy()]}))
    nob = ~((df.label == 1) & (df.bot == 1))
    res["bot_positives"] = int((~nob).sum())
    sub = df[nob].reset_index(drop=True)
    res["no_bot_positives"] = compare(matched_table(sub, pairs[pairs.matched_to.isin(set(sub.sha))],
                                                    {"full": s_full[nob.to_numpy()], "keyword": base["keyword"][nob.to_numpy()]}))
    refine = {r["sha"] for r in json.loads((ROOT / "data/category_sample.json").read_text())}
    nr = ~df.sha.isin(refine)
    res["refinement_commits_in_final"] = int((~nr).sum())
    sub = df[nr].reset_index(drop=True)
    res["no_refinement_commits"] = compare(matched_table(sub, pairs[pairs.matched_to.isin(set(sub.sha))],
                                                         {"full": s_full[nr.to_numpy()], "keyword": base["keyword"][nr.to_numpy()]}))

    # 4. Recall at other flag rates (thresholds from out-of-fold development scores, as in the design).
    s_dev = oof(dev, FEATURE_SETS["full"], kind, params)
    uni_dev = s_dev[(dev.uniform == 1).to_numpy() & (dev.label == 0).to_numpy()]
    res["recall_by_flag_rate"] = {}
    for rate in (0.005, 0.01, 0.02, 0.05):
        t = float(np.quantile(uni_dev, 1 - rate))
        g = df.assign(s=s_full)
        rec = g[g.label == 1].groupby("repo").s.apply(lambda v: float((v >= t).mean()))
        flg = g[(g.label == 0) & (g.uniform == 1)].groupby("repo").s.apply(lambda v: float((v >= t).mean()))
        res["recall_by_flag_rate"][str(rate)] = {"threshold": t, "recall": float(rec.mean()), "flagged": float(flg.mean())}

    # 5. Content indicators of flagged unlabeled commits (uniform pool) against declared and all unlabeled commits.
    g = df.assign(s=s_full)
    kw = g[[f"kw_{x}" for x in MECHANICAL_GROUPS]].max(axis=1)
    g = g.assign(kw=kw, alnum_all=(g.alnum_eq == 1).astype(int), tok_high=(g.tok_pres >= 0.9).astype(int))
    groups = {"flagged_unlabeled": g[(g.label == 0) & (g.uniform == 1) & (g.s >= thr)],
              "declared": g[g.label == 1], "unlabeled": g[(g.label == 0) & (g.uniform == 1)]}
    res["content"] = {k: {"n": int(len(v)), "ws_only": float(v.ws_only.mean()), "alnum_all": float(v.alnum_all.mean()),
                          "tok_high": float(v.tok_high.mean()), "keyword": float(v.kw.mean()),
                          "any_indicator": float(((v.ws_only == 1) | (v.alnum_all == 1) | (v.tok_high == 1) | (v.kw == 1)).mean()),
                          "median_lines": float(np.expm1(v.log_lines).median())}
                      for k, v in groups.items()}

    # 6. Label-shuffle null distribution: five seeds each, within repositories and across all rows.
    _, dev_pairs = combined(args.features, args.matched_features, args.split, "development")
    res["shuffle"] = {"within_repo": [], "global": []}
    for i in range(5):
        for mode in ("within_repo", "global"):
            rng = np.random.default_rng(seed_for("shuffle-null", mode, str(i)))
            sh = dev.copy()
            if mode == "global":
                sh["label"] = rng.permutation(sh.label.to_numpy())
            else:
                sh["label"] = sh.groupby("repo")["label"].transform(lambda v: rng.permutation(v.to_numpy()))
            s = oof(sh, FEATURE_SETS["full"], kind, params)
            r = repo_measures(dev, dev_pairs, s, "shuffled", "development")
            res["shuffle"][mode].append(float(np.median([x["value"] for x in r if x["metric"] == "matched_pu_auc"])))
    for mode in ("within_repo", "global"):
        v = res["shuffle"][mode]
        res["shuffle"][mode + "_mean"], res["shuffle"][mode + "_min"], res["shuffle"][mode + "_max"] = float(np.mean(v)), float(min(v)), float(max(v))

    # 7. Flag share among candidate commits in RQ6 and RQ7 (descriptive).
    for key, d in (("rq6", args.rq6), ("rq7", args.rq7)):
        recs = [json.loads(p.read_text()) for p in Path(d).glob("*__*.json")]
        sh_ = [r["detected"] / r["candidates"] for r in recs if r.get("status") == "OK" and r.get("candidates")]
        res[f"{key}_flag_share_of_candidates_median"] = float(np.median(sh_))
    (out / "summary.json").write_text(json.dumps(res, indent=2))
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
