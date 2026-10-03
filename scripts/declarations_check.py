"""Describe each ignore file at the analyzed revision: entries, duplicates, abbreviations.

Usage: declarations_check.py <measurement_dir> <out_csv>
"""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from measure import CLONES, git  # noqa: E402


def main():
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    rows = []
    for path in sorted(src.glob("*.json")):
        r = json.loads(path.read_text())
        if r.get("status") not in ("OK", "NO_ANCESTOR_DECLARATIONS"):
            continue
        repo = CLONES / r["full_name"].replace("/", "__")
        text = git(repo, "show", f"{r['head']}:.git-blame-ignore-revs").decode(errors="replace")
        entries = [l.split("#", 1)[0].strip() for l in text.splitlines()]
        entries = [e for e in entries if e]
        distinct = list(dict.fromkeys(e.lower() for e in entries))
        rows.append({
            "repo": r["full_name"],
            "lines": len(entries),
            "distinct": len(distinct),
            "abbreviated": sum(1 for e in distinct if re.fullmatch(r"[0-9a-f]{4,39}", e)),
            "effective": r["declaration"]["ancestors"],
            "comments": sum(1 for l in text.splitlines() if l.strip().startswith("#")),
        })
    with out.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(len(rows), "ignore files")


if __name__ == "__main__":
    main()
