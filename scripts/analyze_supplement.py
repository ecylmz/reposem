"""Analyze the supplementary measurements.

Usage: analyze_supplement.py <measurement_dir> <supplement_dir> <analysis_dir>

Adds results_supplement.json to the analysis directory.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyze_full import boot_ci, holm, paired_test, top_set  # noqa: E402

RNG = np.random.default_rng(11)


def shares(c):
    t = sum(c.values())
    return {k: v / t for k, v in c.items()} if t else {}


def hit_target(scores: dict, target: set) -> float:
    """Tie-aware Hit@1: expected hit when the top tied set is broken at random."""
    pos = {d: v for d, v in scores.items() if v > 0}
    top = top_set(pos)
    return sum(1 for d in top if d in target) / len(top) if top else 0.0


def ratio_ci(df, num, den):
    g = df.groupby("repo")[[num, den]].sum()
    g = g[g[den] > 0]
    return boot_ci(g[num] / g[den])


def two_stage(files: pd.DataFrame, col: str, b: int = 2000):
    """Repository-then-file bootstrap of the mean of repository means."""
    groups = [g[col].to_numpy(float) for _, g in files.groupby("repo")]
    est = float(np.mean([g.mean() for g in groups]))
    draws = []
    for _ in range(b):
        idx = RNG.integers(0, len(groups), len(groups))
        draws.append(np.mean([groups[i][RNG.integers(0, len(groups[i]), len(groups[i]))].mean() for i in idx]))
    return [est, float(np.percentile(draws, 2.5)), float(np.percentile(draws, 97.5))]


def main():
    study, supp, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    res = {}
    records = {json.loads(p.read_text())["full_name"]: json.loads(p.read_text()) for p in study.glob("*.json")}
    sups = {json.loads(p.read_text())["full_name"]: json.loads(p.read_text()) for p in supp.glob("*.json")}
    res["repos"] = len(sups)
    res["errors"] = {k: sum(1 for s in sups.values() if f"{k}_error" in s) for k in "abc"}

    # A: line-level recovery, spurious reassignment, declaration on top of -w, content check.
    a = pd.DataFrame([dict(row, repo=n) for n, s in sups.items() for row in s.get("a", [])])
    a.to_csv(out / "supp_lines.csv", index=False)
    res["A"] = {"files": int(len(a)), "repos": int(a.repo.nunique()),
                "reattributed_lines": int(a.reattributed.sum())}
    for lane in ("W", "WMC"):
        res["A"][f"{lane}_recovery"] = ratio_ci(a, f"{lane}_recovers", "reattributed")
        res["A"][f"{lane}_spurious"] = ratio_ci(a, f"{lane}_uncaptured_changed", f"{lane}_uncaptured")
    rep = a.groupby("repo")[["dw_tvd", "dw_flip"]].mean()
    res["A"]["declared_on_w_tvd"] = boot_ci(rep.dw_tvd)
    res["A"]["declared_on_w_flip"] = boot_ci(rep.dw_flip)
    res["A"]["reattributed_of_captured"] = ratio_ci(a, "reattributed", "captured")
    res["A"]["content_checked"] = int(a.content_checked.sum())
    res["A"]["content_ws_equal"] = ratio_ci(a, "content_ws_equal", "content_checked")
    res["A"]["content_alnum_equal"] = ratio_ci(a, "content_alnum_equal", "content_checked")

    # B: persistence of every sampled declared commit against a matched undeclared commit.
    b = pd.DataFrame([dict(row, repo=n) for n, s in sups.items() for row in s.get("b", [])])
    b = b[b.added > 0].copy()
    b["survival"] = (b.credited / b.added).clip(upper=1)
    b["visible"] = b.credited > 0
    m = b[b.match_added.fillna(0) > 0].copy()
    m["match_survival"] = (m.match_credited / m.match_added).clip(upper=1)
    b.to_csv(out / "supp_persistence.csv", index=False)
    res["B"] = {"commits": int(len(b)), "repos": int(b.repo.nunique()), "matched": int(len(m))}
    rb = b.groupby("repo")[["survival", "visible"]].mean()
    res["B"]["survival"] = boot_ci(rb.survival)
    res["B"]["visible"] = boot_ci(rb.visible)
    rm = m.groupby("repo")[["survival", "match_survival"]].mean()
    res["B"]["matched_survival_declared"] = boot_ci(rm.survival)
    res["B"]["matched_survival_undeclared"] = boot_ci(rm.match_survival)
    res["B"]["matched_test"] = paired_test(rm.survival, rm.match_survival)
    b["age_bin"] = pd.cut(b.age_years, [0, 1, 2, 3, 5, 100], labels=["<1", "1-2", "2-3", "3-5", ">5"], right=False)
    m["age_bin"] = pd.cut(m.age_years, [0, 1, 2, 3, 5, 100], labels=["<1", "1-2", "2-3", "3-5", ">5"], right=False)
    by = []
    for name in ["<1", "1-2", "2-3", "3-5", ">5"]:
        sm = m[m.age_bin == name]  # matched pairs only, for both bars
        d = boot_ci(sm.groupby("repo").survival.mean()) if len(sm) else [np.nan] * 3
        u = boot_ci(sm.groupby("repo").match_survival.mean()) if len(sm) else [np.nan] * 3
        by.append({"age_bin": name, "commits": int(len(sm)), "declared": d, "undeclared": u})
    res["B"]["by_age"] = by

    # C: prospective baselines, identity, bots, ignore-file timing, downstream association.
    rows = []
    for n, s in sups.items():
        c = s.get("c")
        if not c or not c[0]:
            continue
        crow, meta = c
        rec = records[n]
        by_hash = {f["path_hash"]: f for f in rec["past"] if f.get("status") == "OK"}
        names = {d: set(v) for d, v in meta["names"].items()}
        bots = set(meta["bots"])
        repo_top = top_set(meta["repo_prior"])
        head_added = rec["declaration"]["file_added_time"]
        for r in crow:
            f = by_hash.get(r["path_hash"])
            if f is None:
                continue
            dc = f["dev_counts"]
            future = f["future_excl_declared"]
            if not future:
                continue
            target = top_set(future)
            raw_o, decl_o = top_set(dc["RAW"]), top_set(dc["DECLARED"])
            row = {"repo": n, "path_hash": r["path_hash"], "flip": raw_o != decl_o, "fixes": r["fixes"], "lines": f["lines"],
                   "ignore_at_past": meta["ignore_file_at_past"]}
            for lane in ("RAW", "W", "WMC", "DECLARED"):
                row[f"hit_{lane}"] = hit_target(dc[lane], target)
                sh = shares(dc[lane])
                row[f"own_{lane}"] = max(sh.values(), default=np.nan)
                row[f"minor_{lane}"] = sum(1 for v in sh.values() if v < 0.05)
            row["hit_LAST"] = float(r["last"] in target) if r["last"] else 0.0
            row["hit_PRIOR"] = hit_target(r["prior"], target) if r["prior"] else 0.0
            row["has_prior"] = bool(r["prior"])
            row["hit_REPOTOP"] = sum(1 for d in repo_top if d in target) / len(repo_top) if repo_top else 0.0
            # Bots and aliases.
            row["raw_owner_bot"] = bool(raw_o & bots)
            row["decl_owner_bot"] = bool(decl_o & bots)
            row["alias"] = bool(raw_o != decl_o and any(names.get(x, set()) & names.get(y, set()) for x in raw_o for y in decl_o))
            fn = r["future_nobot"]
            if fn:
                tn = top_set(fn)
                row["hit_RAW_nobot"] = hit_target({d: v for d, v in dc["RAW"].items() if d not in bots}, tn)
                row["hit_DECLARED_nobot"] = hit_target({d: v for d, v in dc["DECLARED"].items() if d not in bots}, tn)
            else:
                row["hit_RAW_nobot"] = row["hit_DECLARED_nobot"] = np.nan
            rows.append(row)
    c = pd.DataFrame(rows)
    c.to_csv(out / "supp_prospective.csv", index=False)
    flip = c[c.flip]
    res["C"] = {"files": int(len(c)), "repos": int(c.repo.nunique()), "flip_files": int(len(flip)),
                "flip_repos": int(flip.repo.nunique())}
    methods = ["RAW", "W", "WMC", "DECLARED", "LAST", "PRIOR", "REPOTOP"]
    for subset, df in (("all", c), ("flip", flip)):
        rep = df.groupby("repo")[[f"hit_{k}" for k in methods]].mean()
        res["C"][subset] = {k: boot_ci(rep[f"hit_{k}"]) for k in methods}
        res["C"][subset]["RAW_vs_LAST"] = paired_test(rep.hit_RAW, rep.hit_LAST)
        res["C"][subset]["DECLARED_vs_LAST"] = paired_test(rep.hit_DECLARED, rep.hit_LAST)
        res["C"][subset]["RAW_vs_PRIOR"] = paired_test(rep.hit_RAW, rep.hit_PRIOR)
        res["C"][subset]["RAW_vs_DECLARED"] = paired_test(rep.hit_RAW, rep.hit_DECLARED)
        res["C"][subset]["REPOTOP_vs_RAW"] = paired_test(rep.hit_REPOTOP, rep.hit_RAW)
        keys = ["RAW_vs_LAST", "DECLARED_vs_LAST", "RAW_vs_PRIOR", "RAW_vs_DECLARED", "REPOTOP_vs_RAW"]
        adj = holm({k: res["C"][subset][k]["p"] for k in keys})
        for k in keys:
            res["C"][subset][k]["p_holm"] = adj.get(k, np.nan)
    # Mechanism on exactly these owner-change files.
    mech = pd.read_csv(out / "mechanism.csv").merge(flip[["repo", "path_hash"]], on=["repo", "path_hash"])
    mcols = ["raw_owner_declared_author", "decl_owner_declared_author", "raw_owner_active_before",
             "decl_owner_active_before", "raw_owner_active_after", "decl_owner_active_after"]
    mrep = mech.groupby("repo")[mcols].mean()
    res["C"]["mechanism"] = {k: boot_ci(mrep[k]) for k in mcols}
    res["C"]["mechanism"]["files"] = int(len(mech))
    res["C"]["mechanism"]["active_after_test"] = paired_test(mrep.raw_owner_active_after, mrep.decl_owner_active_after)
    res["C"]["mechanism"]["active_before_test"] = paired_test(mrep.raw_owner_active_before, mrep.decl_owner_active_before)
    res["C"]["mechanism"]["prior_commits_median_raw"] = float(mech.raw_owner_prior_commits.median())
    res["C"]["mechanism"]["prior_commits_median_decl"] = float(mech.decl_owner_prior_commits.median())
    res["C"]["flip_has_prior"] = float(flip.has_prior.mean())
    res["C"]["flip_raw_owner_bot"] = float(flip.raw_owner_bot.mean())
    res["C"]["flip_decl_owner_bot"] = float(flip.decl_owner_bot.mean())
    res["C"]["flip_alias"] = float(flip.alias.mean())
    sens = {}
    for label, df in (("no_bot_no_alias", flip[~flip.raw_owner_bot & ~flip.decl_owner_bot & ~flip.alias]),
                      ("ignore_file_at_snapshot", flip[flip.ignore_at_past])):
        rep = df.groupby("repo")[["hit_RAW", "hit_DECLARED"]].mean()
        sens[label] = {"files": int(len(df)), "repos": int(len(rep)), "RAW": boot_ci(rep.hit_RAW),
                       "DECLARED": boot_ci(rep.hit_DECLARED), "test": paired_test(rep.hit_RAW, rep.hit_DECLARED)}
    nb = flip.dropna(subset=["hit_RAW_nobot"])
    rep = nb.groupby("repo")[["hit_RAW_nobot", "hit_DECLARED_nobot"]].mean()
    sens["bot_commits_removed"] = {"files": int(len(nb)), "repos": int(len(rep)), "RAW": boot_ci(rep.hit_RAW_nobot),
                                   "DECLARED": boot_ci(rep.hit_DECLARED_nobot),
                                   "test": paired_test(rep.hit_RAW_nobot, rep.hit_DECLARED_nobot)}
    # Ledger without bulk commits (more than 20 files), on the same owner-change files.
    bulk_dir = study.parent / "bulk"
    brows = []
    for path in bulk_dir.glob("*.json"):
        bdata = json.loads(path.read_text())
        rec = records[bdata["full_name"]]
        by_hash = {f["path_hash"]: f for f in rec["past"] if f.get("status") == "OK"}
        for bf in bdata["files"]:
            f = by_hash.get(bf["path_hash"])
            if f is None or not bf["future_no_bulk"]:
                continue
            dc = f["dev_counts"]
            if top_set(dc["RAW"]) == top_set(dc["DECLARED"]):
                continue
            tgt = top_set(bf["future_no_bulk"])
            brows.append({"repo": bdata["full_name"], "RAW": hit_target(dc["RAW"], tgt), "DECLARED": hit_target(dc["DECLARED"], tgt)})
    if brows:
        bd = pd.DataFrame(brows)
        rep = bd.groupby("repo")[["RAW", "DECLARED"]].mean()
        sens["bulk_commits_removed"] = {"files": int(len(bd)), "repos": int(len(rep)), "RAW": boot_ci(rep.RAW),
                                        "DECLARED": boot_ci(rep.DECLARED), "test": paired_test(rep.RAW, rep.DECLARED)}
    sadj = holm({k: v["test"]["p"] for k, v in sens.items()})
    for k in sens:
        sens[k]["test"]["p_holm"] = sadj.get(k, np.nan)
    res["C"]["sensitivity"] = sens
    res["C"]["share_repos_ignore_at_snapshot"] = float(c.groupby("repo").ignore_at_past.first().mean())

    # Downstream association: ownership and minor contributors vs later bug-fix commits,
    # with ranks taken within each repository.
    def within_rank(df, col):
        return df.groupby("repo")[col].rank(pct=True)

    dd = c.dropna(subset=["own_RAW", "own_DECLARED"]).copy()
    for col in ["fixes"] + [f"own_{l}" for l in ("RAW", "DECLARED")] + [f"minor_{l}" for l in ("RAW", "DECLARED")]:
        dd[col + "_r"] = within_rank(dd, col)
    dd = dd[dd.groupby("repo").fixes.transform("nunique") > 1]
    repos = dd.repo.unique()

    def corr(df, x):
        return stats.spearmanr(df[x + "_r"], df["fixes_r"]).statistic

    down = {"files": int(len(dd)), "repos": int(len(repos))}
    for x in ("own", "minor"):
        est = {l: corr(dd, f"{x}_{l}") for l in ("RAW", "DECLARED")}
        draws = []
        for _ in range(1000):
            pick = RNG.choice(repos, len(repos))
            sub = pd.concat([dd[dd.repo == r] for r in pick])
            draws.append([corr(sub, f"{x}_RAW"), corr(sub, f"{x}_DECLARED")])
        draws = np.array(draws)
        down[x] = {"RAW": [est["RAW"], *np.percentile(draws[:, 0], [2.5, 97.5])],
                   "DECLARED": [est["DECLARED"], *np.percentile(draws[:, 1], [2.5, 97.5])],
                   "diff": [est["DECLARED"] - est["RAW"], *np.percentile(draws[:, 1] - draws[:, 0], [2.5, 97.5])]}
    res["C"]["downstream"] = down

    # RQ2 robustness: two-stage bootstrap, file weighting, minimum file size.
    files = pd.read_csv(out / "files.csv")
    t = files[files["sample"] == "touched"]
    r = files[files["sample"] == "random"]
    rob = {}
    for label, df in (("touched", t), ("random", r)):
        rob[label] = {
            "two_stage_flip": two_stage(df, "raw_flip"),
            "two_stage_major": two_stage(df, "raw_major_change"),
            "file_weighted_flip": float(df.raw_flip.mean()),
            "file_weighted_major": float(df.raw_major_change.mean()),
        }
        big = df[df.lines >= 100]
        rep = big.groupby("repo")[["raw_flip", "raw_major_change", "raw_tvd"]].mean()
        rob[label]["min100_files"] = int(len(big))
        rob[label]["min100_flip"] = boot_ci(rep.raw_flip)
        rob[label]["min100_major"] = boot_ci(rep.raw_major_change)
        rob[label]["min100_tvd"] = boot_ci(rep.raw_tvd)
    # Proportions of repositories where W / WMC are closer, equal, or farther than RAW (touched).
    rt = pd.read_csv(out / "repo_touched.csv", index_col=0)
    comp = {}
    for lane in ("w", "wmc"):
        for m in ("tvd", "dis", "flip"):
            d = rt[f"{lane}_{m}"] - rt[f"raw_{m}"]
            comp[f"{lane}_{m}"] = {"closer": float((d < -1e-12).mean()), "equal": float((d.abs() <= 1e-12).mean()),
                                   "farther": float((d > 1e-12).mean()), "median_diff": float(d.median())}
    rob["heuristic_direction"] = comp
    res["robustness"] = rob
    (out / "results_supplement.json").write_text(json.dumps(res, indent=1, default=float))
    print(json.dumps(res, indent=1, default=float)[:5000])


if __name__ == "__main__":
    main()
