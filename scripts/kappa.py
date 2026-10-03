"""Agreement between the keyword rule and independent human raters.

Usage: kappa.py <ratings_dir> <key.json> <out_json>

<ratings_dir> holds one JSON document per rater (as exported from the rater
page's `ratings` collection); each has `labels: {item_id: category}`.
"""

from __future__ import annotations

import json
import math
import random
import sys
from collections import Counter
from itertools import combinations
from pathlib import Path


LAYOUT = {"formatter", "whitespace", "style", "lint"}  # mechanical layout and cleanup changes


def wilson(k, n, z=1.96):
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return c - h, c + h


def cohen(a: list[str], b: list[str]) -> float:
    n = len(a)
    observed = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    expected = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    return (observed - expected) / (1 - expected) if expected < 1 else 1.0


def main():
    ratings_dir, key_path, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    key = json.loads(key_path.read_text())
    raters = {}
    for p in sorted(ratings_dir.rglob("*.json")):
        doc = json.loads(p.read_text())
        labels = doc.get("labels") or doc.get("data", {}).get("labels")
        if labels:
            raters[p.stem] = labels
    res = {"raters": len(raters), "per_rater": {}, "between_raters": {}}
    for name, labels in raters.items():
        ids = [i for i in key if i in labels]
        rule = [key[i]["rule"] for i in ids]
        human = [labels[i] for i in ids]
        confusion = Counter((r, h) for r, h in zip(rule, human) if r != h)
        rng = random.Random(1)
        draws = []
        for _ in range(2000):
            idx = [rng.randrange(len(ids)) for _ in ids]
            draws.append(cohen([rule[i] for i in idx], [human[i] for i in idx]))
        draws.sort()
        coarse = lambda c: "layout" if c in LAYOUT else c  # noqa: E731
        agree = sum(r == h for r, h in zip(rule, human))
        lo, hi = wilson(agree, len(ids))
        res["per_rater"][name] = {
            "items": len(ids), "agreement": sum(r == h for r, h in zip(rule, human)) / len(ids),
            "kappa": cohen(rule, human), "kappa_ci": [draws[50], draws[1949]], "agreement_ci": [lo, hi],
            "coarse_agreement": sum(coarse(r) == coarse(h) for r, h in zip(rule, human)) / len(ids),
            "coarse_kappa": cohen([coarse(r) for r in rule], [coarse(h) for h in human]),
            "rule_counts": dict(Counter(rule)), "human_counts": dict(Counter(human)),
            "top_disagreements": [[r, h, c] for (r, h), c in confusion.most_common(10)],
        }
    for (a, la), (b, lb) in combinations(raters.items(), 2):
        ids = [i for i in key if i in la and i in lb]
        res["between_raters"][f"{a}|{b}"] = {"items": len(ids), "kappa": cohen([la[i] for i in ids], [lb[i] for i in ids])}
    out.write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
