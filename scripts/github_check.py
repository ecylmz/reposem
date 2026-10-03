"""Compare our blame lanes with the blame that GitHub reports.

For one repository, take the first touched file (in sample order) on which the
declaration re-attributes lines, ask the GitHub GraphQL API for the blame of
that file at the analyzed revision, and compare it line by line with RAW and
DECLARED.

Usage: github_check.py <owner/name> <measurement_dir> <supplement_dir> <out_dir>
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from measure import CLONES, MAX_FILES, eligible, text_files, touched_by  # noqa: E402
from supplement import porcelain  # noqa: E402

QUERY = ("query($o:String!,$n:String!,$e:String!,$p:String!){repository(owner:$o,name:$n){object(expression:$e)"
         "{... on Commit{blame(path:$p){ranges{startingLine endingLine commit{oid}}}}}}}")


def github_blame(full_name, head, path):
    owner, name = full_name.split("/", 1)
    for attempt in range(3):
        r = subprocess.run(["gh", "api", "graphql", "-f", f"query={QUERY}", "-f", f"o={owner}", "-f", f"n={name}",
                            "-f", f"e={head}", "-f", f"p={path}"], capture_output=True, text=True)
        try:
            data = json.loads(r.stdout)
            return data["data"]["repository"]["object"]["blame"]["ranges"]
        except Exception:  # noqa: BLE001
            time.sleep(10 * (attempt + 1))
    return None


def main():
    full_name, src, supp, out = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4])
    out.mkdir(parents=True, exist_ok=True)
    target = out / (full_name.replace("/", "__") + ".json")
    if target.exists():
        return
    key = full_name.replace("/", "__") + ".json"
    record = json.loads((src / key).read_text())
    if record.get("status") != "OK" or not (supp / key).exists():
        return
    sup = json.loads((supp / key).read_text())
    candidates = [a["path_hash"] for a in sup.get("a", []) if a["reattributed"] > 0]
    if not candidates:
        target.write_text(json.dumps({"full_name": full_name, "status": "NO_REATTRIBUTED_FILE"}))
        return
    repo = CLONES / key[:-5]
    head = record["head"]
    declared = [m["sha"] for m in record["declared_meta"]]
    paths = eligible(touched_by(repo, declared), text_files(repo, head), "touched")[:MAX_FILES]
    by_hash = {hashlib.sha256(p.encode()).hexdigest()[:16]: p for p in paths}
    path = by_hash[candidates[0]]
    with tempfile.NamedTemporaryFile("w", suffix=".revs", delete=False) as h:
        h.write("\n".join(declared) + "\n")
        ignore = h.name
    raw = porcelain(repo, head, path, [])
    decl = porcelain(repo, head, path, [], ignore)
    ranges = github_blame(full_name, head, path)
    if ranges is None:
        target.write_text(json.dumps({"full_name": full_name, "status": "GITHUB_FAILED"}))
        return
    gh = [None] * len(raw)
    for rg in ranges:
        for line in range(rg["startingLine"], rg["endingLine"] + 1):
            if line - 1 < len(gh):
                gh[line - 1] = rg["commit"]["oid"]
    differ = [i for i in range(len(raw)) if raw[i]["sha"] != decl[i]["sha"]]
    target.write_text(json.dumps({
        "full_name": full_name, "status": "OK", "path_hash": candidates[0], "lines": len(raw),
        "gh_eq_decl": sum(gh[i] == decl[i]["sha"] for i in range(len(raw))),
        "gh_eq_raw": sum(gh[i] == raw[i]["sha"] for i in range(len(raw))),
        "differ": len(differ),
        "differ_gh_eq_decl": sum(gh[i] == decl[i]["sha"] for i in differ),
        "differ_gh_eq_raw": sum(gh[i] == raw[i]["sha"] for i in differ),
    }))


if __name__ == "__main__":
    main()
