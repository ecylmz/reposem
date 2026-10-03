"""Additional analyses: conditional model for RQ4, controlled ownership--fix
association, counterparts of inactive entries, adoption, effect context,
alias-adjusted owner changes, threshold sensitivity, snapshot-list sensitivity,
and per-age persistence differences.

Usage: analyze_round3.py <measurement_dir> <supplement_dir> <analysis_dir>
"""

from __future__ import annotations

import datetime
import json
import math
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
from statsmodels.discrete.conditional_models import ConditionalLogit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyze_full import boot_ci, paired_test, shares, top_set  # noqa: E402
from measure import CLONES  # noqa: E402

RNG = np.random.default_rng(3)


def gh(path):
    r = subprocess.run(["gh", "api", path], capture_output=True, text=True)
    return json.loads(r.stdout) if r.returncode == 0 else None


def git(repo, *args):
    r = subprocess.run(["/opt/homebrew/bin/git", "-C", str(repo), *args], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def main():
    study, supp, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    res = {}
    records = {}
    for p in study.glob("*.json"):
        r = json.loads(p.read_text())
        records[r["full_name"]] = r
    sups = {}
    for p in supp.glob("*.json"):
        s = json.loads(p.read_text())
        sups[s["full_name"]] = s

    # ---------------------------------------------------------------- RQ4 model
    rows = []
    flip_repo_top = []
    for n, s in sups.items():
        c = s.get("c")
        if not c or not c[0]:
            continue
        crow, meta = c
        rec = records[n]
        by_hash = {f["path_hash"]: f for f in rec["past"] if f.get("status") == "OK"}
        repo_prior = meta["repo_prior"]
        repo_top = top_set(repo_prior)
        for r in crow:
            f = by_hash.get(r["path_hash"])
            if f is None or not f["future_excl_declared"]:
                continue
            dc = f["dev_counts"]
            target = top_set(f["future_excl_declared"])
            raw_o, decl_o = top_set(dc["RAW"]), top_set(dc["DECLARED"])
            flip = raw_o != decl_o
            if flip:
                flip_repo_top.append(float(bool(raw_o & repo_top)))
            cands = set(dc["RAW"]) | set(dc["DECLARED"]) | set(r["prior"]) | ({r["last"]} if r["last"] else set())
            ys = [d in target for d in cands]
            if not any(ys) or all(ys):
                continue
            sr, sd = shares(dc["RAW"]), shares(dc["DECLARED"])
            gid = f"{n}|{r['path_hash']}"
            for d in cands:
                rows.append({"g": gid, "repo": n, "flip": flip, "y": int(d in target),
                             "raw_owner": int(d in raw_o), "decl_owner": int(d in decl_o),
                             "raw_share": sr.get(d, 0.0), "decl_share": sd.get(d, 0.0),
                             "file_prior": math.log1p(r["prior"].get(d, 0)),
                             "repo_prior": math.log1p(repo_prior.get(d, 0)),
                             "last": int(d == r["last"])})
    cand = pd.DataFrame(rows)
    cand.to_csv(out / "r3_candidates.csv", index=False)

    def fit(df, cols):
        model = ConditionalLogit(df["y"], df[cols], groups=df["g"])
        return model.fit(disp=False)

    base = ["file_prior", "repo_prior", "last"]
    model_res = {}
    for subset, df in (("all", cand), ("flip", cand[cand.flip])):
        block = {"files": int(df.g.nunique()), "candidates": int(len(df))}
        for name, cols in (("raw", base + ["raw_owner"]), ("decl", base + ["decl_owner"]), ("both", base + ["raw_owner", "decl_owner"])):
            fr = fit(df, cols)
            block[name] = {c: {"or": float(np.exp(fr.params[c])), "lo": float(np.exp(fr.conf_int().loc[c, 0])),
                               "hi": float(np.exp(fr.conf_int().loc[c, 1])), "p": float(fr.pvalues[c])} for c in cols}
            block[name]["llf"] = float(fr.llf)
        # Repository-cluster bootstrap of the owner coefficients in the joint model.
        groups = {k: v for k, v in df.groupby("repo")}
        repos = list(groups)
        draws = []
        for _ in range(200):
            pick = RNG.choice(repos, len(repos))
            parts = []
            for i, r in enumerate(pick):
                g = groups[r].copy()
                g["g"] = g["g"] + f"#{i}"
                parts.append(g)
            try:
                fr = fit(pd.concat(parts), base + ["raw_owner", "decl_owner"])
                draws.append([fr.params["raw_owner"], fr.params["decl_owner"]])
            except Exception:  # noqa: BLE001
                continue
        draws = np.exp(np.array(draws))
        block["both_cluster_ci"] = {"raw_owner": list(np.percentile(draws[:, 0], [2.5, 97.5])),
                                    "decl_owner": list(np.percentile(draws[:, 1], [2.5, 97.5]))}
        model_res[subset] = block
    res["rq4_model"] = model_res
    res["flip_raw_owner_is_repo_top"] = float(np.mean(flip_repo_top))

    # ------------------------------------------- controlled ownership--fix association
    sp = pd.read_csv(out / "supp_prospective.csv")
    extra = []
    for n, s in sups.items():
        c = s.get("c")
        if not c or not c[0]:
            continue
        by_hash = {f["path_hash"]: f for f in records[n]["past"] if f.get("status") == "OK"}
        for r in c[0]:
            f = by_hash.get(r["path_hash"])
            if f is None:
                continue
            extra.append({"repo": n, "path_hash": r["path_hash"], "prior_commits": sum(r["prior"].values()),
                          "later_commits": sum(f["future_excl_declared"].values())})
    dd = sp.merge(pd.DataFrame(extra), on=["repo", "path_hash"]).dropna(subset=["own_RAW", "own_DECLARED"])
    dd["later_nonfix"] = dd.later_commits - dd.fixes
    dd = dd[dd.groupby("repo").fixes.transform("nunique") > 1].copy()
    cols = ["fixes", "own_RAW", "own_DECLARED", "minor_RAW", "minor_DECLARED", "lines", "prior_commits", "later_nonfix"]
    for col in cols:
        dd[col + "_r"] = dd.groupby("repo")[col].rank(pct=True)
    controls = ["lines_r", "prior_commits_r", "later_nonfix_r"]

    def partial(df, x):
        X = np.column_stack([np.ones(len(df))] + [df[c] for c in controls])
        rx = df[x] - X @ np.linalg.lstsq(X, df[x], rcond=None)[0]
        ry = df["fixes_r"] - X @ np.linalg.lstsq(X, df["fixes_r"], rcond=None)[0]
        return float(np.corrcoef(rx, ry)[0, 1])

    def per_repo_mean(df, x):
        vals = []
        for _, g in df.groupby("repo"):
            if len(g) >= 5 and g[x].nunique() > 1:
                vals.append(g[[x, "fixes"]].corr(method="spearman").iloc[0, 1])
        return float(np.nanmean(vals))

    groups = {k: v for k, v in dd.groupby("repo")}
    repos = list(groups)
    down = {"files": int(len(dd)), "repos": int(len(repos))}
    for x in ("own", "minor"):
        est = {l: partial(dd, f"{x}_{l}_r") for l in ("RAW", "DECLARED")}
        draws = []
        for _ in range(1000):
            sub = pd.concat([groups[r] for r in RNG.choice(repos, len(repos))])
            draws.append([partial(sub, f"{x}_RAW_r"), partial(sub, f"{x}_DECLARED_r")])
        draws = np.array(draws)
        down[x] = {"RAW": [est["RAW"], *np.percentile(draws[:, 0], [2.5, 97.5])],
                   "DECLARED": [est["DECLARED"], *np.percentile(draws[:, 1], [2.5, 97.5])],
                   "diff": [est["DECLARED"] - est["RAW"], *np.percentile(draws[:, 1] - draws[:, 0], [2.5, 97.5])],
                   "per_repo_mean_RAW": per_repo_mean(dd, f"{x}_RAW"),
                   "per_repo_mean_DECLARED": per_repo_mean(dd, f"{x}_DECLARED")}
    res["downstream_controlled"] = down

    # ------------------------------------------------ counterparts of inactive entries
    un = pd.read_csv(out / "unresolved.csv")
    pr = un[un.kind == "PR_COMMIT_SQUASHED_OR_REBASED"]
    cp = Counter()
    for _, row in pr.iterrows():
        rec = records[row.repo]
        declared = {m["sha"] for m in rec.get("declared_meta", [])}
        pulls = gh(f"repos/{row.repo}/commits/{row.entry}/pulls") or []
        merged = [p for p in pulls if p.get("merged_at")]
        if not merged:
            cp["no_pr"] += 1
            continue
        pull = gh(f"repos/{row.repo}/pulls/{merged[0]['number']}") or {}
        sha = pull.get("merge_commit_sha")
        base = (pull.get("base") or {}).get("ref")
        if not sha:
            cp["no_merge_sha"] += 1
        elif sha in declared:
            cp["counterpart_declared"] += 1
        elif base != rec.get("default_branch", base) and False:
            cp["other_base"] += 1
        else:
            cp["counterpart_undeclared"] += 1
    res["pr_counterparts"] = dict(cp)

    # ----------------------------------------------------------- adoption context
    frame = [json.loads(l) for l in (study.parent / "frame_repos.jsonl").open()]
    presence = {json.loads(l)["full_name"]: json.loads(l) for l in (study.parent / "frame_presence.jsonl").open()}
    fr = pd.DataFrame([{"stars": r["stars"], "has": bool(presence.get(r["full_name"], {}).get("has_file"))}
                       for r in frame if r["stars"] >= 5000 and r["full_name"] in presence])
    fr["bucket"] = pd.cut(fr.stars, [5000, 10000, 20000, 50000, 10**7], right=False,
                          labels=["5k-10k", "10k-20k", "20k-50k", ">=50k"])
    res["adoption_by_stars"] = fr.groupby("bucket", observed=False).has.agg(["mean", "size"]).reset_index().astype({"bucket": str}).to_dict(orient="records")
    years = Counter()
    for r in records.values():
        t = r.get("declaration", {}).get("file_added_time")
        if t:
            years[datetime.datetime.fromtimestamp(t, datetime.timezone.utc).year] += 1
    res["adoption_by_year"] = dict(sorted(years.items()))

    # ----------------------------------------------- effect context and robustness
    def owner_change(dc, a, b):
        return float(top_set(dc[a]) != top_set(dc[b]))

    def major(dc, lane, thr):
        return sum(1 for v in shares(dc[lane]).values() if v >= thr)

    ctx_rows = []
    for n, r in records.items():
        if r.get("status") != "OK":
            continue
        names = None
        s = sups.get(n, {})
        if s.get("c") and s["c"][1]:
            names = {d: set(v) for d, v in s["c"][1]["names"].items()}
        for sample in ("touched", "random"):
            for f in r.get(sample, []):
                if f.get("status") != "OK":
                    continue
                dc = f["dev_counts"]
                raw_o, decl_o = top_set(dc["RAW"]), top_set(dc["DECLARED"])
                flip = raw_o != decl_o
                alias = None
                if names is not None:
                    alias = bool(flip and any(names.get(x, set()) & names.get(y, set()) for x in raw_o for y in decl_o))
                ctx_rows.append({"repo": n, "sample": sample,
                                 "raw_decl": float(flip), "raw_w": owner_change(dc, "RAW", "W"),
                                 "raw_wmc": owner_change(dc, "RAW", "WMC"),
                                 "has_names": names is not None,
                                 "raw_decl_noalias": float(flip and not alias) if alias is not None else np.nan,
                                 "major10": float(major(dc, "RAW", 0.10) != major(dc, "DECLARED", 0.10))})
    ctx = pd.DataFrame(ctx_rows)
    res["context"] = {}
    for sample in ("touched", "random"):
        sub = ctx[ctx["sample"] == sample]
        rep = sub.groupby("repo")[["raw_decl", "raw_w", "raw_wmc", "major10"]].mean()
        block = {k: boot_ci(rep[k]) for k in rep.columns}
        named = sub[sub.has_names]
        rn = named.groupby("repo")[["raw_decl", "raw_decl_noalias"]].mean()
        block["named_repos"] = int(len(rn))
        block["raw_decl_named"] = boot_ci(rn.raw_decl)
        block["raw_decl_noalias"] = boot_ci(rn.raw_decl_noalias)
        res["context"][sample] = block

    # -------------------------------------- snapshot-list sensitivity (RQ4)
    keep = set()
    for n, r in records.items():
        if r.get("status") != "OK" or not r.get("past_commit"):
            continue
        repo = CLONES / n.replace("/", "__")
        text = git(repo, "show", f"{r['past_commit']}:.git-blame-ignore-revs")
        if not text:
            continue
        listed = set()
        for line in text.splitlines():
            e = line.split("#", 1)[0].strip()
            if re.fullmatch(r"[0-9a-fA-F]{7,40}", e):
                full = git(repo, "rev-parse", "--verify", "--quiet", e + "^{commit}").strip()
                if full:
                    listed.add(full)
        past_declared = {m["sha"] for m in r["declared_meta"]
                         if subprocess.run(["/opt/homebrew/bin/git", "-C", str(repo), "merge-base", "--is-ancestor", m["sha"], r["past_commit"]]).returncode == 0}
        if past_declared and past_declared <= listed:
            keep.add(n)
    flip = sp[sp.flip & sp.repo.isin(keep)]
    rep = flip.groupby("repo")[["hit_RAW", "hit_DECLARED"]].mean()
    res["snapshot_list"] = {"repos_kept": len(keep), "files": int(len(flip)), "repos": int(len(rep)),
                            "RAW": boot_ci(rep.hit_RAW), "DECLARED": boot_ci(rep.hit_DECLARED),
                            "test": paired_test(rep.hit_RAW, rep.hit_DECLARED)}

    # ------------------------------------------------- persistence by age
    b = pd.read_csv(out / "supp_persistence.csv")
    b = b[(b.added > 0) & (b.match_added.fillna(0) > 0)].copy()
    b["s"] = (b.credited / b.added).clip(upper=1)
    b["m"] = (b.match_credited / b.match_added).clip(upper=1)
    b["bin"] = pd.cut(b.age_years, [0, 1, 2, 3, 5, 100], labels=["<1", "1-2", "2-3", "3-5", ">5"], right=False)
    per = []
    for name, g in b.groupby("bin", observed=False):
        rep = g.groupby("repo")[["s", "m"]].mean()
        t = paired_test(rep.s, rep.m)
        per.append({"bin": str(name), "pairs": int(len(g)), "repos": int(len(rep)), "diff": t["mean_diff"], "ci": t["mean_diff_ci"]})
    res["persistence_by_age_diff"] = per

    (out / "results_round3.json").write_text(json.dumps(res, indent=1, default=float))
    print(json.dumps(res, indent=1, default=float)[:6000])


if __name__ == "__main__":
    main()
