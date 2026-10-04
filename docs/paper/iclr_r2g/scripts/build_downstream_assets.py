#!/usr/bin/env python3
"""Build the compact downstream-utility figure from frozen GNN summaries."""

from pathlib import Path
import json
import sys

import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from plot_style import BLUE, CORAL, INK, MUTED, TEAL, configure  # noqa: E402

TARGETS = (
    ("wirelength", "Wirelength", BLUE),
    ("hold_slack", "Hold slack", CORAL),
    ("effective_resistance", "Eff. resistance", TEAL),
)
STAGES = ("floorplan", "placement", "cts", "route")
STAGE_LABELS = ("post-Synth.", "post-Floorplan", "post-Placement", "post-CTS")


def index(rows, *keys):
    return {tuple(row[key] for key in keys): row for row in rows}


def main():
    configure()
    stage_rows = json.loads((ROOT / "evidence/gnn_stage_prediction.json").read_text())["summary"]
    geometry_rows = json.loads((ROOT / "evidence/gnn_geometry_ablation.json").read_text())["summary"]
    stage = index(stage_rows, "target", "stage")
    geometry = index(geometry_rows, "target", "ablation")

    fig, (ax_stage, ax_geometry) = plt.subplots(
        1, 2, figsize=(7.0, 2.25), gridspec_kw={"width_ratios": (1.55, 1.0)}
    )
    x = np.arange(len(STAGES))
    for target, label, color in TARGETS:
        raw = np.array([stage[(target, name)]["macro_mae_mean"] for name in STAGES])
        std = np.array([stage[(target, name)]["macro_mae_std"] for name in STAGES])
        normalized = 100.0 * raw / raw[0]
        normalized_std = 100.0 * std / raw[0]
        ax_stage.errorbar(
            x, normalized, yerr=normalized_std, color=color, marker="o",
            linewidth=1.7, markersize=4.2, capsize=2, label=label,
        )
    ax_stage.axhline(100, color=MUTED, linewidth=0.7, linestyle="--")
    ax_stage.set_xticks(x, STAGE_LABELS, rotation=17, ha="right")
    ax_stage.set_ylabel("MAE relative to post-synthesis (%)")
    ax_stage.set_ylim(30, 110)
    ax_stage.legend(frameon=False, ncol=3, loc="lower left", borderaxespad=0)
    ax_stage.set_title("a  Stage-valid inputs", loc="left", color=INK, fontweight="bold")

    penalties = []
    for target, _, _ in TARGETS:
        full = stage[(target, "cts")]["macro_mae_mean"]
        removed = geometry[(target, "no_geometry")]["macro_mae_mean"]
        penalties.append(100.0 * (removed / full - 1.0))
    bars = ax_geometry.bar(
        np.arange(len(TARGETS)), penalties,
        color=[color for _, _, color in TARGETS], width=0.62,
    )
    ax_geometry.axhline(0, color=MUTED, linewidth=0.7)
    ax_geometry.set_xticks(
        np.arange(len(TARGETS)), [label for _, label, _ in TARGETS],
        rotation=17, ha="right",
    )
    ax_geometry.set_ylabel("MAE increase without geometry (%)")
    ax_geometry.set_ylim(0, 190)
    ax_geometry.set_title("b  Geometry ablation", loc="left", color=INK, fontweight="bold")
    for bar, value in zip(bars, penalties):
        ax_geometry.text(
            bar.get_x() + bar.get_width() / 2, value + 5,
            f"{value:.1f}%", ha="center", va="bottom", fontsize=8,
        )

    fig.subplots_adjust(left=0.09, right=0.99, bottom=0.29, top=0.88, wspace=0.34)
    for suffix in ("pdf", "png"):
        fig.savefig(ROOT / f"figures/downstream_utility.{suffix}", dpi=300)
    plt.close(fig)


if __name__ == "__main__":
    main()
