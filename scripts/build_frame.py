"""Build the sampling frame: popular GitHub repositories that keep a root
`.git-blame-ignore-revs` file on the default branch.

Step 1 enumerates non-fork, non-archived repositories with at least MIN_STARS
stars through the repository search API, splitting star ranges until every
query returns fewer than 1000 results.
Step 2 checks file presence for every repository through GraphQL.

Step 3 fetches commit and contributor counts for repositories with the file.
Step 4 applies the inclusion criteria and writes frame.json and study_repos.txt.

Usage: build_frame.py enumerate | check | meta | select
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data"
REPOS = DATA / "frame_repos.jsonl"
PRESENCE = DATA / "frame_presence.jsonl"
MIN_STARS = 5000
MAX_STARS = 1_000_000


def gh(*args: str) -> dict:
    for attempt in range(6):
        result = subprocess.run(["gh", "api", *args], capture_output=True, text=True)
        if result.returncode == 0:
            return json.loads(result.stdout)
        if "rate limit" in (result.stderr + result.stdout).lower() or "secondary" in result.stderr.lower():
            time.sleep(60)
            continue
        time.sleep(5 * (attempt + 1))
    raise RuntimeError(result.stderr[:300])


def search(query: str, page: int) -> dict:
    data = gh("-X", "GET", "search/repositories", "-f", f"q={query}", "-f", "per_page=100", "-f", f"page={page}")
    time.sleep(2.1)  # 30 search requests per minute
    return data


def enumerate_repos():
    seen = set()
    if REPOS.exists():
        seen = {json.loads(l)["full_name"] for l in REPOS.open()}
    done_ranges = DATA / "frame_ranges_done.txt"
    done = set(done_ranges.read_text().split()) if done_ranges.exists() else set()
    stack = [(MIN_STARS, MAX_STARS)]
    with REPOS.open("a") as out, done_ranges.open("a") as log:
        while stack:
            lo, hi = stack.pop()
            key = f"{lo}..{hi}"
            if key in done:
                continue
            query = f"stars:{lo}..{hi} fork:false archived:false"
            first = search(query, 1)
            total = first["total_count"]
            if total >= 1000 and hi > lo:
                mid = (lo + hi) // 2
                stack += [(lo, mid), (mid + 1, hi)]
                continue
            items = first["items"]
            for page in range(2, min(10, (total + 99) // 100) + 1):
                items += search(query, page)["items"]
            for item in items:
                if item["full_name"] in seen:
                    continue
                seen.add(item["full_name"])
                out.write(json.dumps({
                    "full_name": item["full_name"],
                    "stars": item["stargazers_count"],
                    "size_kb": item["size"],
                    "language": item["language"],
                    "created_at": item["created_at"],
                    "pushed_at": item["pushed_at"],
                    "default_branch": item["default_branch"],
                }) + "\n")
            out.flush()
            log.write(key + "\n")
            log.flush()
            print(key, total, len(seen), file=sys.stderr, flush=True)


def check_presence():
    names = [json.loads(l)["full_name"] for l in REPOS.open()]
    done = set()
    if PRESENCE.exists():
        done = {json.loads(l)["full_name"] for l in PRESENCE.open()}
    todo = [n for n in dict.fromkeys(names) if n not in done]
    with PRESENCE.open("a") as out:
        for start in range(0, len(todo), 50):
            batch = todo[start:start + 50]
            parts = []
            for i, name in enumerate(batch):
                owner, repo = name.split("/", 1)
                parts.append(
                    f"r{i}: repository(owner: {json.dumps(owner)}, name: {json.dumps(repo)}) {{"
                    f" isFork isArchived"
                    f" file: object(expression: \"HEAD:.git-blame-ignore-revs\") {{ ... on Blob {{ byteSize }} }} }}"
                )
            query = "query {" + " ".join(parts) + "}"
            result = subprocess.run(["gh", "api", "graphql", "-f", f"query={query}"], capture_output=True, text=True)
            data = (json.loads(result.stdout or "{}").get("data")) or {}
            if not data and result.returncode != 0:
                time.sleep(60)
                continue
            for i, name in enumerate(batch):
                node = data.get(f"r{i}")
                record = {"full_name": name, "resolved": node is not None}
                if node is None:
                    continue  # retried on the next pass
                record.update({
                    "has_file": node["file"] is not None,
                    "file_bytes": (node["file"] or {}).get("byteSize"),
                    "is_fork": node["isFork"],
                    "is_archived": node["isArchived"],
                })
                out.write(json.dumps(record) + "\n")
            out.flush()
            print(start + len(batch), len(todo), file=sys.stderr, flush=True)


def with_file() -> list[str]:
    repos = {json.loads(l)["full_name"]: json.loads(l) for l in REPOS.open()}
    presence = {json.loads(l)["full_name"]: json.loads(l) for l in PRESENCE.open()}
    return [n for n, r in repos.items() if r["stars"] >= MIN_STARS and presence.get(n, {}).get("has_file")
            and not presence[n]["is_fork"] and not presence[n]["is_archived"]]


def fetch_meta():
    """Commit count of the default branch and GitHub's mentionable users per repository."""
    names = with_file()
    meta, todo = {}, list(names)
    for _ in range(5):
        for start in range(0, len(todo), 10):
            batch = todo[start:start + 10]
            parts = [f'r{i}: repository(owner: {json.dumps(n.split("/")[0])}, name: {json.dumps(n.split("/")[1])}) {{'
                     f' mentionableUsers {{ totalCount }} defaultBranchRef {{ target {{ ... on Commit {{ history {{ totalCount }} }} }} }} }}'
                     for i, n in enumerate(batch)]
            result = subprocess.run(["gh", "api", "graphql", "-f", "query={" + " ".join(parts) + "}"], capture_output=True, text=True)
            data = (json.loads(result.stdout or "{}").get("data")) or {}
            for i, n in enumerate(batch):
                node = data.get(f"r{i}")
                if node:
                    meta[n] = {"contributors": node["mentionableUsers"]["totalCount"],
                               "commits": node["defaultBranchRef"]["target"]["history"]["totalCount"]}
        todo = [n for n in names if n not in meta]
        if not todo:
            break
        time.sleep(5)
    repos = {json.loads(l)["full_name"]: json.loads(l) for l in REPOS.open()}
    (DATA / "frame_with_file_meta.json").write_text(json.dumps([{**repos[n], **meta.get(n, {})} for n in names]))


def select():
    """Apply the inclusion criteria: >= 200 commits, >= 10 mentionable users, <= 2.5 GB."""
    repos = [json.loads(l) for l in REPOS.open()]
    meta = json.loads((DATA / "frame_with_file_meta.json").read_text())
    included = [m for m in meta if m.get("commits", 0) >= 200 and m.get("contributors", 0) >= 10 and m["size_kb"] <= 2_500_000]
    frame = {"date": "1~October~2026", "frame_repos": sum(r["stars"] >= MIN_STARS for r in repos), "with_file": len(meta),
             # Reasons are counted in order, so a repository is excluded for one reason only.
             "excluded_size": sum(m["size_kb"] > 2_500_000 for m in meta),
             "excluded_commits": sum(m["size_kb"] <= 2_500_000 and m.get("commits", 0) < 200 for m in meta),
             "excluded_contributors": sum(m["size_kb"] <= 2_500_000 and m.get("commits", 0) >= 200
                                          and m.get("contributors", 0) < 10 for m in meta),
             "included": len(included)}
    (DATA / "frame.json").write_text(json.dumps(frame))
    (DATA / "study_repos.txt").write_text("\n".join(m["full_name"] for m in sorted(included, key=lambda m: m["size_kb"])) + "\n")
    print(frame)


if __name__ == "__main__":
    {"enumerate": enumerate_repos, "check": check_presence, "meta": fetch_meta, "select": select}[sys.argv[1]]()
