"""Two sensitivity analyses requested in review.

  large  touched files above the 5,000-line limit (up to 5 per repository):
         RAW and DECLARED, owner change, major-contributor change, share distance;
  c3     for a deterministic subsample of repositories, the touched sample under
         -w -M -C (WMC) and -w -M -C -C -C (WMC3): owner change and share
         distance relative to DECLARED, recovery of re-attributed lines, and
         reassignment of uncaptured lines.

Usage: extra_lanes.py <owner/name> <measurement_dir> <out_dir>
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from measurement_contract import GitRunner, IdentityNormalizer  # noqa: E402
from measure import CLONES, MAX_FILES, SKIP_NAME, eligible, text_files, touched_by  # noqa: E402
from supplement import porcelain  # noqa: E402
from analyze_full import n_major, top_set, tvd  # noqa: E402

LARGE_FILES = 5
C3_SHARE = 0.25  # share of repositories in the -C -C -C subsample


def in_c3_subsample(full_name: str) -> bool:
    return int(hashlib.sha256(("c3" + full_name).encode()).hexdigest(), 16) % 1000 < C3_SHARE * 1000


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
    identity = IdentityNormalizer(GitRunner(repo), record["head"], scope_id=full_name)
    head = record["head"]
    declared = [m["sha"] for m in record["declared_meta"]]
    dset = set(declared)
    with tempfile.NamedTemporaryFile("w", suffix=".revs", delete=False) as h:
        h.write("\n".join(declared) + "\n")
        ignore = h.name
    sizes = text_files(repo, head)
    touched = touched_by(repo, declared)
    dev = lambda r: None if r["unbl"] else identity.developer_id(r["name"], r["mail"])  # noqa: E731
    result = {"full_name": full_name, "large": [], "c3": None}

    # Large files.
    large = [p for p in touched if p in sizes and sizes[p] > 5000 and not SKIP_NAME.search(p) and p != ".git-blame-ignore-revs"]
    large.sort(key=lambda p: hashlib.sha256(("large" + p).encode()).hexdigest())
    for path in large[:LARGE_FILES]:
        try:
            raw = porcelain(repo, head, path, [])
            decl = porcelain(repo, head, path, [], ignore)
        except Exception:  # noqa: BLE001
            continue
        if not raw or len(raw) != len(decl):
            continue
        a = Counter(d for d in map(dev, raw) if d)
        b = Counter(d for d in map(dev, decl) if d)
        result["large"].append({"lines": len(raw), "flip": int(top_set(a) != top_set(b)),
                                "major": int(n_major(a) != n_major(b)), "tvd": tvd(a, b)})

    # Repeated -C on a subsample.
    if in_c3_subsample(full_name):
        rows = []
        for path in eligible(touched, sizes, "touched")[:MAX_FILES]:
            try:
                raw = porcelain(repo, head, path, [])
                decl = porcelain(repo, head, path, [], ignore)
                wmc = porcelain(repo, head, path, ["-w", "-M", "-C"])
                wmc3 = porcelain(repo, head, path, ["-w", "-M", "-C", "-C", "-C"])
            except Exception:  # noqa: BLE001
                rows.append({"status": "FAILED"})
                continue
            n = len(raw)
            if not n or any(len(x) != n for x in (decl, wmc, wmc3)):
                continue
            d = {k: [dev(r) for r in v] for k, v in (("raw", raw), ("decl", decl), ("wmc", wmc), ("wmc3", wmc3))}
            counts = {k: Counter(x for x in v if x) for k, v in d.items()}
            captured = [raw[i]["sha"] in dset for i in range(n)]
            reattr = [i for i in range(n) if captured[i] and d["decl"][i] is not None and d["decl"][i] != d["raw"][i]]
            unc = [i for i in range(n) if not captured[i]]
            row = {"status": "OK", "reattributed": len(reattr), "uncaptured": len(unc)}
            for k in ("wmc", "wmc3"):
                row[f"{k}_flip"] = int(top_set(counts[k]) != top_set(counts["decl"]))
                row[f"{k}_tvd"] = tvd(counts[k], counts["decl"])
                row[f"{k}_recovers"] = sum(1 for i in reattr if d[k][i] == d["decl"][i])
                row[f"{k}_reassigns"] = sum(1 for i in unc if d[k][i] != d["raw"][i])
            rows.append(row)
        result["c3"] = rows
    target.write_text(json.dumps(result))


if __name__ == "__main__":
    main()
