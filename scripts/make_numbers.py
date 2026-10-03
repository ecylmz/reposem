"""Write numbers.tex and the result tables from the analysis outputs.

Every number in the manuscript comes from this script, so text, tables, and
figures cannot drift apart.

Usage: make_numbers.py <analysis_dir> <manuscript_dir>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd


def pct(x, digits=1):
    return f"{100 * x:.{digits}f}"


def pct_ci(ci, digits=1):
    return f"{100 * ci[0]:.{digits}f}--{100 * ci[1]:.{digits}f}\\%"


def num(x, digits=3):
    return f"{x:.{digits}f}"


def num_ci(ci, digits=3):
    if min(ci) < 0:
        return f"{signed(ci[0], digits)} to {signed(ci[1], digits)}"
    return f"{ci[0]:.{digits}f}--{ci[1]:.{digits}f}"


def signed(x, digits=3):
    return f"$-${abs(x):.{digits}f}" if x < 0 else f"{x:.{digits}f}"


def thousands(n):
    return f"{int(n):,}".replace(",", "{,}")


def fmt_p(p):
    if p != p:  # NaN
        return "--"
    return "$<$0.001" if p < 0.001 else f"{p:.3f}"


def main():
    analysis, ms = Path(sys.argv[1]), Path(sys.argv[2])
    res = json.loads((analysis / "results.json").read_text())
    frame = json.loads((analysis / "frame.json").read_text())
    unresolved = pd.read_csv(analysis / "unresolved.csv")
    catcheck = json.loads((analysis / "category_check.json").read_text())
    m = {}

    # Frame and inclusion.
    m["FrameDate"] = frame["date"]
    m["FrameRepos"] = thousands(frame["frame_repos"])
    m["WithFile"] = thousands(frame["with_file"])
    m["WithFilePct"] = pct(frame["with_file"] / frame["frame_repos"])
    m["ExcludedSize"] = str(frame["excluded_size"])
    m["Included"] = str(frame["included"])
    st = res["status_counts"]
    ok, none = st.get("OK", 0), st.get("NO_ANCESTOR_DECLARATIONS", 0)
    m["MeasuredOK"] = str(ok)
    m["NoAncestor"] = str(none)
    m["NoAncestorPct"] = pct(none / frame["included"])
    m["RepoFailed"] = str(frame["included"] - ok - none)

    fs = res["file_status"]
    for key, sample in (("Touched", "touched"), ("Random", "random"), ("Past", "past")):
        m[f"{key}Files"] = thousands(fs.get(f"{sample}:OK", 0))
    m["TouchedRepos"] = str(res["divergence"]["touched"]["repos"])
    m["RandomRepos"] = str(res["divergence"]["random"]["repos"])
    m["PastRepos"] = str(res["prospective"]["primary"]["repos_measured"])
    attempted = sum(v for k, v in fs.items())
    not_measured = sum(v for k, v in fs.items() if k.split(":")[1] in ("TIMEOUT", "BUDGET_EXHAUSTED"))
    m["BlameNotMeasured"] = thousands(not_measured)
    m["BlameNotMeasuredPct"] = pct(not_measured / attempted)
    total_files = sum(v for k, v in fs.items() if k.endswith(":OK"))
    m["TotalFilesRounded"] = thousands(int(total_files // 1000 * 1000))

    # RQ1.
    d = res["declarations"]
    m["EntriesMedian"] = f"{d['entries_median']:g}"
    m["EntriesQOne"] = f"{d['entries_iqr'][0]:g}"
    m["EntriesQThree"] = f"{d['entries_iqr'][1]:g}"
    m["EntriesMax"] = str(d["entries_max"])
    m["FileAgeMedian"] = f"{d['file_age_median']:.1f}"
    m["HistoryYearsMedian"] = f"{d['history_years_median']:.1f}"
    m["Revisions"] = thousands(d["revisions"])
    cats = d["revision_categories"]
    m["CatFormatterPct"] = pct(cats.get("formatter", 0) / d["revisions"], 0)
    m["CatFormatterRepoPct"] = pct(d["revision_categories_repo_share"].get("formatter", 0), 0)
    m["RevFilesMedian"] = f"{d['revision_files_median']:g}"
    m["RevLinesMedian"] = thousands(d["revision_lines_median"])
    m["RevLinesPNinety"] = thousands(round(d["revision_lines_p90"], -2))
    with_file = d["repos"]
    m["ReposUnresolved"] = str(d["repos_with_unresolved"])
    m["ReposUnresolvedPct"] = pct(d["repos_with_unresolved"] / with_file)
    m["UnresolvedRepoRatio"] = str(round(with_file / d["repos_with_unresolved"]))
    m["EntriesUnresolvedPct"] = pct(d["entries_unresolved_total"] / d["entries_total"])
    n_un = len(unresolved)
    kinds = unresolved.kind.value_counts()
    on_github = kinds.get("PR_COMMIT_SQUASHED_OR_REBASED", 0) + kinds.get("UNMERGED_COMMIT", 0) + kinds.get("LOCAL_NOT_ON_DEFAULT_BRANCH", 0)
    m["UnresolvedN"] = str(n_un)
    m["UnresolvedOnGitHubPct"] = pct(on_github / n_un, 0)
    m["UnresolvedPRPct"] = pct(kinds.get("PR_COMMIT_SQUASHED_OR_REBASED", 0) / n_un, 0)
    m["UnresolvedLostPct"] = pct(kinds.get("NOT_FOUND_ON_GITHUB", 0) / n_un, 0)
    m["UnresolvedMalformedPct"] = pct(kinds.get("MALFORMED", 0) / n_un, 0)
    m["CatCheckN"] = str(catcheck["n"])
    m["CatCheckAgree"] = str(catcheck["agree"])

    # RQ2.
    t, r = res["divergence"]["touched"], res["divergence"]["random"]
    m["TCapturedPct"] = pct(t["captured"]["mean"])
    m["TCapturePct"] = pct(t["capture"]["mean"])
    for lane, key in (("Raw", "raw"), ("W", "w"), ("WMC", "wmc")):
        m[f"T{lane}DisPct"] = pct(t[f"{key}_dis"]["mean"])
        m[f"T{lane}DisCI"] = pct_ci(t[f"{key}_dis"]["ci"])
        m[f"T{lane}FlipPct"] = pct(t[f"{key}_flip"]["mean"])
        m[f"T{lane}FlipCI"] = pct_ci(t[f"{key}_flip"]["ci"])
        m[f"T{lane}MajorPct"] = pct(t[f"{key}_major_change"]["mean"])
        m[f"T{lane}MajorCI"] = pct_ci(t[f"{key}_major_change"]["ci"])
        m[f"T{lane}TopThreePct"] = pct(t[f"{key}_top3_change"]["mean"])
        m[f"T{lane}TopThreeCI"] = pct_ci(t[f"{key}_top3_change"]["ci"])
        m[f"T{lane}Tvd"] = num(t[f"{key}_tvd"]["mean"])
        m[f"T{lane}TvdCI"] = num_ci(t[f"{key}_tvd"]["ci"])
        m[f"R{lane}FlipPct"] = pct(r[f"{key}_flip"]["mean"])
        m[f"R{lane}FlipCI"] = pct_ci(r[f"{key}_flip"]["ci"])
        m[f"R{lane}MajorPct"] = pct(r[f"{key}_major_change"]["mean"])
        m[f"R{lane}MajorCI"] = pct_ci(r[f"{key}_major_change"]["ci"])
    m["TAnyFlipPct"] = pct(t["any_flip"]["mean"], 0)
    m["RAnyFlipPct"] = pct(r["any_flip"]["mean"], 0)
    m["RCapturedPct"] = pct(r["captured"]["mean"])
    m["TUnblamablePct"] = pct(t["unblamable"]["mean"])
    unb = res["unblamable_of_captured"]
    m["MappedOfCapturedPct"] = pct(1 - unb[0], 0)
    m["MappedOfCapturedCI"] = pct_ci([1 - unb[2], 1 - unb[1]], 0)
    p = res["persistence_repo"]
    m["PersistRepos"] = str(res["persistence_complete_repos"])
    m["PersistYoungPct"] = pct(p["young_visible"][0], 0)
    m["PersistYoungCI"] = pct_ci(p["young_visible"][1:], 0)
    m["PersistOldPct"] = pct(p["old_visible"][0], 0)
    m["PersistOldCI"] = pct_ci(p["old_visible"][1:], 0)

    # RQ3.
    u = res["uncaptured_random"]
    m["UncapFiles"] = thousands(u["files"])
    m["UncapWMCDisPct"] = pct(u["wmc_dis"][0])
    m["UncapWMCDisCI"] = pct_ci(u["wmc_dis"][1:])
    m["UncapWMCFlipPct"] = pct(u["wmc_flip"][0])
    m["UncapWMCFlipCI"] = pct_ci(u["wmc_flip"][1:])
    m["UncapWDisPct"] = pct(u["w_dis"][0])

    # RQ4.
    pr = res["prospective"]["primary"]
    m["ProspEvaluable"] = thousands(pr["files_evaluable"])
    m["ProspRepos"] = str(pr["repos"])
    m["NdcgRAW"] = num(pr["ndcg_RAW"]["mean"])
    m["NdcgDECL"] = num(pr["ndcg_DECLARED"]["mean"])
    m["NdcgDiff"] = num(pr["tests"]["ndcg_DECLARED_vs_RAW"]["mean_diff"])
    m["NdcgDiffCI"] = num_ci(pr["tests"]["ndcg_DECLARED_vs_RAW"]["mean_diff_ci"])
    m["HitRAW"] = num(pr["hit1_RAW"]["mean"])
    m["HitDECL"] = num(pr["hit1_DECLARED"]["mean"])
    m["FlipFiles"] = thousands(pr["flip_files"])
    m["FlipRepos"] = str(pr["flip_repos"])
    m["FlipHitRAWPct"] = pct(pr["flip_hit1_RAW"]["mean"], 0)
    m["FlipHitRAWCI"] = pct_ci(pr["flip_hit1_RAW"]["ci"], 0)
    m["FlipHitDECLPct"] = pct(pr["flip_hit1_DECLARED"]["mean"], 0)
    m["FlipHitDECLCI"] = pct_ci(pr["flip_hit1_DECLARED"]["ci"], 0)
    mech = res["mechanism"]
    m["MechRawAuthorPct"] = pct(mech["raw_owner_declared_author"][0], 0)
    m["MechRawActivePct"] = pct(mech["raw_owner_active_after"][0], 0)
    m["MechDeclActivePct"] = pct(mech["decl_owner_active_after"][0], 0)
    m["MechActiveDiff"] = pct(mech["active_test"]["mean_diff"], 0) + "~percentage points"
    m["MechActiveDiffCI"] = f"{100 * mech['active_test']['mean_diff_ci'][0]:.0f}--{100 * mech['active_test']['mean_diff_ci'][1]:.0f}"
    m["BootB"] = thousands(5000)
    m["NdcgDiff"] = signed(pr["tests"]["ndcg_DECLARED_vs_RAW"]["mean_diff"])
    m["NdcgDiffP"] = fmt_p(pr["tests"]["ndcg_DECLARED_vs_RAW"]["p_holm"])
    ft = pr["flip_tests"]["hit1"]
    m["FlipHitDiff"] = pct(-ft["mean_diff"], 0)
    m["FlipHitDiffCI"] = f"{-100 * ft['mean_diff_ci'][1]:.0f}--{-100 * ft['mean_diff_ci'][0]:.0f}"
    m["FlipHitR"] = f"{abs(ft['rank_biserial']):.2f}"
    m["FlipHitP"] = fmt_p(ft["p"])
    m["CaptureTvdRho"] = f"{res['capture_tvd_spearman'][0]:.2f}"
    h = res["heuristics"]
    m["WRawTvdDelta"] = signed(h["W_vs_RAW_tvd"]["mean_diff"])
    m["WRawTvdP"] = fmt_p(h["W_vs_RAW_tvd"]["p_holm"])
    m["WMCRawTvdDelta"] = signed(h["WMC_vs_RAW_tvd"]["mean_diff"])
    m["WCloserPct"] = pct(res["heuristics_repo_share_w_closer"], 0)
    for key, vals in res["divergence_by_category"].items():
        name = {"formatter": "Fmt", "whitespace": "Ws", "migration": "Mig", "style": "Sty"}.get(key)
        if name:
            m[f"Cat{name}Repos"] = str(vals["repos"])
            m[f"Cat{name}RawTvd"] = num(vals["raw_tvd"])
            m[f"Cat{name}WTvd"] = num(vals["w_tvd"])
    for c, name in (("migration", "Migration"), ("lint", "Lint"), ("style", "Style"), ("other", "Other")):
        m[f"Cat{name}Pct"] = pct(cats.get(c, 0) / d["revisions"], 0)
        m[f"Cat{name}RepoPct"] = pct(d["revision_categories_repo_share"].get(c, 0), 0)

    lines = [f"\\newcommand{{\\{k}}}{{{v}}}" for k, v in sorted(m.items())]
    (ms / "numbers.tex").write_text("% Generated by scripts/make_numbers.py. Do not edit.\n" + "\n".join(lines) + "\n")

    # Table: categories.
    revs = pd.read_csv(analysis / "declared_revisions.csv")
    n_repos = revs.repo.nunique()
    order = [c for c in revs.category.value_counts().index if c != "other"] + ["other"]
    names = {"formatter": "Formatter", "whitespace": "Whitespace or indentation", "refactoring": "Refactoring",
             "lint": "Lint or spelling", "style": "Style", "move/rename": "Move or rename",
             "migration": "Language or API migration", "license/header": "License or header", "other": "Other"}
    rows = ["\\resizebox{\\linewidth}{!}{%", "\\begin{tabular}{lrrrr}", "\\toprule",
            "Category & Commits & \\% of commits & \\% of repos. & Median lines \\\\", "\\midrule"]
    for c in order:
        sub = revs[revs.category == c]
        if sub.empty:
            continue
        rows.append(f"{names[c]} & {thousands(len(sub))} & {pct(len(sub) / len(revs))} & "
                    f"{pct(sub.repo.nunique() / n_repos)} & {thousands(sub.lines_changed.median())} \\\\")
    rows += ["\\midrule", f"Total ({n_repos} repositories) & {thousands(len(revs))} & 100.0 & & {thousands(revs.lines_changed.median())} \\\\",
             "\\bottomrule", "\\end{tabular}}"]
    (ms / "tables" / "categories.tex").write_text("\n".join(rows) + "\n")

    # Table: heuristic tests.
    h = res["heuristics"]
    rows = ["\\begin{tabular}{llrrr}", "\\toprule", "Measure & Comparison & $\\Delta$ & $r$ & $p$ \\\\", "\\midrule"]
    for key, name in (("tvd", "Share distance"), ("dis", "Line disagreement"), ("flip", "Owner change")):
        for comp, label in (("W_vs_RAW", "\\W{} vs.\\ \\RAW"), ("WMC_vs_RAW", "\\WMC{} vs.\\ \\RAW"), ("WMC_vs_W", "\\WMC{} vs.\\ \\W")):
            v = h[f"{comp}_{key}"]
            rows.append(f"{name if comp == 'W_vs_RAW' else ''} & {label} & {v['mean_diff']:+.3f} & {v['rank_biserial']:+.2f} & {fmt_p(v['p_holm'])} \\\\")
        rows.append("\\addlinespace")
    rows[-1] = "\\bottomrule"
    rows.append("\\end{tabular}")
    (ms / "tables" / "heuristics.tex").write_text("\n".join(rows) + "\n")

    # Table: prospective alignment.
    a = res["prospective"]["all_commits"]

    def ci_cell(v):
        return f"{v['mean']:.3f} {{\\scriptsize[{v['ci'][0]:.2f}, {v['ci'][1]:.2f}]}}"

    rows = ["\\resizebox{\\linewidth}{!}{%", "\\begin{tabular}{lcccc}", "\\toprule",
            "& \\multicolumn{2}{c}{All evaluable files} & \\multicolumn{2}{c}{Owner-change files} \\\\",
            "\\cmidrule(lr){2-3}\\cmidrule(lr){4-5}",
            "Lane & NDCG@5 & Hit@1 & Hit@1 & Hit@1 (all commits) \\\\", "\\midrule"]
    for lane, label in (("RAW", "\\RAW"), ("W", "\\W"), ("WMC", "\\WMC"), ("DECLARED", "\\DECL")):
        flip = ci_cell(pr[f"flip_hit1_{lane}"]) if f"flip_hit1_{lane}" in pr else "--"
        flip_all = ci_cell(a[f"flip_hit1_{lane}"]) if f"flip_hit1_{lane}" in a else "--"
        rows.append(f"{label} & {ci_cell(pr[f'ndcg_{lane}'])} & {ci_cell(pr[f'hit1_{lane}'])} & {flip} & {flip_all} \\\\")
    rows += ["\\midrule",
             f"Files (repositories) & \\multicolumn{{2}}{{c}}{{{thousands(pr['files_evaluable'])} ({pr['repos']})}} & "
             f"{thousands(pr['flip_files'])} ({pr['flip_repos']}) & {thousands(a['flip_files'])} ({a['flip_repos']}) \\\\",
             "\\bottomrule", "\\end{tabular}}"]
    (ms / "tables" / "prospective.tex").write_text("\n".join(rows) + "\n")
    # Supplementary analyses: extra macros, and tables that replace the ones above.
    supplement_macros(m, analysis, ms, res, frame)
    round3_macros(m, analysis)
    final_macros(m, analysis, ms)
    # Keep only the macros that the manuscript uses.
    import re as _re
    used = set()
    for tex in [ms / "main.tex", *ms.glob("sections/*.tex"), *ms.glob("figures/*.tex")]:
        if not tex.exists():
            continue
        used |= set(_re.findall(r"\\([A-Za-z]+)", tex.read_text()))
    if used:  # without manuscript sources (replication package), keep every macro
        m = {k: v for k, v in m.items() if k in used}
    # p-value macros used in prose carry their relation sign.
    for k in ("SurvDiffP", "HitAllDiffP", "HitFlipDiffP", "CThreeFlipP"):
        if k in m:
            m[k] = "$p < 0.001$" if "<" in m[k] else f"$p = {m[k]}$"
    lines = [f"\\newcommand{{\\{k}}}{{{v}}}" for k, v in sorted(m.items())]
    (ms / "numbers.tex").write_text("% Generated by scripts/make_numbers.py. Do not edit.\n" + "\n".join(lines) + "\n")
    print(len(m), "macros written")


def wilson(k, n, z=1.96):
    phat = k / n
    denom = 1 + z * z / n
    centre = (phat + z * z / (2 * n)) / denom
    half = z * ((phat * (1 - phat) / n + z * z / (4 * n * n)) ** 0.5) / denom
    return centre - half, centre + half


def supplement_macros(m, analysis: Path, ms: Path, res: dict, frame: dict):
    sup = json.loads((analysis / "results_supplement.json").read_text())
    d = res["declarations"]
    chk = pd.read_csv(analysis / "declarations_check.csv")
    nonempty = int((chk.distinct > 0).sum())
    m["EmptyFiles"] = str(d["empty_files"])
    m["NonEmptyFiles"] = str(nonempty)
    m["AllInactive"] = str(d["repos_all_unresolved"])
    m["DupRepos"] = str(d["repos_with_duplicates"])
    m["CommentsPct"] = pct(d["files_with_comments"], 0)
    m["ReposUnresolved"] = str(d["repos_with_unresolved"])
    m["ReposUnresolvedPct"] = pct(d["repos_with_unresolved"] / nonempty)
    m["UnresolvedRepoRatio"] = str(round(nonempty / d["repos_with_unresolved"]))
    m["EntriesUnresolvedPct"] = pct(d["entries_unresolved_total"] / d["entries_total"])
    m["EntriesTotal"] = thousands(d["entries_total"])
    lo, hi = wilson(int(m["CatCheckAgree"]), int(m["CatCheckN"]))
    m["CatCheckCI"] = f"{100 * lo:.0f}--{100 * hi:.0f}\\%"
    m["ExcludedSmall"] = str(frame["excluded_commits"] + frame["excluded_contributors"])

    revs = pd.read_csv(analysis / "declared_revisions.csv")
    mig = revs[revs.category == "migration"].repo.value_counts()
    lint = revs[revs.category == "lint"].repo.value_counts()
    m["MigTopThreePct"] = pct(mig.head(3).sum() / mig.sum(), 0)
    m["LintTopOnePct"] = pct(lint.head(1).sum() / lint.sum(), 0)
    m["MergeDeclared"] = str(int((revs.files_changed == 0).sum()))

    # Population descriptives.
    meta = json.loads((analysis.parent / "frame_with_file_meta.json").read_text())
    inc = [l.strip() for l in (analysis.parent / "study_repos.txt").read_text().splitlines() if l.strip()]
    mm = {x["full_name"]: x for x in meta}
    df = pd.DataFrame([mm[n] for n in inc])
    m["MedStars"] = thousands(df.stars.median())
    m["MedCommits"] = thousands(df.commits.median())
    m["MedContributors"] = thousands(df.contributors.median())
    langs = df.language.fillna("none").value_counts()
    m["TopLangs"] = ", ".join(f"{k} ({v})" for k, v in langs.head(5).items())
    m["PythonPct"] = pct(langs.get("Python", 0) / len(df), 0)

    files = pd.read_csv(analysis / "files.csv")
    tf = files[files["sample"] == "touched"].assign(minor_change=lambda d: (d.raw_minor != d.decl_minor).astype(float))
    m["TMinorChangePct"] = pct(tf.groupby("repo").minor_change.mean().mean())

    # RQ2 additions.
    rho = res["capture_tvd_spearman"][0]
    m["CaptureTvdRho"] = f"{rho:.2f}"
    rob = sup["robustness"]
    m["TFlipTwoStageCI"] = pct_ci(rob["touched"]["two_stage_flip"][1:])
    m["RFlipTwoStageCI"] = pct_ci(rob["random"]["two_stage_flip"][1:])
    m["TFlipFileWeighted"] = pct(rob["touched"]["file_weighted_flip"])
    m["RFlipFileWeighted"] = pct(rob["random"]["file_weighted_flip"])
    m["TFlipMinHundred"] = pct(rob["touched"]["min100_flip"][0])
    m["RFlipMinHundred"] = pct(rob["random"]["min100_flip"][0])
    m["TMajorMinHundred"] = pct(rob["touched"]["min100_major"][0])

    B = sup["B"]
    m["PersistCommits"] = thousands(B["commits"])
    m["PersistRepos"] = str(B["repos"])
    m["PersistMatched"] = thousands(B["matched"])
    m["SurvivalPct"] = pct(B["survival"][0], 0)
    m["SurvivalCI"] = pct_ci(B["survival"][1:], 0)
    m["VisiblePct"] = pct(B["visible"][0], 0)
    m["SurvDeclPct"] = pct(B["matched_survival_declared"][0], 1)
    m["SurvUndeclPct"] = pct(B["matched_survival_undeclared"][0], 1)
    m["SurvDiff"] = pct(B["matched_test"]["mean_diff"], 1)
    m["SurvDiffCI"] = f"{100 * B['matched_test']['mean_diff_ci'][0]:.1f}--{100 * B['matched_test']['mean_diff_ci'][1]:.1f}"
    m["SurvDiffP"] = fmt_p(B["matched_test"]["p"])
    ages = {r["age_bin"]: r for r in B["by_age"]}
    m["SurvYoungDecl"] = pct(ages["<1"]["declared"][0], 0)
    m["SurvOldDecl"] = pct(ages[">5"]["declared"][0], 0)
    m["SurvOldUndecl"] = pct(ages[">5"]["undeclared"][0], 0)

    # RQ3 additions.
    A = sup["A"]
    for lane in ("W", "WMC"):
        m[f"{lane}RecoveryPct"] = pct(A[f"{lane}_recovery"][0], 0)
        m[f"{lane}RecoveryCI"] = pct_ci(A[f"{lane}_recovery"][1:], 0)
        m[f"{lane}SpuriousPct"] = pct(A[f"{lane}_spurious"][0])
        m[f"{lane}SpuriousCI"] = pct_ci(A[f"{lane}_spurious"][1:])
    m["DeclOnWFlipPct"] = pct(A["declared_on_w_flip"][0])
    m["DeclOnWFlipCI"] = pct_ci(A["declared_on_w_flip"][1:])
    m["DeclOnWTvd"] = num(A["declared_on_w_tvd"][0])
    m["ReattrLines"] = thousands(A["reattributed_lines"])
    m["ReattrOfCapturedPct"] = pct(A["reattributed_of_captured"][0], 0)
    m["ContentWsPct"] = pct(A["content_ws_equal"][0], 0)
    m["ContentAlnumPct"] = pct(A["content_alnum_equal"][0], 0)
    m["ContentAlnumCI"] = pct_ci(A["content_alnum_equal"][1:], 0)
    hd = rob["heuristic_direction"]
    m["WCloserPct"] = pct(hd["w_tvd"]["closer"], 0)
    m["WFartherPct"] = pct(hd["w_tvd"]["farther"], 0)
    m["WMCFartherPct"] = pct(hd["wmc_tvd"]["farther"], 0)
    m["WDisFartherPct"] = pct(hd["w_dis"]["farther"], 0)

    # RQ4.
    C = sup["C"]
    m["ProspFiles"] = thousands(C["files"])
    m["ProspRepos"] = str(C["repos"])
    m["FlipFiles"] = thousands(C["flip_files"])
    m["FlipRepos"] = str(C["flip_repos"])
    names = {"RAW": "RAW", "W": "W", "WMC": "WMC", "DECLARED": "DECL", "LAST": "Last", "PRIOR": "Prior", "REPOTOP": "RepoTop"}
    for subset, tag in (("all", "All"), ("flip", "Flip")):
        for k, n in names.items():
            m[f"Hit{tag}{n}"] = pct(C[subset][k][0], 1)
            m[f"Hit{tag}{n}CI"] = pct_ci(C[subset][k][1:], 0)
        t = C[subset]["RAW_vs_DECLARED"]
        m[f"Hit{tag}Diff"] = pct(t["mean_diff"], 1)
        m[f"Hit{tag}DiffCI"] = f"{100 * t['mean_diff_ci'][0]:.1f}--{100 * t['mean_diff_ci'][1]:.1f}"
        m[f"Hit{tag}DiffP"] = fmt_p(t["p_holm"])
        m[f"Hit{tag}DiffR"] = f"{abs(t['rank_biserial']):.2f}"
        m[f"Hit{tag}TopP"] = fmt_p(C[subset]["REPOTOP_vs_RAW"]["p_holm"])
    mech = C["mechanism"]
    m["MechRawAuthorPct"] = pct(mech["raw_owner_declared_author"][0], 0)
    m["MechDeclAuthorPct"] = pct(mech["decl_owner_declared_author"][0], 0)
    m["MechRawBeforePct"] = pct(mech["raw_owner_active_before"][0], 0)
    m["MechDeclBeforePct"] = pct(mech["decl_owner_active_before"][0], 0)
    m["MechRawActivePct"] = pct(mech["raw_owner_active_after"][0], 0)
    m["MechDeclActivePct"] = pct(mech["decl_owner_active_after"][0], 0)
    m["MechRawPriorMedian"] = f"{mech['prior_commits_median_raw']:.0f}"
    m["MechDeclPriorMedian"] = f"{mech['prior_commits_median_decl']:.0f}"
    m["BotRawPct"] = pct(C["flip_raw_owner_bot"])
    m["AliasPct"] = pct(C["flip_alias"])
    m["IgnoreAtSnapshotPct"] = pct(C["share_repos_ignore_at_snapshot"], 0)
    dn = C["downstream"]
    m["DownFiles"] = thousands(dn["files"])
    m["DownRepos"] = str(dn["repos"])
    m["DownOwnRaw"] = signed(dn["own"]["RAW"][0], 2)
    m["DownOwnDecl"] = signed(dn["own"]["DECLARED"][0], 2)
    m["DownOwnDiff"] = signed(dn["own"]["diff"][0], 3)
    m["DownOwnDiffCI"] = num_ci(dn["own"]["diff"][1:])
    m["DownOwnRelPct"] = pct(dn["own"]["diff"][0] / dn["own"]["RAW"][0], 0)
    m["DownOwnSmallerPct"] = pct(1 - dn["own"]["RAW"][0] / dn["own"]["DECLARED"][0], 0)
    m["DownMinorRaw"] = num(dn["minor"]["RAW"][0], 2)
    m["DownMinorDecl"] = num(dn["minor"]["DECLARED"][0], 2)
    m["DownMinorDiffCI"] = num_ci(dn["minor"]["diff"][1:])

    # Table: RQ4.
    sens = C["sensitivity"]
    rows = ["\\begin{tabular}{lcc}", "\\toprule",
            "Predictor of the top later committer & All evaluable files & Owner-change files \\\\", "\\midrule"]
    labels = [("RAW", "\\RAW{} owner"), ("W", "\\W{} owner"), ("WMC", "\\WMC{} owner"), ("DECLARED", "\\DECL{} owner"),
              ("PRIOR", "Top committer to the file, prior year"), ("LAST", "Last committer to the file"),
              ("REPOTOP", "Most active developer in the repository, prior year")]
    for k, label in labels:
        a, f = C["all"][k], C["flip"][k]
        rows.append(f"{label} & {a[0]:.3f} {{\\scriptsize[{a[1]:.3f}, {a[2]:.3f}]}} & {f[0]:.3f} {{\\scriptsize[{f[1]:.3f}, {f[2]:.3f}]}} \\\\")
    rows += ["\\midrule", f"Files (repositories) & {thousands(C['files'])} ({C['repos']}) & {thousands(C['flip_files'])} ({C['flip_repos']}) \\\\",
             "\\midrule", "\\multicolumn{3}{l}{\\emph{Sensitivity on owner-change files: \\RAW{} vs.\\ \\DECL{} owner}} \\\\"]
    for key, label in (("no_bot_no_alias", "Without bot owners and name-matched aliases"),
                       ("bot_commits_removed", "Later commits by bots removed"),
                       ("bulk_commits_removed", "Later commits touching $>$20 files removed"),
                       ("ignore_file_at_snapshot", "Ignore file already present at the snapshot")):
        v = sens[key]
        rows.append(f"{label} ({v['files']} files) & \\multicolumn{{2}}{{c}}{{{v['RAW'][0]:.3f} vs.\\ {v['DECLARED'][0]:.3f}, $p$ {fmt_p(v['test']['p_holm'])}}} \\\\")
    rows += ["\\bottomrule", "\\end{tabular}"]
    (ms / "tables" / "prospective.tex").write_text("\n".join(rows) + "\n")

    # Table: RQ3.
    h = res["heuristics"]
    rows = ["\\begin{tabular}{@{}>{\\raggedright\\arraybackslash}p{3.0cm}cccc@{}}", "\\toprule",
            "& \\multicolumn{2}{c}{\\W} & \\multicolumn{2}{c}{\\WMC} \\\\", "\\cmidrule(lr){2-3}\\cmidrule(lr){4-5}",
            "Measure & value [95\\% CI] & $p$ & value [95\\% CI] & $p$ \\\\", "\\midrule"]
    def ci(v, d=0):
        return f"{pct(v[0], d)}\\% {{\\scriptsize[{pct(v[1], d)}, {pct(v[2], d)}]}}"
    rows.append(f"Re-attributed lines recovered & {ci(A['W_recovery'])} & & {ci(A['WMC_recovery'])} & \\\\")
    rows.append(f"Uncaptured lines reassigned & {ci(A['W_spurious'], 1)} & & {ci(A['WMC_spurious'], 1)} & \\\\")
    for key, label in (("tvd", "share distance"), ("dis", "line disagreement"), ("flip", "owner change")):
        cells = []
        for lane in ("W", "WMC"):
            v = h[f"{lane}_vs_RAW_{key}"]
            lo, hi = v["mean_diff_ci"]
            cells += [f"{signed(v['mean_diff'])} {{\\scriptsize[{signed(lo)}, {signed(hi)}]}}", fmt_p(v["p_holm"])]
        rows.append(f"$\\Delta$ {label} & " + " & ".join(cells) + " \\\\")
    rows.append(f"Repositories closer / farther than \\RAW{{}} (share distance) & {pct(hd['w_tvd']['closer'], 0)}\\% / {pct(hd['w_tvd']['farther'], 0)}\\% & & {pct(hd['wmc_tvd']['closer'], 0)}\\% / {pct(hd['wmc_tvd']['farther'], 0)}\\% & \\\\")
    rows += ["\\bottomrule", "\\end{tabular}"]
    (ms / "tables" / "heuristics.tex").write_text("\n".join(rows) + "\n")

    # Table: population and samples.
    st = res["status_counts"]
    fs = res["file_status"]
    rows = ["\\begin{tabular}{lr}", "\\toprule", "Stage & Count \\\\", "\\midrule",
            f"Repositories with $\\geq$5{{,}}000 stars (non-fork, non-archived) & {thousands(frame['frame_repos'])} \\\\",
            f"\\quad with a root \\texttt{{.git-blame-ignore-revs}} & {frame['with_file']} \\\\",
            f"\\quad excluded: larger than 2.5\\,GB & {frame['excluded_size']} \\\\",
            f"\\quad excluded: $<$200 commits or $<$10 mentionable users & {frame['excluded_commits'] + frame['excluded_contributors']} \\\\",
            f"Included repositories & {frame['included']} \\\\",
            f"\\quad empty ignore file & {d['empty_files']} \\\\",
            f"\\quad no entry resolves to a default-branch commit & {d['repos_all_unresolved']} \\\\",
            f"Repositories with an effective declaration (measured) & {st.get('OK', 0)} \\\\",
            "\\midrule",
            f"Touched sample: files (repositories) & {thousands(fs.get('touched:OK', 0))} ({res['divergence']['touched']['repos']}) \\\\",
            f"Random sample: files (repositories) & {thousands(fs.get('random:OK', 0))} ({res['divergence']['random']['repos']}) \\\\",
            f"Historical sample: files (repositories) & {thousands(fs.get('past:OK', 0))} ({res['prospective']['primary']['repos_measured']}) \\\\",
            f"\\quad with at least one later commit & {thousands(C['files'])} ({C['repos']}) \\\\",
            f"\\quad of which owner-change files & {thousands(C['flip_files'])} ({C['flip_repos']}) \\\\",
            f"\\quad with variation in later fixes (ownership--fix analysis) & {thousands(C['downstream']['files'])} ({C['downstream']['repos']}) \\\\",
            "\\bottomrule", "\\end{tabular}"]
    (ms / "tables" / "flow.tex").write_text("\n".join(rows) + "\n")


def round3_macros(m, analysis: Path):
    path = analysis / "results_round3.json"
    if not path.exists():
        return
    r = json.loads(path.read_text())
    for subset, tag in (("all", "All"), ("flip", "Flip")):
        b = r["rq4_model"][subset]
        m[f"Model{tag}Files"] = thousands(b["files"])
        for k, name in (("raw_owner", "Raw"), ("decl_owner", "Decl")):
            v = b["both"][k]
            m[f"Model{tag}{name}OR"] = f"{v['or']:.2f}"
            m[f"Model{tag}{name}CI"] = f"{v['lo']:.2f}--{v['hi']:.2f}"
            lo, hi = b["both_cluster_ci"][k]
            m[f"Model{tag}{name}ClusterCI"] = f"{lo:.2f}--{hi:.2f}"
        m[f"Model{tag}RepoPriorOR"] = f"{b['both']['repo_prior']['or']:.2f}"
        m[f"Model{tag}FilePriorOR"] = f"{b['both']['file_prior']['or']:.2f}"
    m["FlipRawIsRepoTopPct"] = pct(r["flip_raw_owner_is_repo_top"], 0)
    d = r["downstream_controlled"]
    m["DownPartialRaw"] = signed(d["own"]["RAW"][0], 3)
    m["DownPartialDecl"] = signed(d["own"]["DECLARED"][0], 3)
    m["DownPartialDiff"] = signed(d["own"]["diff"][0], 3)
    m["DownPartialDiffCI"] = num_ci(d["own"]["diff"][1:])
    m["DownPartialSmallerPct"] = pct(1 - d["own"]["RAW"][0] / d["own"]["DECLARED"][0], 0)
    m["DownRepoMeanRaw"] = signed(d["own"]["per_repo_mean_RAW"], 2)
    m["DownRepoMeanDecl"] = signed(d["own"]["per_repo_mean_DECLARED"], 2)
    m["DownMinorPartialRaw"] = num(d["minor"]["RAW"][0], 3)
    m["DownMinorPartialDecl"] = num(d["minor"]["DECLARED"][0], 3)
    m["DownMinorPartialDiffCI"] = num_ci(d["minor"]["diff"][1:])
    pc = r["pr_counterparts"]
    m["PRLinked"] = str(sum(pc.values()))
    m["PRCounterpartUndeclared"] = str(pc.get("counterpart_undeclared", 0))
    stars = {x["bucket"]: x for x in r["adoption_by_stars"]}
    m["AdoptLowPct"] = pct(stars["5k-10k"]["mean"])
    m["AdoptHighPct"] = pct(stars[">=50k"]["mean"])
    years = {int(k): v for k, v in r["adoption_by_year"].items()}
    m["AdoptBeforeTwentyTwo"] = str(sum(v for k, v in years.items() if k < 2022))
    m["AdoptTwentyTwo"] = str(years.get(2022, 0))
    m["AdoptAfterTwentyTwo"] = str(sum(v for k, v in years.items() if k > 2022))
    c = r["context"]
    for sample, tag in (("touched", "T"), ("random", "R")):
        m[f"{tag}CtxRawW"] = pct(c[sample]["raw_w"][0])
        m[f"{tag}CtxRawWMC"] = pct(c[sample]["raw_wmc"][0])
        m[f"{tag}MajorTen"] = pct(c[sample]["major10"][0])
        m[f"{tag}NoAliasFlip"] = pct(c[sample]["raw_decl_noalias"][0])
        m[f"{tag}NamedFlip"] = pct(c[sample]["raw_decl_named"][0])
    m["NamedRepos"] = str(c["touched"]["named_repos"])
    sl = r["snapshot_list"]
    m["SnapRepos"] = str(sl["repos_kept"])
    m["SnapRaw"] = f"{sl['RAW'][0]:.2f}"
    m["SnapDecl"] = f"{sl['DECLARED'][0]:.2f}"
    m["SnapP"] = fmt_p(sl["test"]["p"])
    per = r["persistence_by_age_diff"]
    m["PersistMinDiff"] = pct(min(x["diff"] for x in per), 0)
    m["PersistMaxDiff"] = pct(max(x["diff"] for x in per), 0)
    m["PersistAllBinsPositive"] = "every" if all(x["ci"][0] > 0 for x in per) else "most"


def final_macros(m, analysis: Path, ms: Path):
    path = analysis / "results_final.json"
    if not path.exists():
        return
    r = json.loads(path.read_text())
    mg = r["magnitude"]
    for sample, tag in (("touched", "T"), ("random", "R")):
        g = mg[sample]
        m[f"{tag}OwnerAbs"] = num(g["raw_owner_abs"][0])
        m[f"{tag}OwnerAbsGtTen"] = pct(g["owner_abs_gt10"][0], 0)
        m[f"{tag}MajorAbs"] = num(g["major_abs"][0], 2)
        m[f"{tag}SourcePct"] = pct(g["source_share_files"], 0)
        m[f"{tag}SourceFlip"] = pct(g["source_flip"][0])
        m[f"{tag}SourceFlipCI"] = pct_ci(g["source_flip"][1:])
        m[f"{tag}SourceMajor"] = pct(g["source_major"][0])
    m["RFlipFileWeightedFinal"] = pct(mg["random"]["file_weighted_flip"])
    lc = r["later_commits"]
    m["LaterMedian"] = f"{lc['median']:.0f}"
    m["LaterQOne"] = f"{lc['q1']:.0f}"
    m["LaterQThree"] = f"{lc['q3']:.0f}"
    m["LaterOnePct"] = pct(lc["share_one"], 0)
    t3 = r["three_later_flip"]
    m["ThreeFlipFiles"] = str(t3["files"])
    m["ThreeFlipRaw"] = pct(t3["RAW"][0])
    m["ThreeFlipDecl"] = pct(t3["DECLARED"][0])
    m["ThreeFlipLast"] = pct(t3["LAST"][0])
    m["ThreeFlipRepoTop"] = pct(t3["REPOTOP"][0])
    bb = r["bulk_baselines"]
    m["BulkRaw"] = pct(bb["RAW"][0])
    m["BulkDecl"] = pct(bb["DECLARED"][0])
    m["BulkLast"] = pct(bb["LAST"][0])
    m["BulkPrior"] = pct(bb["PRIOR"][0])
    m["BulkRepoTop"] = pct(bb["REPOTOP"][0])
    for subset, tag in (("all", "All"), ("flip", "Flip")):
        t = r["rq4_tests"][subset]
        for k, name in (("RAW_vs_DECLARED", "RawDecl"), ("LAST_vs_RAW", "LastRaw"), ("PRIOR_vs_RAW", "PriorRaw"),
                        ("REPOTOP_vs_RAW", "TopRaw"), ("LAST_vs_DECLARED", "LastDecl")):
            v = t[k]
            m[f"Test{tag}{name}D"] = pct(v["mean_diff"])
            m[f"Test{tag}{name}CI"] = f"{100 * v['mean_diff_ci'][0]:.1f}--{100 * v['mean_diff_ci'][1]:.1f}".replace("-0.", "$-$0.") if v['mean_diff_ci'][0] >= 0 else f"$-${abs(100 * v['mean_diff_ci'][0]):.1f} to {100 * v['mean_diff_ci'][1]:.1f}"
            m[f"Test{tag}{name}P"] = fmt_p(v["p_holm"])
    m["HistMinorChangePct"] = pct(r["hist_minor_change"][0], 0)
    m["PartialNoLaterRaw"] = signed(r["partial_no_later"]["RAW"], 3)
    m["PartialNoLaterDecl"] = signed(r["partial_no_later"]["DECLARED"], 3)
    kp = analysis / "kappa.json"
    if kp.exists():
        k = list(json.loads(kp.read_text())["per_rater"].values())[0]
        m["KappaN"] = str(k["items"])
        m["KappaAgree"] = pct(k["agreement"], 0)
        m["KappaAgreeCI"] = f"{100 * k['agreement_ci'][0]:.0f}--{100 * k['agreement_ci'][1]:.0f}\\%"
        m["Kappa"] = f"{k['kappa']:.2f}"
        m["KappaCI"] = f"{k['kappa_ci'][0]:.2f}--{k['kappa_ci'][1]:.2f}"
        m["KappaCoarseAgree"] = pct(k["coarse_agreement"], 0)
        m["KappaCoarse"] = f"{k['coarse_kappa']:.2f}"
        m["KappaRuleFormatter"] = str(k["rule_counts"].get("formatter", 0))
        m["KappaHumanFormatter"] = str(k["human_counts"].get("formatter", 0))
    if "github" in r:
        g = r["github"]
        m["GhRepos"] = str(g["repos"])
        m["GhLines"] = thousands(g["lines"])
        m["GhDifferLines"] = thousands(g["differ_lines"])
        m["GhAgreePct"] = pct(g["pooled_decl"], 1)
        m["GhRawPct"] = pct(g["pooled_raw"], 1)
        m["GhIdentical"] = str(g["repos_identical_decl"])
    if "large_files" in r:
        lf = r["large_files"]
        m["LargeFiles"] = str(lf["files"])
        m["LargeRepos"] = str(lf["repos"])
        m["LargeMedianLines"] = thousands(lf["median_lines"])
        m["LargeFlip"] = pct(lf["flip"][0])
        m["LargeFlipCI"] = pct_ci(lf["flip"][1:])
        m["LargeMajor"] = pct(lf["major"][0])
    if "c3" in r:
        c = r["c3"]
        m["CThreeRepos"] = str(c["repos"])
        m["CThreeFiles"] = thousands(c["files"])
        m["CThreeFailed"] = str(c["failed_files"])
        for k, name in (("wmc", "One"), ("wmc3", "Three")):
            m[f"C{name}Flip"] = pct(c[f"{k}_flip"][0])
            m[f"C{name}Tvd"] = num(c[f"{k}_tvd"][0])
            m[f"C{name}Recovery"] = pct(c[f"{k}_recovery"][0], 0)
            m[f"C{name}Reassign"] = pct(c[f"{k}_reassign"][0])
        m["CThreeFlipP"] = fmt_p(c["flip_test"]["p"])
    if "variants" in r:
        v = r["variants"]
        m["VarRepos"] = str(v["touched"]["decl"]["repos"])
        m["VarFmtRepos"] = str(v["repos_with_fmt"])
        for sample, tag in (("touched", "T"), ("random", "R")):
            for k, name in (("decl", "Decl"), ("fmt", "Fmt"), ("exact", "Exact")):
                m[f"Var{tag}{name}Flip"] = pct(v[sample][k]["flip"][0])
                m[f"Var{tag}{name}Major"] = pct(v[sample][k]["major"][0])

    # RQ4 tables: hits, then tests and sensitivity.
    sup = json.loads((analysis / "results_supplement.json").read_text())
    C = sup["C"]
    rows = ["\\begin{tabular}{@{}>{\\raggedright\\arraybackslash}p{4.6cm}cc@{}}", "\\toprule", "Predictor & All evaluable files & Owner-change files \\\\", "\\midrule"]
    labels = [("RAW", "\\RAW{} owner"), ("W", "\\W{} owner"), ("WMC", "\\WMC{} owner"), ("DECLARED", "\\DECL{} owner"),
              ("PRIOR", "Top committer to the file, prior year"), ("LAST", "Last committer to the file"),
              ("REPOTOP", "Most active developer in the repository, prior year")]
    for k, label in labels:
        a, f = C["all"][k], C["flip"][k]
        rows.append(f"{label} & {a[0]:.3f} [{a[1]:.3f}, {a[2]:.3f}] & {f[0]:.3f} [{f[1]:.3f}, {f[2]:.3f}] \\\\")
    rows += ["\\midrule", f"Files (repositories) & {thousands(C['files'])} ({C['repos']}) & {thousands(C['flip_files'])} ({C['flip_repos']}) \\\\",
             "\\bottomrule", "\\end{tabular}"]
    (ms / "tables" / "prospective.tex").write_text("\n".join(rows) + "\n")
    rows = ["\\begin{tabular}{@{}>{\\raggedright\\arraybackslash}p{6.4cm}cc@{}}", "\\toprule", "Comparison on owner-change files & Difference [95\\% CI] & $p$ \\\\", "\\midrule"]
    t = r["rq4_tests"]["flip"]
    for k, label in (("RAW_vs_DECLARED", "\\RAW{} owner $-$ \\DECL{} owner"), ("LAST_vs_RAW", "Last committer $-$ \\RAW{} owner"),
                     ("PRIOR_vs_RAW", "Prior-year file committer $-$ \\RAW{} owner"), ("REPOTOP_vs_RAW", "Most active developer $-$ \\RAW{} owner"),
                     ("LAST_vs_DECLARED", "Last committer $-$ \\DECL{} owner")):
        v = t[k]
        rows.append(f"{label} & {signed(v['mean_diff'])} [{signed(v['mean_diff_ci'][0])}, {signed(v['mean_diff_ci'][1])}] & {fmt_p(v['p_holm'])} \\\\")
    rows += ["\\midrule", "\\multicolumn{3}{l}{\\emph{Sensitivity: \\RAW{} owner vs.\\ \\DECL{} owner}} \\\\"]
    sens = C["sensitivity"]
    snap = json.loads((analysis / "results_round3.json").read_text())["snapshot_list"]
    items = [("Without bot owners and name-matched aliases", sens["no_bot_no_alias"]),
             ("Later commits by bots removed", sens["bot_commits_removed"]),
             ("Later commits touching $>$20 files removed", sens["bulk_commits_removed"]),
             ("Ignore file at the snapshot already listed the earlier declared commits", {"files": snap["files"], "repos": snap["repos"], "RAW": snap["RAW"], "DECLARED": snap["DECLARED"], "test": {"p_holm": snap["test"]["p"]}})]
    for label, v in items:
        rows.append(f"{label} ({v['files']} files, {v['repos']} repos.) & {v['RAW'][0]:.3f} vs.\\ {v['DECLARED'][0]:.3f} & {fmt_p(v['test'].get('p_holm', v['test'].get('p')))} \\\\")
    rows += ["\\bottomrule", "\\end{tabular}"]
    (ms / "tables" / "prospective_tests.tex").write_text("\n".join(rows) + "\n")
    m["SnapFiles"] = str(snap["files"])
    m["SnapSubRepos"] = str(snap["repos"])


if __name__ == "__main__":
    main()
