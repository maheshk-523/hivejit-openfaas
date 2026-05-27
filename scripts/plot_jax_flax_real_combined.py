#!/usr/bin/env python3
"""Combined line graph and bar comparisons for real Flax/MNIST cache results."""

from __future__ import annotations

import argparse
import csv
import statistics
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def read_rows(path: Path, scenario: str) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = [row for row in csv.DictReader(f) if row["scenario"] == scenario]
    for row in rows:
        row["iteration"] = int(row["iteration"])
        for field in (
            "lower_ms",
            "compile_or_load_ms",
            "first_execute_ms",
            "startup_plus_first_request_ms",
        ):
            row[field] = float(row[field])
    return sorted(rows, key=lambda row: row["iteration"])


def warm_curve(rows: list[dict[str, Any]]) -> list[float]:
    values = [rows[0]["startup_plus_first_request_ms"]]
    values.extend(row["first_execute_ms"] for row in rows[1:10])
    return values


def med(rows: list[dict[str, Any]], field: str) -> float:
    return statistics.median(row[field] for row in rows)


def render(results_dir: Path, scenario: str, out: Path) -> None:
    baseline = read_rows(results_dir / "baseline.csv", scenario)
    cache = read_rows(results_dir / "persistent-cache-reuse.csv", scenario)
    if len(baseline) < 10 or len(cache) < 10:
        raise ValueError(f"expected 10 rows for {scenario} in {results_dir}")

    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "figure.facecolor": "#04152d",
            "axes.facecolor": "#082344",
            "savefig.facecolor": "#04152d",
            "text.color": "#edf6ff",
            "axes.labelcolor": "#cfe2f5",
            "xtick.color": "#9fb9d3",
            "ytick.color": "#9fb9d3",
            "axes.edgecolor": "#2b5f93",
            "grid.color": "#1e4b77",
        }
    )

    fig = plt.figure(figsize=(15.8, 6.7), dpi=180)
    outer = fig.add_gridspec(1, 2, width_ratios=[1.72, 0.78], wspace=0.22)
    line_ax = fig.add_subplot(outer[0, 0])
    cmp_ax = fig.add_subplot(outer[0, 1])

    x = list(range(1, 11))
    line_ax.plot(
        x,
        warm_curve(baseline),
        color="#7ec8ff",
        marker="o",
        linewidth=2.7,
        markersize=6,
        label="Baseline cold JIT",
    )
    line_ax.plot(
        x,
        warm_curve(cache),
        color="#ffb86b",
        marker="s",
        linestyle="--",
        linewidth=2.7,
        markersize=5.8,
        label="Persistent cache hit",
    )
    line_ax.set_xticks(x)
    line_ax.set_xlabel("Invocation")
    line_ax.set_ylabel("Latency (milliseconds)")
    line_ax.set_title("Warm Curve", fontsize=14, pad=12)
    line_ax.grid(True, linewidth=0.8)
    line_ax.legend(loc="upper right", frameon=True, facecolor="#0a2a50", edgecolor="#3b76ad", labelcolor="#edf6ff")
    line_ax.text(
        0.02,
        0.08,
        f"first-request p50: {med(baseline, 'startup_plus_first_request_ms'):.0f}ms -> "
        f"{med(cache, 'startup_plus_first_request_ms'):.0f}ms\n"
        f"compile/load p50: {med(baseline, 'compile_or_load_ms'):.0f}ms -> "
        f"{med(cache, 'compile_or_load_ms'):.1f}ms",
        transform=line_ax.transAxes,
        ha="left",
        va="bottom",
        fontsize=10.2,
        bbox={"boxstyle": "round,pad=0.38", "facecolor": "#0a2a50", "edgecolor": "#3b76ad", "alpha": 0.95},
    )

    cmp_categories = [
        ("First\nrequest", "startup_plus_first_request_ms"),
        ("Compile\nload", "compile_or_load_ms"),
        ("Execute", "first_execute_ms"),
    ]
    idx = list(range(len(cmp_categories)))
    width = 0.34
    base_vals = [med(baseline, field) for _label, field in cmp_categories]
    cache_vals = [med(cache, field) for _label, field in cmp_categories]
    cmp_ax.bar([i - width / 2 for i in idx], base_vals, width=width, color="#7ec8ff", label="Baseline")
    cmp_ax.bar([i + width / 2 for i in idx], cache_vals, width=width, color="#ffb86b", label="Cache hit")
    for i, value in enumerate(base_vals):
        cmp_ax.text(i - width / 2, value + 11, f"{value:.0f}", ha="center", va="bottom", fontsize=8.8, fontweight="bold")
    for i, value in enumerate(cache_vals):
        label = f"{value:.1f}" if value < 10 else f"{value:.0f}"
        cmp_ax.text(i + width / 2, value + 11, label, ha="center", va="bottom", fontsize=8.8, fontweight="bold")
    cmp_ax.set_title("p50 Component Bars", fontsize=13, pad=10)
    cmp_ax.set_ylabel("ms")
    cmp_ax.set_xticks(idx, [label for label, _field in cmp_categories])
    cmp_ax.set_ylim(0, max(base_vals + cache_vals) * 1.35)
    cmp_ax.grid(True, axis="y", linewidth=0.8)
    cmp_ax.legend(loc="upper right", frameon=True, facecolor="#0a2a50", edgecolor="#3b76ad", labelcolor="#edf6ff", fontsize=8.8)

    fig.suptitle("Real Flax/MNIST JAX Persistent Compilation Cache", fontsize=17, y=0.98)
    fig.text(
        0.5,
        0.02,
        "Real MNIST training images, Flax Linen CNN train_step, CPU backend. Point 1 includes lower/compile/execute; later points are hot execution. Linear y-axis.",
        ha="center",
        va="bottom",
        fontsize=9.2,
        color="#9fb9d3",
    )

    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-dir", type=Path, required=True)
    parser.add_argument("--scenario", default="flax-mnist-cnn-train-real")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    render(args.results_dir, args.scenario, args.out)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
