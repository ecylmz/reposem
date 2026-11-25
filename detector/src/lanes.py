"""Blame lanes with the detected ignore list (RQ6 and RQ7).

For each repository: rebuild the v1.0.0 file samples (touched and random, same salts),
score every non-merge commit that touched a sampled file with the frozen detector,
write the flagged commits to an ignore file (lane DETECTED), and blame each sampled file
in the lanes RAW, W, WMC, DECLARED (when a declaration exists), and DETECTED.

Usage: lanes.py --mode rq6|rq7 --repos FILE --model DIR --out DIR
One JSON file per repository is written to --out as soon as it finishes.
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

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from common import CONFIG, ROOT, clone_dir, commit_features  # noqa: E402
from measure import MAX_FILES, blame, eligible, git, text_files, touched_by  # noqa: E402
from measurement_contract import GitRunner, IdentityNormalizer  # noqa: E402

BUDGET = 20 * 60  # seconds of blame work per sample and repository


def owners(devs):
    c = Counter(d for d in devs if d is not None)
    if not c:
        return frozenset()
    top = max(c.values())
    return frozenset(d for d, n in c.items() if n == top)


def shares(devs):
    c = Counter(d for d in devs if d is not None)
    n = sum(c.values())
    return {d: k / n for d, k in c.items()} if n else {}


def tvd(a, b):
    keys = set(a) | set(b)
    return 0.5 * sum(abs(a.get(k, 0) - b.get(k, 0)) for k in keys)


def measure_repo(args):
    repo_name, mode, model_dir, out_dir = args
    target = Path(out_dir) / (repo_name.replace("/", "__") + ".json")
    if target.exists():
        return repo_name, "SKIP"
    started = time.time()
    rec = {"repo": repo_name, "mode": mode}
    try:
        repo = clone_dir(repo_name)
        if mode == "rq6":
            study = json.loads((ROOT / CONFIG["study_dir"] / (repo_name.replace("/", "__") + ".json")).read_text())
            head = study["head"]
            declared = set()
            with open(ROOT / CONFIG["declared_csv"]) as f:
                for row in csv.DictReader(f):
                    if row["repo"] == repo_name:
                        declared.add(row["sha"])
        else:
            # The default-branch revision on the frame date, as in v1.0.0.
            head = git(repo, "rev-list", "-1", "--first-parent", "--before=2026-10-01T23:59:59Z", "HEAD").decode().strip()
            declared = set()
            try:
                git(repo, "cat-file", "-e", f"{head}:.git-blame-ignore-revs")
                rec["ignore_file_at_head"] = True
            except Exception:  # noqa: BLE001 - the expected case
                rec["ignore_file_at_head"] = False
        rec["head"] = head
        sizes = text_files(repo, head)
        samples = {"random": eligible(sizes.keys(), sizes, "random")[:MAX_FILES]}
        if declared:
            samples["touched"] = eligible(touched_by(repo, declared), sizes, "touched")[:MAX_FILES]
        paths = sorted({p for v in samples.values() for p in v})
        cand = git(repo, "log", "--no-merges", "--format=%H", head, "--", *paths).decode().split() if paths else []
        with open(Path(model_dir) / "models.pkl", "rb") as f:
            model = pickle.load(f)["full"]
        threshold = json.loads((Path(model_dir) / "summary.json").read_text())["threshold"]
        feats, failed = [], 0
        keep = []
        for sha in cand:
            try:
                fx = commit_features(repo, sha)
                feats.append([fx[c] for c in model["columns"]])
                keep.append(sha)
            except Exception:  # noqa: BLE001
                failed += 1
        scores = model["model"].predict_proba(np.array(feats, float))[:, 1] if feats else np.array([])
        detected = [s for s, sc in zip(keep, scores) if sc >= threshold]
        rec.update(candidates=len(cand), scored=len(keep), score_failed=failed, detected=len(detected),
                   detected_declared=len(set(detected) & declared), declared=len(declared))
        tmpdir = Path(tempfile.mkdtemp())
        det_file = tmpdir / "detected.revs"
        det_file.write_text("\n".join(detected) + "\n")
        decl_file = tmpdir / "declared.revs"
        decl_file.write_text("\n".join(sorted(declared)) + "\n")
        runner = GitRunner(repo)
        identity = IdentityNormalizer(runner, head, scope_id=repo_name)
        lanes = ["RAW", "W", "WMC", "DETECTED"] + (["DECLARED"] if declared else [])
        rec["samples"] = {}
        for sample, plist in samples.items():
            deadline = time.time() + BUDGET
            files = []
            for path in plist:
                entry = {"status": "OK"}
                if time.time() > deadline:
                    files.append({"status": "BUDGET_EXHAUSTED"})
                    continue
                try:
                    rows = {}
                    for lane in lanes:
                        if lane == "DETECTED":
                            rows[lane] = blame(repo, head, path, "DECLARED", str(det_file)) if detected else blame(repo, head, path, "RAW", "")
                        elif lane == "DECLARED":
                            rows[lane] = blame(repo, head, path, "DECLARED", str(decl_file))
                        else:
                            rows[lane] = blame(repo, head, path, lane, "")
                except subprocess.TimeoutExpired:
                    files.append({"status": "TIMEOUT"})
                    continue
                except Exception as exc:  # noqa: BLE001
                    files.append({"status": "BLAME_FAILED", "error": str(exc)[:200]})
                    continue
                n = len(rows["RAW"])
                if n == 0 or any(len(v) != n for v in rows.values()):
                    files.append({"status": "LENGTH_MISMATCH"})
                    continue
                dev = {l: [None if r[4] else identity.developer_id(r[1], r[2]) for r in v] for l, v in rows.items()}
                raw_commit = [r[0] for r in rows["RAW"]]
                entry["lines"] = n
                entry["owner_change"] = {}
                entry["tvd"] = {}
                ref_lanes = ["RAW"] + (["DECLARED"] if declared else [])
                for ref in ref_lanes:
                    for lane in lanes:
                        if lane == ref:
                            continue
                        entry["owner_change"][f"{lane}|{ref}"] = int(owners(dev[lane]) != owners(dev[ref]))
                        entry["tvd"][f"{lane}|{ref}"] = tvd(shares(dev[lane]), shares(dev[ref]))
                if declared:
                    re_idx = [i for i in range(n) if dev["RAW"][i] is not None and dev["DECLARED"][i] is not None
                              and dev["RAW"][i] != dev["DECLARED"][i]]
                    unc_idx = [i for i in range(n) if raw_commit[i] not in declared and dev["RAW"][i] is not None]
                    entry["reattributed"] = len(re_idx)
                    entry["uncaptured"] = len(unc_idx)
                    entry["recovered"] = {l: sum(1 for i in re_idx if dev[l][i] == dev["DECLARED"][i])
                                          for l in ("W", "WMC", "DETECTED")}
                    entry["reassigned"] = {l: sum(1 for i in unc_idx if dev[l][i] is not None and dev[l][i] != dev["RAW"][i])
                                           for l in ("W", "WMC", "DETECTED", "DECLARED")}
                files.append(entry)
            rec["samples"][sample] = files
        rec["status"] = "OK"
    except Exception as exc:  # noqa: BLE001
        rec["status"] = "REPO_FAILED"
        rec["error"] = repr(exc)[:500]
    rec["seconds"] = round(time.time() - started, 1)
    target.write_text(json.dumps(rec))
    return repo_name, rec["status"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["rq6", "rq7"], required=True)
    ap.add_argument("--repos", required=True, help="text file with one owner/name per line")
    ap.add_argument("--model", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=CONFIG["workers"])
    args = ap.parse_args()
    Path(args.out).mkdir(parents=True, exist_ok=True)
    repos = [l.strip() for l in open(args.repos) if l.strip()]
    status = Counter()
    with ProcessPoolExecutor(args.workers) as ex:
        for i, (name, st) in enumerate(ex.map(measure_repo, [(r, args.mode, args.model, args.out) for r in repos])):
            status[st] += 1
            print(f"{i + 1}/{len(repos)} {name} {st}", flush=True)
    print(json.dumps(status))


if __name__ == "__main__":
    main()
