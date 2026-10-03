"""Sensitivity of RQ2 to the declared set and to approximate line mappings.

For the touched and random samples of one repository, compute owner change,
major-contributor change, and share distance between RAW and two variants of
DECLARED:

  FMT    the declared set restricted to formatter and whitespace commits;
  EXACT  DECLARED, but re-attributions whose mapped earlier line differs in
         letters and digits are treated as unblamable.

Usage: variants.py <owner/name> <measurement_dir> <out_dir>
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from measurement_contract import GitRunner, IdentityNormalizer  # noqa: E402
from measure import CLONES, MAX_FILES, eligible, git, text_files, touched_by  # noqa: E402
from supplement import norm_alnum, porcelain  # noqa: E402
from analyze_full import classify, n_major, top_set, tvd  # noqa: E402


def summarize(raw, other):
    return {"flip": int(top_set(raw) != top_set(other)), "major": int(n_major(raw) != n_major(other)), "tvd": tvd(raw, other)}


def main():
    full_name, src, out = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
    out.mkdir(parents=True, exist_ok=True)
    target = out / (full_name.replace("/", "__") + ".json")
    if target.exists():
        return
    record = json.loads((src / (full_name.replace("/", "__") + ".json")).read_text())
    if record.get("status") != "OK":
        return
    repo = CLONES / full_name.replace("/", "__")
    runner = GitRunner(repo)
    identity = IdentityNormalizer(runner, record["head"], scope_id=full_name)
    head = record["head"]
    declared = [m["sha"] for m in record["declared_meta"]]
    fmt = [m["sha"] for m in record["declared_meta"] if classify(m["subject"]) in ("formatter", "whitespace")]
    dset = set(declared)
    files = {}
    for name, shas in (("all", declared), ("fmt", fmt)):
        with tempfile.NamedTemporaryFile("w", suffix=".revs", delete=False) as h:
            h.write("\n".join(shas) + ("\n" if shas else ""))
            files[name] = h.name
    sizes = text_files(repo, head)
    samples = {"touched": eligible(touched_by(repo, declared), sizes, "touched")[:MAX_FILES],
               "random": eligible(sizes.keys(), sizes, "random")[:MAX_FILES]}
    rows = []
    blob_cache = {}
    for sample, paths in samples.items():
        for path in paths:
            try:
                raw = porcelain(repo, head, path, [])
                decl = porcelain(repo, head, path, [], files["all"])
                dfmt = porcelain(repo, head, path, [], files["fmt"]) if fmt else raw
            except Exception:  # noqa: BLE001
                continue
            if not raw or len(decl) != len(raw) or len(dfmt) != len(raw):
                continue
            dev = lambda r: None if r["unbl"] else identity.developer_id(r["name"], r["mail"])  # noqa: E731
            c_raw = Counter(d for d in map(dev, raw) if d)
            c_decl = Counter(d for d in map(dev, decl) if d)
            c_fmt = Counter(d for d in map(dev, dfmt) if d)
            exact = Counter()
            for r0, r1 in zip(raw, decl):
                d1 = dev(r1)
                if d1 is None:
                    continue
                if r0["sha"] in dset and r1["sha"] not in dset and d1 != dev(r0):
                    key = (r1["sha"], r1["file"])
                    if key not in blob_cache:
                        try:
                            blob_cache[key] = git(repo, "show", f"{r1['sha']}:{r1['file']}").split(b"\n")
                        except Exception:  # noqa: BLE001
                            blob_cache[key] = None
                    blob = blob_cache[key]
                    if blob is None or r1["orig"] - 1 >= len(blob) or norm_alnum(blob[r1["orig"] - 1]) != norm_alnum(r0["content"]):
                        continue  # approximate mapping: treat as unblamable
                exact[d1] += 1
            rows.append({"sample": sample, "path_hash": hashlib.sha256(path.encode()).hexdigest()[:16],
                         "ext": Path(path).suffix.lower(),
                         "decl": summarize(c_raw, c_decl), "fmt": summarize(c_raw, c_fmt), "exact": summarize(c_raw, exact)})
    target.write_text(json.dumps({"full_name": full_name, "fmt_commits": len(fmt), "declared": len(declared), "files": rows}))


if __name__ == "__main__":
    main()
