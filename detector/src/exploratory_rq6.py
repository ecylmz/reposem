"""Exploratory (after the final evaluation): simple ignore lists in the RQ6 setting.

For each final repository, rebuild the touched sample and the candidate commits as lanes.py does, and
form three lists from the same candidates:
  DETECTED  the frozen detector at its threshold (as in RQ6)
  KEYWORD   the keyword baseline of RQ5 (lint, formatter, whitespace, or style group in the subject)
  SIZE      commits whose changed-line count is at or above the 99th percentile of the uniformly drawn
            unlabeled development commits (the same 1% flag rate as the detector)
Blame the touched files in RAW, DECLARED, and the three list lanes, and compute recovery and reassignment
as in RQ6. Also record how many declared commits are among the candidates and how many each list flags.
"""

from __future__ import annotations

import argparse
import csv
import json
import pickle
import subprocess
import sys
import tempfile
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from common import CONFIG, MECHANICAL_GROUPS, ROOT, clone_dir, commit_features  # noqa: E402
from measure import MAX_FILES, blame, eligible, git, text_files, touched_by  # noqa: E402
from measurement_contract import GitRunner, IdentityNormalizer  # noqa: E402

BUDGET = 20 * 60
LISTS = ("DETECTED", "KEYWORD", "SIZE")


def measure_repo(args):
    repo_name, model_dir, size_cut, out_dir = args
    target = Path(out_dir) / (repo_name.replace("/", "__") + ".json")
    if target.exists():
        return repo_name, "SKIP"
    rec = {"repo": repo_name}
    started = time.time()
    try:
        repo = clone_dir(repo_name)
        study = json.loads((ROOT / CONFIG["study_dir"] / (repo_name.replace("/", "__") + ".json")).read_text())
        head = study["head"]
        declared = set()
        with open(ROOT / CONFIG["declared_csv"]) as f:
            for row in csv.DictReader(f):
                if row["repo"] == repo_name:
                    declared.add(row["sha"])
        sizes = text_files(repo, head)
        touched = eligible(touched_by(repo, declared), sizes, "touched")[:MAX_FILES]
        rnd = eligible(sizes.keys(), sizes, "random")[:MAX_FILES]
        paths = sorted(set(touched) | set(rnd))
        cand = git(repo, "log", "--no-merges", "--format=%H", head, "--", *paths).decode().split() if paths else []
        with open(Path(model_dir) / "models.pkl", "rb") as f:
            model = pickle.load(f)["full"]
        threshold = json.loads((Path(model_dir) / "summary.json").read_text())["threshold"]
        lists = {k: [] for k in LISTS}
        feats, keep = [], []
        for sha in cand:
            try:
                fx = commit_features(repo, sha)
            except Exception:  # noqa: BLE001
                continue
            keep.append(sha)
            feats.append([fx[c] for c in model["columns"]])
            if max(fx[f"kw_{g}"] for g in MECHANICAL_GROUPS):
                lists["KEYWORD"].append(sha)
            if fx["log_lines"] >= size_cut:
                lists["SIZE"].append(sha)
        if feats:
            scores = model["model"].predict_proba(np.array(feats, float))[:, 1]
            lists["DETECTED"] = [s for s, sc in zip(keep, scores) if sc >= threshold]
        dc = declared & set(keep)
        rec.update(candidates=len(keep), declared=len(declared), declared_in_candidates=len(dc),
                   flagged={k: len(v) for k, v in lists.items()},
                   flagged_declared={k: len(set(v) & dc) for k, v in lists.items()})
        tmp = Path(tempfile.mkdtemp())
        files_ = {}
        for k, v in list(lists.items()) + [("DECLARED", sorted(declared))]:
            p = tmp / f"{k}.revs"
            p.write_text("\n".join(v) + "\n")
            files_[k] = (p, bool(v))
        identity = IdentityNormalizer(GitRunner(repo), head, scope_id=repo_name)
        deadline = time.time() + BUDGET
        out = []
        for path in touched:
            if time.time() > deadline:
                out.append({"status": "BUDGET_EXHAUSTED"})
                continue
            try:
                rows = {"RAW": blame(repo, head, path, "RAW", "")}
                for k, (p, nonempty) in files_.items():
                    rows[k] = blame(repo, head, path, "DECLARED", str(p)) if nonempty else rows["RAW"]
            except subprocess.TimeoutExpired:
                out.append({"status": "TIMEOUT"})
                continue
            except Exception as exc:  # noqa: BLE001
                out.append({"status": "BLAME_FAILED", "error": str(exc)[:200]})
                continue
            n = len(rows["RAW"])
            if n == 0 or any(len(v) != n for v in rows.values()):
                out.append({"status": "LENGTH_MISMATCH"})
                continue
            dev = {l: [None if r[4] else identity.developer_id(r[1], r[2]) for r in v] for l, v in rows.items()}
            raw_commit = [r[0] for r in rows["RAW"]]
            re_idx = [i for i in range(n) if dev["RAW"][i] is not None and dev["DECLARED"][i] is not None
                      and dev["RAW"][i] != dev["DECLARED"][i]]
            unc_idx = [i for i in range(n) if raw_commit[i] not in declared and dev["RAW"][i] is not None]
            out.append({"status": "OK", "reattributed": len(re_idx), "uncaptured": len(unc_idx),
                        "recovered": {l: sum(1 for i in re_idx if dev[l][i] == dev["DECLARED"][i]) for l in LISTS},
                        "reassigned": {l: sum(1 for i in unc_idx if dev[l][i] is not None and dev[l][i] != dev["RAW"][i])
                                       for l in LISTS}})
        rec["touched"] = out
        rec["status"] = "OK"
    except Exception as exc:  # noqa: BLE001
        rec["status"] = "REPO_FAILED"
        rec["error"] = repr(exc)[:500]
    rec["seconds"] = round(time.time() - started, 1)
    target.write_text(json.dumps(rec))
    return repo_name, rec["status"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repos", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--features", required=True, help="features-2 features.csv (for the size cut)")
    ap.add_argument("--split", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=CONFIG["workers"])
    args = ap.parse_args()
    out = Path(args.out)
    (out / "repos").mkdir(parents=True, exist_ok=True)
    f = pd.read_csv(args.features)
    sp = pd.read_csv(args.split)
    dev = set(sp.loc[sp.role == "development", "repo"])
    u = f[f.repo.isin(dev) & (f.status == "ok") & (f.label == 0)]
    size_cut = float(np.quantile(u.log_lines, 1 - CONFIG["flag_rate"]))
    repos = [l.strip() for l in open(args.repos) if l.strip()]
    st = Counter()
    with ProcessPoolExecutor(args.workers) as ex:
        for i, (name, s) in enumerate(ex.map(measure_repo, [(r, args.model, size_cut, out / "repos") for r in repos])):
            st[s] += 1
            print(f"{i + 1}/{len(repos)} {name} {s}", flush=True)
    rows = []
    for p in sorted((out / "repos").glob("*.json")):
        r = json.loads(p.read_text())
        if r["status"] != "OK":
            continue
        ok = [x for x in r["touched"] if x["status"] == "OK"]
        re_n, unc = sum(x["reattributed"] for x in ok), sum(x["uncaptured"] for x in ok)
        row = {"repo": r["repo"], "candidates": r["candidates"], "declared_in_candidates": r["declared_in_candidates"]}
        for l in LISTS:
            row[f"flagged_{l}"] = r["flagged"][l]
            row[f"flagged_declared_{l}"] = r["flagged_declared"][l]
            row[f"recovery_{l}"] = sum(x["recovered"][l] for x in ok) / re_n if re_n else np.nan
            row[f"reassign_{l}"] = sum(x["reassigned"][l] for x in ok) / unc if unc else np.nan
        rows.append(row)
    t = pd.DataFrame(rows)
    t.to_csv(out / "exploratory_rq6.csv", index=False)
    summ = {"status": dict(st), "repos": int(len(t)), "size_cut_log_lines": size_cut,
            "size_cut_lines": float(np.expm1(size_cut)),
            "mean": {c: float(t[c].mean()) for c in t.columns if c.startswith(("recovery_", "reassign_"))},
            "median_flagged": {l: float(t[f"flagged_{l}"].median()) for l in LISTS},
            "recall_in_candidates": {l: float(t[f"flagged_declared_{l}"].sum() / t.declared_in_candidates.sum()) for l in LISTS},
            "precision_vs_declared": {l: float(t[f"flagged_declared_{l}"].sum() / t[f"flagged_{l}"].sum()) for l in LISTS}}
    (out / "summary.json").write_text(json.dumps(summ, indent=2))
    print(json.dumps(summ, indent=2))


if __name__ == "__main__":
    main()
