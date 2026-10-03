"""Full analysis of the measured sample.

Usage: analyze_full.py <measurement_dir> <out_dir>

Writes CSV tables and a results.json with every number the manuscript reports.
Repositories are the unit of inference: file-level values are averaged within a
repository first, and uncertainty comes from a repository bootstrap.
"""

from __future__ import annotations

import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

RNG = np.random.default_rng(20261001)
B = 5000
LANES = ("RAW", "W", "WMC", "DECLARED")
CATEGORIES = [
    ("lint", r"\blint|flake8|pylint|clippy|pyupgrade|codespell|typo|spelling|shellcheck|cppcheck|clang-tidy|"
             r"\b[EFW]\d{3}\b|\bpep ?8\b"),
    ("formatter", r"\bblack\b|prettier|clang-?format|scalafmt|\bruff\b|gofmt|rustfmt|\byapf\b|autopep8|\bisort\b|"
                  r"spotless|ktlint|eslint|stylua|swiftformat|google-java-format|dprint|\bbiome\b|fourmolu|ormolu|"
                  r"ocamlformat|phpcbf|php-cs|cargo fmt|astyle|uncrustify|rubocop|standardrb|shfmt|prettif|pgindent|"
                  r"perltidy|beautif|coding standard|\b(re)?format(s|ted|ting|ter|ters)?\b|\bfmt\b"),
    ("whitespace", r"whitespace|indent|\btabs?\b|\beol\b|line ending|crlf|newline"),
    ("migration", r"migrat|convert|port to|python 3|py3|es6|upgrade|moderniz|f-string|u-strings|namespaces|pep 604"),
    ("style", r"\bstyle\b|code style|\bcs\b|reflow|tidy|clean-?up|braces|quote|usings|consisten|harmoniz|"
              r"sort(ed)? imports|update imports|trailing comma"),
    ("move/rename", r"\bmove|\bmoved|rename|relocat|restructur|reorganiz|src layout|\bsplit"),
    ("license/header", r"license|licence|copyright|header|spdx"),
    ("refactoring", r"refactor|replace|extract|separate|simplif|typing|type annotation|annotations|\btypes?\b|rip out"),
]


def classify(subject: str) -> str:
    s = subject.lower()
    for name, pattern in CATEGORIES:
        if re.search(pattern, s):
            return name
    return "other"


def shares(counts: dict) -> dict:
    total = sum(counts.values())
    return {k: v / total for k, v in counts.items()} if total else {}


def tvd(a: dict, b: dict) -> float:
    pa, pb = shares(a), shares(b)
    return 0.5 * sum(abs(pa.get(k, 0) - pb.get(k, 0)) for k in set(pa) | set(pb))


def top_set(counts: dict) -> frozenset:
    if not counts:
        return frozenset()
    best = max(counts.values())
    return frozenset(k for k, v in counts.items() if v == best)


def owner_share(counts: dict) -> float:
    return max(shares(counts).values(), default=0.0)


def n_major(counts: dict) -> int:
    return sum(1 for v in shares(counts).values() if v >= 0.05)


def n_minor(counts: dict) -> int:
    return sum(1 for v in shares(counts).values() if v < 0.05)


def top_k_set(counts: dict, k: int = 3) -> set:
    return set(sorted(counts, key=lambda d: (-counts[d], d))[:k])


# --- file-level rows --------------------------------------------------------------

def file_row(repo: str, sample: str, f: dict) -> dict | None:
    if f.get("status") != "OK" or not f.get("lines"):
        return None
    dc = f["dev_counts"]
    row = {
        "repo": repo, "sample": sample, "lines": f["lines"],
        "capture": f["raw_declared_lines"] / f["lines"],
        "captured": f["raw_declared_lines"] > 0,
        "unblamable": f["unblamable_lines"] / f["lines"],
    }
    for lane in ("RAW", "W", "WMC"):
        key = lane.lower()
        p = f["pairs"][f"{lane}|DECLARED"]
        row[f"{key}_dis"] = p["author_diff"] / p["author_n"] if p["author_n"] else np.nan
        row[f"{key}_tvd"] = tvd(dc[lane], dc["DECLARED"])
        row[f"{key}_flip"] = float(top_set(dc[lane]) != top_set(dc["DECLARED"]))
        row[f"{key}_owner_abs"] = abs(owner_share(dc["DECLARED"]) - owner_share(dc[lane]))
        row[f"{key}_major_change"] = float(n_major(dc[lane]) != n_major(dc["DECLARED"]))
        row[f"{key}_top3_change"] = float(top_k_set(dc[lane]) != top_k_set(dc["DECLARED"]))
    row["raw_owner"] = owner_share(dc["RAW"])
    row["decl_owner"] = owner_share(dc["DECLARED"])
    row["raw_major"] = n_major(dc["RAW"])
    row["decl_major"] = n_major(dc["DECLARED"])
    row["raw_minor"] = n_minor(dc["RAW"])
    row["decl_minor"] = n_minor(dc["DECLARED"])
    return row


# --- prospective alignment ----------------------------------------------------------

def expected_discounts(scores: dict, universe: list, k: int) -> dict:
    """Tie-aware expected rank discount for each developer in the universe."""
    groups = {}
    for d in universe:
        groups.setdefault(scores.get(d, 0), []).append(d)
    result, position = {}, 1
    for score in sorted(groups, reverse=True):
        members = groups[score]
        span = range(position, position + len(members))
        disc = sum(1 / math.log2(i + 1) for i in span if i <= k) / len(members)
        for d in members:
            result[d] = disc
        position += len(members)
    return result


def ndcg(scores: dict, relevance: dict, universe: list, k: int = 5) -> float:
    disc = expected_discounts(scores, universe, k)
    dcg = sum(relevance.get(d, 0) * disc[d] for d in universe)
    ideal = sorted((relevance.get(d, 0) for d in universe), reverse=True)[:k]
    idcg = sum(r / math.log2(i + 2) for i, r in enumerate(ideal))
    return dcg / idcg if idcg else np.nan


def hit1(scores: dict, relevance: dict) -> float:
    top = top_set({d: v for d, v in scores.items() if v > 0})
    target = top_set({d: v for d, v in relevance.items() if v > 0})
    return sum(1 for d in top if d in target) / len(top) if top else 0.0


def past_rows(repo: str, record: dict, key: str) -> list[dict]:
    rows = []
    for f in record.get("past", []):
        if f.get("status") != "OK":
            continue
        dc = f["dev_counts"]
        universe = sorted(set().union(*[set(dc[lane]) for lane in LANES]))
        future = {d: n for d, n in f[key].items() if d in universe}
        total_future = sum(f[key].values())
        row = {
            "repo": repo, "path_hash": f["path_hash"], "capture": f["raw_declared_lines"] / f["lines"],
            "future_commits": total_future, "known_future": sum(future.values()),
            "flip": float(top_set(dc["RAW"]) != top_set(dc["DECLARED"])),
        }
        evaluable = sum(future.values()) > 0
        row["evaluable"] = evaluable
        for lane in LANES:
            if evaluable:
                row[f"ndcg_{lane}"] = ndcg(dc[lane], future, universe)
                row[f"hit1_{lane}"] = hit1(dc[lane], future)
            else:
                row[f"ndcg_{lane}"] = np.nan
                row[f"hit1_{lane}"] = np.nan
        rows.append(row)
    return rows


# --- inference helpers ---------------------------------------------------------------

def boot_ci(values, func=np.mean):
    values = np.asarray([v for v in values if not pd.isna(v)], dtype=float)
    if len(values) == 0:
        return (np.nan, np.nan, np.nan)
    idx = RNG.integers(0, len(values), size=(B, len(values)))
    draws = np.apply_along_axis(func, 1, values[idx])
    return (float(func(values)), float(np.percentile(draws, 2.5)), float(np.percentile(draws, 97.5)))


def paired_test(x, y):
    """Wilcoxon signed-rank on paired repository values with rank-biserial effect size."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    mask = ~(np.isnan(x) | np.isnan(y))
    d = (x - y)[mask]
    nz = d[d != 0]
    out = {"n": int(mask.sum()), "n_nonzero": int(len(nz)), "mean_diff": float(np.mean(d)) if len(d) else np.nan}
    out["mean_diff_ci"] = boot_ci(d)[1:]
    if len(nz) < 5:
        out.update(p=np.nan, rank_biserial=np.nan)
        return out
    res = stats.wilcoxon(nz)
    ranks = stats.rankdata(np.abs(nz))
    r_plus, r_minus = ranks[nz > 0].sum(), ranks[nz < 0].sum()
    out.update(p=float(res.pvalue), rank_biserial=float((r_plus - r_minus) / (r_plus + r_minus)))
    return out


def holm(pvals: dict) -> dict:
    items = sorted((p, k) for k, p in pvals.items() if not pd.isna(p))
    m, adjusted, running = len(items), {}, 0.0
    for i, (p, k) in enumerate(items):
        running = max(running, min(1.0, (m - i) * p))
        adjusted[k] = running
    return adjusted


def main():
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    records = [json.loads(p.read_text()) for p in sorted(src.glob("*.json"))]
    res = {"status_counts": dict(Counter(r.get("status") for r in records))}
    ok = [r for r in records if r.get("status") == "OK"]

    # Declarations and declared revisions.
    decl_rows, rev_rows = [], []
    for r in records:
        if r.get("status") not in ("OK", "NO_ANCESTOR_DECLARATIONS"):
            continue
        d = r["declaration"]
        decl_rows.append({
            "repo": r["full_name"], "entries": d["entries"], "resolved": d["resolved"], "invalid": d["invalid"],
            "ancestors": d["ancestors"], "commits_total": r["commits_total"],
            "history_years": (r["head_time"] - r["first_commit_time"]) / (365.25 * 86400),
            "file_age_years": (r["head_time"] - d["file_added_time"]) / (365.25 * 86400) if d["file_added_time"] else np.nan,
        })
        for m in r.get("declared_meta", []):
            match = re.search(r"(\d+) files? changed(?:, (\d+) insertions?\(\+\))?(?:, (\d+) deletions?\(-\))?", m["shortstat"])
            files_changed = int(match.group(1)) if match else 0
            ins = int(match.group(2) or 0) if match else 0
            dels = int(match.group(3) or 0) if match else 0
            rev_rows.append({
                "repo": r["full_name"], "sha": m["sha"], "category": classify(m["subject"]),
                "age_years": (r["head_time"] - m["time"]) / (365.25 * 86400),
                "files_changed": files_changed, "lines_changed": ins + dels,
            })
    decl = pd.DataFrame(decl_rows)
    revs = pd.DataFrame(rev_rows)
    decl["unresolved_share"] = 1 - decl["ancestors"] / decl["entries"].where(decl["entries"] > 0)
    decl.to_csv(out / "declarations.csv", index=False)
    revs.to_csv(out / "declared_revisions.csv", index=False)
    # Distinct entries per ignore file (duplicate lines removed), from declarations_check.py.
    chk = pd.read_csv(out / "declarations_check.csv")
    ne = chk[chk.distinct > 0]
    inactive = ne.distinct - ne.effective
    res["declarations"] = {
        "repos": int(len(chk)), "empty_files": int((chk.distinct == 0).sum()),
        "repos_with_duplicates": int((chk.lines > chk.distinct).sum()),
        "repos_with_abbreviated": int((chk.abbreviated > 0).sum()),
        "files_with_comments": float((ne.comments > 0).mean()),
        "entries_median": float(ne.distinct.median()), "entries_iqr": [float(ne.distinct.quantile(.25)), float(ne.distinct.quantile(.75))],
        "entries_max": int(ne.distinct.max()),
        "repos_with_unresolved": int((inactive > 0).sum()),
        "repos_all_unresolved": int((ne.effective == 0).sum()),
        "entries_total": int(ne.distinct.sum()), "entries_unresolved_total": int(inactive.sum()),
        "file_age_median": float(decl.file_age_years.median()),
        "history_years_median": float(decl.history_years.median()),
        "revisions": int(len(revs)),
        "revision_categories": revs.category.value_counts().to_dict(),
        "revision_categories_repo_share": (revs.groupby("category").repo.nunique() / revs.repo.nunique()).to_dict(),
        "revision_files_median": float(revs.files_changed.median()),
        "revision_lines_median": float(revs.lines_changed.median()),
        "revision_lines_p90": float(revs.lines_changed.quantile(.9)),
        "revision_age_median": float(revs.age_years.median()),
    }

    # File-level divergence for touched and random samples.
    files = pd.DataFrame([row for r in ok for sample in ("touched", "random")
                          for f in r.get(sample, []) if (row := file_row(r["full_name"], sample, f))])
    files.to_csv(out / "files.csv", index=False)
    statuses = Counter((s, f.get("status")) for r in ok for s in ("touched", "random", "past") for f in r.get(s, []))
    res["file_status"] = {f"{s}:{st}": n for (s, st), n in statuses.items()}
    metrics = ["capture", "captured", "unblamable"] + [f"{l}_{m}" for l in ("raw", "w", "wmc")
               for m in ("dis", "tvd", "flip", "owner_abs", "major_change", "top3_change")]
    res["divergence"] = {}
    repo_tables = {}
    for sample in ("touched", "random"):
        sub = files[files["sample"] == sample]
        repo = sub.groupby("repo")[metrics].mean()
        repo["files"] = sub.groupby("repo").size()
        repo["any_flip"] = sub.groupby("repo")["raw_flip"].max()
        repo_tables[sample] = repo
        repo.to_csv(out / f"repo_{sample}.csv")
        block = {"repos": int(len(repo)), "files": int(len(sub))}
        for m in metrics + ["any_flip"]:
            mean, lo, hi = boot_ci(repo[m])
            block[m] = {"mean": mean, "ci": [lo, hi], "median": float(repo[m].median())}
        block["repos_flip_share_ge_10pct"] = float((repo["raw_flip"] >= 0.10).mean())
        block["repos_tvd_ge_010"] = float((repo["raw_tvd"] >= 0.10).mean())
        res["divergence"][sample] = block

    # Files without captured lines: DECLARED equals RAW there, so any heuristic
    # difference from DECLARED comes from lines unrelated to the declarations.
    zero = files[(files["sample"] == "random") & (files.capture == 0)]
    zrepo = zero.groupby("repo")[["w_dis", "wmc_dis", "w_flip", "wmc_flip", "raw_dis"]].mean()
    res["uncaptured_random"] = {"files": int(len(zero)), "repos": int(len(zrepo))}
    for c in zrepo.columns:
        res["uncaptured_random"][c] = boot_ci(zrepo[c])
    # Share of captured lines that Git could not map to an earlier commit.
    tf = files[(files["sample"] == "touched") & files.captured].copy()
    tf["captured_lines"] = tf.capture * tf.lines
    tf["unblamable_lines"] = tf.unblamable * tf.lines
    g = tf.groupby("repo")[["captured_lines", "unblamable_lines"]].sum()
    res["unblamable_of_captured"] = boot_ci(g.unblamable_lines / g.captured_lines)

    # Heuristic adequacy: paired tests on repository means (touched sample).
    rt = repo_tables["touched"]
    tests = {}
    for m in ("tvd", "dis", "flip"):
        tests[f"W_vs_RAW_{m}"] = paired_test(rt[f"w_{m}"], rt[f"raw_{m}"])
        tests[f"WMC_vs_RAW_{m}"] = paired_test(rt[f"wmc_{m}"], rt[f"raw_{m}"])
        tests[f"WMC_vs_W_{m}"] = paired_test(rt[f"wmc_{m}"], rt[f"w_{m}"])
    # The Holm family is the six reported comparisons with RAW.
    adj = holm({k: v["p"] for k, v in tests.items() if not k.startswith("WMC_vs_W")})
    for k in tests:
        tests[k]["p_holm"] = adj.get(k, np.nan)
    res["heuristics"] = tests
    res["heuristics_repo_share_w_closer"] = float((rt["w_tvd"] < rt["raw_tvd"]).mean())
    res["heuristics_repo_share_wmc_closer"] = float((rt["wmc_tvd"] < rt["raw_tvd"]).mean())
    res["heuristics_repo_share_w_close_005"] = float((rt["w_tvd"] < 0.05).mean())

    # Persistence: share of declared revisions' lines still visible, by age.
    cap_rows = []
    for r in ok:
        measured = [f for f in r.get("touched", []) if f.get("status") == "OK"]
        complete = r.get("touched_eligible", 0) == len(measured) and len(measured) > 0
        touched_lines = Counter()
        for f in r.get("touched", []):
            if f.get("status") == "OK":
                touched_lines.update(f.get("raw_declared_by_rev", {}))
        for m in r.get("declared_meta", []):
            cap_rows.append({"repo": r["full_name"], "sha": m["sha"], "complete": complete,
                             "age_years": (r["head_time"] - m["time"]) / (365.25 * 86400),
                             "lines_visible": touched_lines.get(m["sha"], 0)})
    cap_all = pd.DataFrame(cap_rows)
    # Only repositories whose every touched file was measured give an exact count.
    cap = cap_all[cap_all.complete].copy()
    res["persistence_complete_repos"] = int(cap.repo.nunique())
    cap["visible"] = cap.lines_visible > 0
    cap["age_bin"] = pd.cut(cap.age_years, [0, 1, 2, 3, 5, 100], labels=["<1", "1-2", "2-3", "3-5", ">5"], right=False)
    cap.to_csv(out / "persistence.csv", index=False)
    per = cap.groupby("age_bin", observed=False).agg(revisions=("sha", "size"), visible=("visible", "mean"),
                                                     median_lines=("lines_visible", "median")).reset_index()
    per.to_csv(out / "persistence_by_age.csv", index=False)
    res["persistence"] = {
        "revisions": int(len(cap)), "visible_share": float(cap.visible.mean()),
        "by_age": per.astype({"age_bin": str}).to_dict(orient="records"),
        "spearman_age_lines": [float(x) for x in stats.spearmanr(cap.age_years, cap.lines_visible)],
    }
    # Repository-level visibility by age, so that repositories weigh equally.
    cap["old"] = cap.age_years >= 3
    vis_repo = cap.groupby(["repo", "old"]).visible.mean().unstack()
    res["persistence_repo"] = {
        "young_visible": boot_ci(vis_repo.get(False, pd.Series(dtype=float))),
        "old_visible": boot_ci(vis_repo.get(True, pd.Series(dtype=float))),
        "repos_old": int(vis_repo.get(True, pd.Series(dtype=float)).notna().sum()),
    }
    res["persistence_repo"]["visible_any"] = boot_ci(cap.groupby("repo").visible.mean())
    rt_caps = rt[["capture", "raw_tvd"]].join(decl.set_index("repo")[["entries", "file_age_years"]])
    res["capture_tvd_spearman"] = [float(x) for x in stats.spearmanr(rt_caps.capture, rt_caps.raw_tvd)]

    # Prospective alignment at the one-year-earlier snapshot.
    res["prospective"] = {}
    for key, label in (("future_excl_declared", "primary"), ("future", "all_commits")):
        prow = pd.DataFrame([row for r in ok for row in past_rows(r["full_name"], r, key)])
        if prow.empty:
            continue
        prow.to_csv(out / f"prospective_files_{label}.csv", index=False)
        ev = prow[prow.evaluable]
        cols = [f"{m}_{l}" for m in ("ndcg", "hit1") for l in LANES]
        repo = ev.groupby("repo")[cols].mean()
        repo["files"] = ev.groupby("repo").size()
        repo.to_csv(out / f"prospective_repo_{label}.csv")
        block = {"files_measured": int(len(prow)), "files_evaluable": int(len(ev)), "repos": int(len(repo)),
                 "repos_measured": int(prow.repo.nunique())}
        for c in cols:
            mean, lo, hi = boot_ci(repo[c])
            block[c] = {"mean": mean, "ci": [lo, hi]}
        ptests = {}
        for m in ("ndcg", "hit1"):
            for lane in ("RAW", "W", "WMC"):
                ptests[f"{m}_DECLARED_vs_{lane}"] = paired_test(repo[f"{m}_DECLARED"], repo[f"{m}_{lane}"])
        padj = holm({k: v["p"] for k, v in ptests.items()})
        for k in ptests:
            ptests[k]["p_holm"] = padj.get(k, np.nan)
        block["tests"] = ptests
        # Files where the owner flips: the decision-relevant subset.
        flip = ev[ev.flip == 1]
        block["flip_files"] = int(len(flip))
        block["flip_repos"] = int(flip.repo.nunique())
        if len(flip):
            frepo = flip.groupby("repo")[cols].mean()
            for c in cols:
                mean, lo, hi = boot_ci(frepo[c])
                block[f"flip_{c}"] = {"mean": mean, "ci": [lo, hi]}
            block["flip_tests"] = {
                "hit1": paired_test(frepo["hit1_DECLARED"], frepo["hit1_RAW"]),
                "ndcg": paired_test(frepo["ndcg_DECLARED"], frepo["ndcg_RAW"]),
            }
            fadj = holm({k: v["p"] for k, v in block["flip_tests"].items()})
            for k in block["flip_tests"]:
                block["flip_tests"][k]["p_holm"] = fadj.get(k, np.nan)
            flip[["repo", "path_hash"]].to_csv(out / f"flip_files_{label}.csv", index=False)
            # File-level counts on flip files: which lane's owner made more future commits.
            block["flip_file_hit1_declared_better"] = int((flip.hit1_DECLARED > flip.hit1_RAW).sum())
            block["flip_file_hit1_raw_better"] = int((flip.hit1_RAW > flip.hit1_DECLARED).sum())
            block["flip_file_hit1_tie"] = int((flip.hit1_RAW == flip.hit1_DECLARED).sum())
        res["prospective"][label] = block

    # Category-level divergence: repositories whose declared revisions are mostly formatter runs.
    if not revs.empty:
        dominant = revs.groupby("repo").category.agg(lambda s: s.value_counts().index[0])
        rt2 = rt.join(dominant.rename("dominant_category"))
        res["divergence_by_category"] = rt2.groupby("dominant_category").agg(
            repos=("raw_tvd", "size"), raw_tvd=("raw_tvd", "mean"), w_tvd=("w_tvd", "mean"),
            raw_flip=("raw_flip", "mean")).round(4).to_dict(orient="index")

    # Mechanism behind owner changes in the historical sample.
    mech_path = out / "mechanism.csv"
    if mech_path.exists():
        mech = pd.read_csv(mech_path)
        # Same population as the RQ4 owner-change analysis: evaluable owner-change files.
        keep = pd.read_csv(out / "flip_files_primary.csv")
        mech = mech.merge(keep, on=["repo", "path_hash"])
        cols = ["raw_owner_declared_author", "decl_owner_declared_author", "raw_owner_active_after",
                "decl_owner_active_after", "raw_owner_active_before", "decl_owner_active_before"]
        mrepo = mech.groupby("repo")[cols].mean()
        res["mechanism"] = {"files": int(len(mech)), "repos": int(len(mrepo))}
        for c in cols:
            res["mechanism"][c] = boot_ci(mrepo[c])
        res["mechanism"]["active_test"] = paired_test(mrepo["raw_owner_active_after"], mrepo["decl_owner_active_after"])
        res["mechanism"]["prior_commits_median_raw"] = float(mech.raw_owner_prior_commits.median())
        res["mechanism"]["prior_commits_median_decl"] = float(mech.decl_owner_prior_commits.median())

    (out / "results.json").write_text(json.dumps(res, indent=2, default=float))
    print(json.dumps(res, indent=1, default=float)[:6000])


if __name__ == "__main__":
    main()
