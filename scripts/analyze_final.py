"""Final additional analyses: effect magnitudes, source-file scope, RQ4 baselines
under the same filters, later-commit distribution, partial correlation without
the later-activity control, and the declared-set / mapping variants.

Usage: analyze_final.py <measurement_dir> <analysis_dir>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyze_full import boot_ci, file_row, holm, paired_test, top_set  # noqa: E402
from analyze_supplement import hit_target  # noqa: E402

SOURCE = {".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh", ".hxx", ".cs", ".java", ".kt", ".kts", ".scala", ".go",
          ".rs", ".py", ".pyi", ".pyx", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".rb", ".php", ".swift", ".m",
          ".mm", ".dart", ".lua", ".pl", ".pm", ".sh", ".bash", ".zsh", ".r", ".jl", ".ex", ".exs", ".erl", ".hs",
          ".ml", ".mli", ".fs", ".fsx", ".clj", ".cljs", ".groovy", ".vue", ".svelte", ".sql", ".zig", ".nim",
          ".v", ".sv", ".vhd", ".f90", ".f", ".asm", ".s", ".ps1", ".el", ".vim", ".cu", ".cuh", ".proto"}

RNG = np.random.default_rng(5)


def main():
    study, out = Path(sys.argv[1]), Path(sys.argv[2])
    data = study.parent
    records = {}
    for p in study.glob("*.json"):
        r = json.loads(p.read_text())
        records[r["full_name"]] = r
    res = {}

    # Effect magnitudes and source-file scope.
    rows = []
    for n, r in records.items():
        if r.get("status") != "OK":
            continue
        for sample in ("touched", "random"):
            for f in r.get(sample, []):
                row = file_row(n, sample, f)
                if row:
                    row["source"] = f.get("ext", "") in SOURCE
                    rows.append(row)
    files = pd.DataFrame(rows)
    files["owner_abs_gt10"] = (files.raw_owner_abs > 0.10).astype(float)
    files["major_abs"] = (files.raw_major - files.decl_major).abs()
    files["minor_abs"] = (files.raw_minor - files.decl_minor).abs()
    res["magnitude"] = {}
    for sample in ("touched", "random"):
        sub = files[files["sample"] == sample]
        rep = sub.groupby("repo")[["raw_owner_abs", "owner_abs_gt10", "major_abs", "minor_abs", "source"]].mean()
        block = {k: boot_ci(rep[k]) for k in rep.columns}
        src = sub[sub.source]
        srep = src.groupby("repo")[["raw_flip", "raw_major_change", "raw_tvd"]].mean()
        block["source_files"] = int(len(src))
        block["source_share_files"] = float(sub.source.mean())
        block["source_flip"] = boot_ci(srep.raw_flip)
        block["source_major"] = boot_ci(srep.raw_major_change)
        block["file_weighted_flip"] = float(sub.raw_flip.mean())
        res["magnitude"][sample] = block

    # RQ4: later-commit distribution, >= 3 later commits, baselines under filters.
    sp = pd.read_csv(out / "supp_prospective.csv")
    later = {}
    for n, r in records.items():
        for f in r.get("past", []):
            if f.get("status") == "OK":
                later[(n, f["path_hash"])] = sum(f.get("future_excl_declared", {}).values())
    sp["later"] = [later.get((a, b), 0) for a, b in zip(sp.repo, sp.path_hash)]
    res["later_commits"] = {"median": float(sp.later.median()), "q1": float(sp.later.quantile(.25)), "q3": float(sp.later.quantile(.75)),
                            "share_one": float((sp.later == 1).mean())}
    methods = ["RAW", "DECLARED", "LAST", "PRIOR", "REPOTOP"]
    three = sp[sp.later >= 3]
    rep = three.groupby("repo")[[f"hit_{m}" for m in methods]].mean()
    res["three_later"] = {"files": int(len(three)), "repos": int(len(rep)), **{m: boot_ci(rep[f"hit_{m}"]) for m in methods}}
    fl3 = three[three.flip]
    rep = fl3.groupby("repo")[[f"hit_{m}" for m in methods]].mean()
    res["three_later_flip"] = {"files": int(len(fl3)), "repos": int(len(rep)), **{m: boot_ci(rep[f"hit_{m}"]) for m in methods}}

    # Baselines against the bulk-free ledger, on owner-change files.
    sups = {json.loads(p.read_text())["full_name"]: json.loads(p.read_text()) for p in (data / "supplement").glob("*.json")}
    brows = []
    for p in (data / "bulk").glob("*.json"):
        b = json.loads(p.read_text())
        n = b["full_name"]
        s = sups.get(n, {})
        if not s.get("c") or not s["c"][0]:
            continue
        crow = {x["path_hash"]: x for x in s["c"][0]}
        repo_top = top_set(s["c"][1]["repo_prior"])
        by_hash = {f["path_hash"]: f for f in records[n]["past"] if f.get("status") == "OK"}
        for bf in b["files"]:
            f, c = by_hash.get(bf["path_hash"]), crow.get(bf["path_hash"])
            if f is None or c is None or not bf["future_no_bulk"]:
                continue
            dc = f["dev_counts"]
            if top_set(dc["RAW"]) == top_set(dc["DECLARED"]):
                continue
            tgt = top_set(bf["future_no_bulk"])
            brows.append({"repo": n, "RAW": hit_target(dc["RAW"], tgt), "DECLARED": hit_target(dc["DECLARED"], tgt),
                          "LAST": float(c["last"] in tgt) if c["last"] else 0.0,
                          "PRIOR": hit_target(c["prior"], tgt) if c["prior"] else 0.0,
                          "REPOTOP": sum(1 for d in repo_top if d in tgt) / len(repo_top) if repo_top else 0.0})
    bd = pd.DataFrame(brows)
    rep = bd.groupby("repo")[methods].mean()
    res["bulk_baselines"] = {"files": int(len(bd)), "repos": int(len(rep)), **{m: boot_ci(rep[m]) for m in methods}}

    # All RQ4 paired comparisons with intervals (owner-change and all files).
    res["rq4_tests"] = {}
    for subset, df in (("all", sp), ("flip", sp[sp.flip])):
        rep = df.groupby("repo")[[f"hit_{m}" for m in methods]].mean()
        tests = {"RAW_vs_DECLARED": paired_test(rep.hit_RAW, rep.hit_DECLARED),
                 "LAST_vs_RAW": paired_test(rep.hit_LAST, rep.hit_RAW),
                 "PRIOR_vs_RAW": paired_test(rep.hit_PRIOR, rep.hit_RAW),
                 "REPOTOP_vs_RAW": paired_test(rep.hit_REPOTOP, rep.hit_RAW),
                 "LAST_vs_DECLARED": paired_test(rep.hit_LAST, rep.hit_DECLARED)}
        adj = holm({k: v["p"] for k, v in tests.items()})
        for k in tests:
            tests[k]["p_holm"] = adj.get(k, np.nan)
        res["rq4_tests"][subset] = tests

    # Minor-contributor change in the historical sample.
    hist = sp.dropna(subset=["minor_RAW", "minor_DECLARED"])
    res["hist_minor_change"] = boot_ci(hist.assign(ch=(hist.minor_RAW != hist.minor_DECLARED).astype(float)).groupby("repo").ch.mean())

    # Partial correlation without the later-activity control.
    cand = json.loads((out / "results_round3.json").read_text())
    extra = []
    for n, s in sups.items():
        if not s.get("c") or not s["c"][0]:
            continue
        for r in s["c"][0]:
            extra.append({"repo": n, "path_hash": r["path_hash"], "prior_commits": sum(r["prior"].values())})
    dd = sp.merge(pd.DataFrame(extra), on=["repo", "path_hash"]).dropna(subset=["own_RAW", "own_DECLARED"])
    dd = dd[dd.groupby("repo").fixes.transform("nunique") > 1].copy()
    for col in ("fixes", "own_RAW", "own_DECLARED", "lines", "prior_commits"):
        dd[col + "_r"] = dd.groupby("repo")[col].rank(pct=True)

    def partial(df, x, controls):
        X = np.column_stack([np.ones(len(df))] + [df[c] for c in controls])
        rx = df[x] - X @ np.linalg.lstsq(X, df[x], rcond=None)[0]
        ry = df["fixes_r"] - X @ np.linalg.lstsq(X, df["fixes_r"], rcond=None)[0]
        return float(np.corrcoef(rx, ry)[0, 1])

    ctrl = ["lines_r", "prior_commits_r"]
    res["partial_no_later"] = {l: partial(dd, f"own_{l}_r", ctrl) for l in ("RAW", "DECLARED")}

    # Declared-set and mapping variants.
    vrows = []
    for p in (data / "variants").glob("*.json"):
        v = json.loads(p.read_text())
        for f in v["files"]:
            for k in ("decl", "fmt", "exact"):
                vrows.append({"repo": v["full_name"], "sample": f["sample"], "variant": k,
                              "flip": f[k]["flip"], "major": f[k]["major"], "tvd": f[k]["tvd"], "has_fmt": v["fmt_commits"] > 0})
    if vrows:
        vd = pd.DataFrame(vrows)
        res["variants"] = {}
        for sample in ("touched", "random"):
            block = {}
            for k in ("decl", "fmt", "exact"):
                sub = vd[(vd["sample"] == sample) & (vd.variant == k)]
                rep = sub.groupby("repo")[["flip", "major", "tvd"]].mean()
                block[k] = {m: boot_ci(rep[m]) for m in ("flip", "major", "tvd")}
                block[k]["repos"] = int(len(rep))
            res["variants"][sample] = block
        res["variants"]["repos_with_fmt"] = int(vd.groupby("repo").has_fmt.first().sum())

    # Agreement with the blame that GitHub reports.
    gh = [json.loads(x.read_text()) for x in (data / "github_check").glob("*.json")]
    if gh:
        ok = pd.DataFrame([g for g in gh if g.get("status") == "OK"])
        ok["decl_share"] = ok.gh_eq_decl / ok.lines
        ok["raw_share"] = ok.gh_eq_raw / ok.lines
        ok["differ_decl"] = ok.differ_gh_eq_decl / ok.differ
        ok["differ_raw"] = ok.differ_gh_eq_raw / ok.differ
        res["github"] = {
            "repos": int(len(ok)), "status": {k: int(v) for k, v in pd.Series([g.get("status") for g in gh]).value_counts().items()},
            "lines": int(ok.lines.sum()), "differ_lines": int(ok.differ.sum()),
            "pooled_decl": float(ok.gh_eq_decl.sum() / ok.lines.sum()), "pooled_raw": float(ok.gh_eq_raw.sum() / ok.lines.sum()),
            "pooled_differ_decl": float(ok.differ_gh_eq_decl.sum() / ok.differ.sum()),
            "pooled_differ_raw": float(ok.differ_gh_eq_raw.sum() / ok.differ.sum()),
            "repo_decl": boot_ci(ok.decl_share), "repo_differ_decl": boot_ci(ok.differ_decl),
            "repos_identical_decl": int((ok.gh_eq_decl == ok.lines).sum()),
            "repos_closer_raw": int((ok.gh_eq_raw > ok.gh_eq_decl).sum()),
        }
        ok.to_csv(out / "github_check.csv", index=False)

    # Large files and repeated -C.
    ex = [json.loads(x.read_text()) for x in (data / "extra").glob("*.json")]
    if ex:
        lf = pd.DataFrame([dict(f, repo=e["full_name"]) for e in ex for f in e.get("large", [])])
        if not lf.empty:
            rep = lf.groupby("repo")[["flip", "major", "tvd"]].mean()
            res["large_files"] = {"files": int(len(lf)), "repos": int(len(rep)), "median_lines": float(lf.lines.median()),
                                  **{k: boot_ci(rep[k]) for k in ("flip", "major", "tvd")}}
        c3 = pd.DataFrame([dict(f, repo=e["full_name"]) for e in ex if e.get("c3") for f in e["c3"]])
        if not c3.empty:
            failed = int((c3.status != "OK").sum())
            c3 = c3[c3.status == "OK"]
            rep = c3.groupby("repo").agg(wmc_flip=("wmc_flip", "mean"), wmc3_flip=("wmc3_flip", "mean"),
                                         wmc_tvd=("wmc_tvd", "mean"), wmc3_tvd=("wmc3_tvd", "mean"),
                                         re=("reattributed", "sum"), un=("uncaptured", "sum"),
                                         wmc_rec=("wmc_recovers", "sum"), wmc3_rec=("wmc3_recovers", "sum"),
                                         wmc_ras=("wmc_reassigns", "sum"), wmc3_ras=("wmc3_reassigns", "sum"))
            withre = rep[rep.re > 0]
            res["c3"] = {"repos": int(len(rep)), "files": int(len(c3)), "failed_files": failed,
                         "wmc_flip": boot_ci(rep.wmc_flip), "wmc3_flip": boot_ci(rep.wmc3_flip),
                         "wmc_tvd": boot_ci(rep.wmc_tvd), "wmc3_tvd": boot_ci(rep.wmc3_tvd),
                         "wmc_recovery": boot_ci(withre.wmc_rec / withre.re), "wmc3_recovery": boot_ci(withre.wmc3_rec / withre.re),
                         "wmc_reassign": boot_ci(rep.wmc_ras / rep.un), "wmc3_reassign": boot_ci(rep.wmc3_ras / rep.un),
                         "flip_test": paired_test(rep.wmc3_flip, rep.wmc_flip), "tvd_test": paired_test(rep.wmc3_tvd, rep.wmc_tvd)}

    (out / "results_final.json").write_text(json.dumps(res, indent=1, default=float))
    print(json.dumps(res, indent=1, default=float)[:4000])


if __name__ == "__main__":
    main()
