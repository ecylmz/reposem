"""RQ7 sample: popular repositories of the v1.0.0 frame that keep no root ignore file.

Candidates: frame repositories with at least 5,000 stars, not forks, not archived, at most
2.5 GB, whose presence check of 2026-10-01 found no root .git-blame-ignore-revs file.
A seeded shuffle orders them. Metadata come from the GitHub GraphQL API in that order
until the sample holds N repositories with at least 200 commits and at least 10
mentionable users (the v1.0.0 inclusion criteria). Each sampled repository is cloned
(bare) next to the v1.0.0 clones.
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
import time
from pathlib import Path

from common import CONFIG, GIT, ROOT, clone_dir, seed_for


def meta_batch(names):
    parts = [f'r{i}: repository(owner: {json.dumps(n.split("/")[0])}, name: {json.dumps(n.split("/")[1])}) {{'
             f' mentionableUsers {{ totalCount }} defaultBranchRef {{ target {{ ... on Commit {{ history {{ totalCount }} }} }} }} }}'
             for i, n in enumerate(names)]
    res = subprocess.run(["gh", "api", "graphql", "-f", "query={" + " ".join(parts) + "}"], capture_output=True, text=True)
    data = (json.loads(res.stdout or "{}").get("data")) or {}
    out = {}
    for i, n in enumerate(names):
        node = data.get(f"r{i}")
        if node and node.get("defaultBranchRef"):
            out[n] = {"contributors": node["mentionableUsers"]["totalCount"],
                      "commits": node["defaultBranchRef"]["target"]["history"]["totalCount"]}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    frame = {json.loads(l)["full_name"]: json.loads(l) for l in open(ROOT / "data/frame_repos.jsonl")}
    presence = [json.loads(l) for l in open(ROOT / "data/frame_presence.jsonl")]
    cands = sorted(p["full_name"] for p in presence
                   if p.get("resolved") and p.get("has_file") is False and not p.get("is_fork") and not p.get("is_archived")
                   and p["full_name"] in frame and frame[p["full_name"]]["stars"] >= 5000
                   and frame[p["full_name"]]["size_kb"] <= 2_500_000)
    random.Random(seed_for("rq7")).shuffle(cands)
    counts = {"candidates": len(cands), "checked": 0, "no_metadata": 0, "excluded_commits": 0,
              "excluded_contributors": 0, "clone_failed": [], "sampled": 0}
    sample, meta = [], {}
    i = 0
    while len(sample) < CONFIG["rq7_sample"] and i < len(cands):
        batch = cands[i:i + 10]
        i += 10
        m = meta_batch(batch)
        for n in batch:
            if len(sample) >= CONFIG["rq7_sample"]:
                break
            counts["checked"] += 1
            if n not in m:
                counts["no_metadata"] += 1
                continue
            meta[n] = m[n]
            if m[n]["commits"] < 200:
                counts["excluded_commits"] += 1
                continue
            if m[n]["contributors"] < 10:
                counts["excluded_contributors"] += 1
                continue
            target = clone_dir(n)
            if not (target / "HEAD").exists():
                try:
                    subprocess.run([GIT, "clone", "--bare", "--quiet", f"https://github.com/{n}.git", str(target)],
                                   check=True, timeout=3600, capture_output=True)
                except Exception as exc:  # noqa: BLE001
                    counts["clone_failed"].append([n, repr(exc)[:200]])
                    continue
            sample.append(n)
            print(len(sample), n, flush=True)
        time.sleep(1)
    counts["sampled"] = len(sample)
    (out / "rq7_repos.txt").write_text("\n".join(sample) + "\n")
    (out / "rq7_meta.json").write_text(json.dumps({n: {**frame[n], **meta[n]} for n in sample}, indent=1))
    (out / "counts.json").write_text(json.dumps(counts, indent=2))
    print(json.dumps({k: v if not isinstance(v, list) else len(v) for k, v in counts.items()}))


if __name__ == "__main__":
    main()
