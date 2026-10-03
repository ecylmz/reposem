"""Explain owner changes in the historical sample.

For every file of the historical sample whose owner differs between RAW and
DECLARED, record whether each owner authored a declared commit and whether each
owner made any commit to the repository after the snapshot.

Usage: mechanism.py <measurement_dir> <out_csv>
"""

from __future__ import annotations

import csv
import datetime
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from measurement_contract import GitRunner, IdentityNormalizer  # noqa: E402
from measure import CLONES, git  # noqa: E402


def top(counts: dict) -> set:
    if not counts:
        return set()
    best = max(counts.values())
    return {d for d, v in counts.items() if v == best}


def authors(repo, identity, *rev_args) -> set:
    out = git(repo, "log", "--no-merges", "--format=%an%x00%ae", *rev_args).decode(errors="replace")
    return {identity.developer_id(*line.split("\0")) for line in out.splitlines() if "\0" in line}


def main():
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    rows = []
    for path in sorted(src.glob("*.json")):
        record = json.loads(path.read_text())
        if record.get("status") != "OK" or not record.get("past_commit"):
            continue
        flips = [f for f in record["past"] if f.get("status") == "OK"
                 and top(f["dev_counts"]["RAW"]) != top(f["dev_counts"]["DECLARED"])]
        if not flips:
            continue
        repo = CLONES / record["full_name"].replace("/", "__")
        identity = IdentityNormalizer(GitRunner(repo), record["head"], scope_id=record["full_name"])
        past, head = record["past_commit"], record["head"]
        active_after = authors(repo, identity, f"{past}..{head}")
        past_time = int(git(repo, "show", "-s", "--format=%ct", past).decode().strip())
        prior = {}
        out_log = git(repo, "log", "--no-merges", "--since=" + datetime.datetime.fromtimestamp(past_time - 365 * 86400, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "--format=%an%x00%ae", past)
        for line in out_log.decode(errors="replace").splitlines():
            if "\0" in line:
                d = identity.developer_id(*line.split("\0", 1))
                prior[d] = prior.get(d, 0) + 1
        declared_authors = set()
        for meta in record["declared_meta"]:
            declared_authors |= authors(repo, identity, "-1", meta["sha"])
        for f in flips:
            raw_owner, decl_owner = top(f["dev_counts"]["RAW"]), top(f["dev_counts"]["DECLARED"])
            if not raw_owner or not decl_owner:
                continue  # every line unblamable in one lane
            future = f.get("future_excl_declared", {})
            rows.append({
                "repo": record["full_name"],
                "path_hash": f["path_hash"],
                "raw_owner_declared_author": int(bool(raw_owner & declared_authors)),
                "decl_owner_declared_author": int(bool(decl_owner & declared_authors)),
                "raw_owner_active_after": int(bool(raw_owner & active_after)),
                "decl_owner_active_after": int(bool(decl_owner & active_after)),
                "raw_owner_active_before": int(any(prior.get(d, 0) for d in raw_owner)),
                "decl_owner_active_before": int(any(prior.get(d, 0) for d in decl_owner)),
                "raw_owner_prior_commits": max(prior.get(d, 0) for d in raw_owner),
                "decl_owner_prior_commits": max(prior.get(d, 0) for d in decl_owner),
                "file_future_commits": sum(future.values()),
            })
    with out.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(len(rows), "owner-change files")


if __name__ == "__main__":
    main()
