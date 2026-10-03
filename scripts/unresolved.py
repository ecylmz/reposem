"""Classify ignore-file entries that do not resolve to a default-branch commit.

For each such entry, ask GitHub whether the commit exists in the repository's
network (for example as a pull-request commit) and, if so, whether it is
associated with a merged pull request.

Usage: unresolved.py <measurement_dir> <out_csv>
"""

from __future__ import annotations

import csv
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from measure import CLONES, git  # noqa: E402


def gh(path: str):
    result = subprocess.run(["gh", "api", "-H", "Accept: application/vnd.github+json", path],
                            capture_output=True, text=True)
    if result.returncode != 0:
        return None
    return json.loads(result.stdout)


def main():
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    rows = []
    for path in sorted(src.glob("*.json")):
        record = json.loads(path.read_text())
        if record.get("status") not in ("OK", "NO_ANCESTOR_DECLARATIONS"):
            continue
        repo = CLONES / record["full_name"].replace("/", "__")
        text = git(repo, "show", f"{record['head']}:.git-blame-ignore-revs").decode(errors="replace")
        declared = {m["sha"] for m in record.get("declared_meta", [])}
        for raw in text.splitlines():
            entry = raw.split("#", 1)[0].strip()
            if not entry:
                continue
            row = {"repo": record["full_name"], "entry": entry[:64]}
            if not re.fullmatch(r"[0-9a-fA-F]{7,40}", entry):
                row["kind"] = "MALFORMED"
            else:
                resolved = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", entry + "^{commit}"],
                                          capture_output=True, text=True).stdout.strip()
                if resolved in declared:
                    continue
                if resolved:
                    row["kind"] = "LOCAL_NOT_ON_DEFAULT_BRANCH"
                else:
                    commit = gh(f"repos/{record['full_name']}/commits/{entry}")
                    if commit is None:
                        row["kind"] = "NOT_FOUND_ON_GITHUB"
                    else:
                        pulls = gh(f"repos/{record['full_name']}/commits/{commit['sha']}/pulls") or []
                        merged = [p for p in pulls if p.get("merged_at")]
                        row["kind"] = "PR_COMMIT_SQUASHED_OR_REBASED" if merged else "UNMERGED_COMMIT"
                        row["subject"] = commit["commit"]["message"].splitlines()[0][:120]
            rows.append(row)
    with out.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["repo", "entry", "kind", "subject"])
        writer.writeheader()
        writer.writerows(rows)
    kinds = {}
    for r in rows:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
    print(len(rows), kinds)


if __name__ == "__main__":
    main()
