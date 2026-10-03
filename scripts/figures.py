"""Draw the manuscript figures from the analysis outputs.

Usage: figures.py <analysis_dir> <figure_dir>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

LANE_COLOR = {"RAW": "#2a78d6", "W": "#eb6834", "WMC": "#1baf7a", "DECLARED": "#4a3aa7"}
INK, MUTED, GRID = "#1f1f1e", "#6b6a63", "#e4e3dc"

plt.rcParams.update({
    "font.family": "serif", "font.size": 8.5, "axes.edgecolor": MUTED, "axes.labelcolor": INK,
    "xtick.color": MUTED, "ytick.color": INK, "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6, "axes.axisbelow": True,
    "savefig.bbox": "tight", "savefig.pad_inches": 0.02, "pdf.fonttype": 42,
})


def divergence(res: dict, out: Path):
    metrics = [("dis", "Line disagreement"), ("tvd", "Share distance"), ("flip", "Owner change"),
               ("major_change", "Major-contributor change"), ("top3_change", "Top-three change")]
    lanes = [("raw", "RAW"), ("w", "W"), ("wmc", "WMC")]
    fig, axes = plt.subplots(1, 2, figsize=(6.6, 2.9), sharey=True, sharex=True)
    for ax, sample, title in zip(axes, ("touched", "random"), ("Touched sample", "Random sample")):
        block = res["divergence"][sample]
        y = 0
        ticks, labels = [], []
        for key, name in metrics:
            for j, (lk, lane) in enumerate(lanes):
                v = block[f"{lk}_{key}"]
                yy = y + j * 0.28
                ax.errorbar(v["mean"], yy, xerr=[[v["mean"] - v["ci"][0]], [v["ci"][1] - v["mean"]]],
                            fmt="o", ms=4.2, color=LANE_COLOR[lane], ecolor=LANE_COLOR[lane], elinewidth=1.4,
                            capsize=0, markeredgecolor="white", markeredgewidth=0.6)
            ticks.append(y + 0.28)
            labels.append(name)
            y += 1.25
        ax.set_yticks(ticks, labels)
        ax.set_title(f"{title} ({block['repos']} repositories)", fontsize=8.5, color=INK, loc="left")
        ax.set_xlim(0, None)
        ax.grid(axis="y", visible=False)
    axes[0].invert_yaxis()  # shared y axis: invert once
    fig.supxlabel("Disagreement with DECLARED: mean across repositories (95% CI)", fontsize=8.5, y=0.02)
    handles = [plt.Line2D([], [], marker="o", ls="", color=LANE_COLOR[l], label=l) for _, l in lanes]
    fig.legend(handles=handles, loc="upper center", ncol=3, frameon=False, bbox_to_anchor=(0.6, 1.06))
    fig.tight_layout()
    fig.savefig(out / "divergence.pdf")
    plt.close(fig)


def persistence(analysis: Path, out: Path):
    sup = json.loads((analysis / "results_supplement.json").read_text())
    rows = sup["B"]["by_age"]
    names = [r["age_bin"].replace("-", "–") for r in rows]
    x = np.arange(len(rows))
    fig, ax = plt.subplots(figsize=(3.6, 2.4))
    for off, key, label, color in ((-0.17, "declared", "declared commits", LANE_COLOR["DECLARED"]),
                                   (0.17, "undeclared", "matched undeclared commits", "#8f8d84")):
        v = np.array([r[key] for r in rows], dtype=float)
        ax.bar(x + off, v[:, 0], width=0.32, color=color, label=label, zorder=2)
        ax.errorbar(x + off, v[:, 0], yerr=[v[:, 0] - v[:, 1], v[:, 2] - v[:, 0]], fmt="none", ecolor=INK,
                    elinewidth=0.9, capsize=0, zorder=3)
    for xi, r in zip(x, rows):
        top = max(r["declared"][2], r["undeclared"][2])
        ax.text(xi, top + 0.03, f"n={r['commits']}", ha="center", va="bottom", fontsize=6.5, color=INK)
    ax.set_xticks(x, names)
    ax.set_ylim(0, 1.08)
    ax.set_xlabel("Age of commit (years)")
    ax.set_ylabel("Share of added lines\nstill credited by RAW")
    ax.grid(axis="x", visible=False)
    ax.legend(frameon=False, fontsize=7.2, loc="lower center", bbox_to_anchor=(0.5, 1.0), ncol=2)
    fig.savefig(out / "persistence.pdf")
    plt.close(fig)


def prospective(analysis: Path, out: Path):
    sup = json.loads((analysis / "results_supplement.json").read_text())["C"]
    methods = [("RAW", "RAW owner"), ("W", "W owner"), ("WMC", "WMC owner"), ("DECLARED", "DECLARED owner"),
               ("PRIOR", "top committer, prior year"), ("LAST", "last committer"), ("REPOTOP", "most active in repository")]
    fig, axes = plt.subplots(1, 2, figsize=(6.6, 2.5), sharey=True, sharex=True)
    for ax, key, title in zip(axes, ("all", "flip"), (f"All evaluable files ({sup['files']:,})",
                                                     f"Owner-change files ({sup['flip_files']:,})")):
        for k, (m, label) in enumerate(methods):
            v = sup[key][m]
            color = LANE_COLOR.get(m, "#8f8d84")
            ax.barh(k, v[0], height=0.62, color=color, zorder=2)
            ax.errorbar(v[0], k, xerr=[[v[0] - v[1]], [v[2] - v[0]]], fmt="none", ecolor=INK, elinewidth=0.9, zorder=3)
            ax.text(v[2] + 0.012, k, f"{v[0]:.2f}", va="center", fontsize=7.2, color=INK)
        ax.set_yticks(range(len(methods)), [l for _, l in methods])
        ax.set_title(title, fontsize=8.5, loc="left", color=INK)
        ax.set_xlim(0, 0.5)
        ax.grid(axis="y", visible=False)
    axes[0].invert_yaxis()
    fig.supxlabel("Hit@1 for the top later committer: mean across repositories (95% CI)", fontsize=8.5, y=0.0)
    fig.tight_layout()
    fig.savefig(out / "prospective.pdf")
    plt.close(fig)


def main():
    analysis, out = Path(sys.argv[1]), Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    res = json.loads((analysis / "results.json").read_text())
    divergence(res, out)
    persistence(analysis, out)


if __name__ == "__main__":
    main()
