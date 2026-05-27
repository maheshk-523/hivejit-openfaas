#!/usr/bin/env python3
"""Render a distinct real-data Flax cache result figure."""

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
            "artifact_import_ms",
            "startup_plus_first_request_ms",
        ):
            row[field] = float(row[field])
    return sorted(rows, key=lambda row: row["iteration"])


def warm_curve(rows: list[dict[str, Any]]) -> list[float]:
    values = [rows[0]["startup_plus_first_request_ms"]]
    values.extend(row["first_execute_ms"] for row in rows[1:10])
    return values


def render(results_dir: Path, scenario: str, out: Path) -> None:
    baseline = read_rows(results_dir / "baseline.csv", scenario)
    cache = read_rows(results_dir / "persistent-cache-reuse.csv", scenario)
    if len(baseline) < 10 or len(cache) < 10:
        raise ValueError(f"expected 10 rows for {scenario} in {results_dir}")

    x = list(range(1, 11))
    baseline_curve = warm_curve(baseline)
    cache_curve = warm_curve(cache)

    baseline_p50 = statistics.median(row["startup_plus_first_request_ms"] for row in baseline)
    cache_p50 = statistics.median(row["startup_plus_first_request_ms"] for row in cache)
    compile_p50 = statistics.median(row["compile_or_load_ms"] for row in baseline)
    cache_compile_p50 = statistics.median(row["compile_or_load_ms"] for row in cache)

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

    fig = plt.figure(figsize=(15.2, 6.5), dpi=180)
    gs = fig.add_gridspec(1, 2, width_ratios=[2.25, 1.0], wspace=0.25)
    ax = fig.add_subplot(gs[0, 0])
    cmp_ax = fig.add_subplot(gs[0, 1])

    ax.plot(
        x,
        baseline_curve,
        color="#7ec8ff",
        marker="o",
        linewidth=2.7,
        markersize=6,
        label="Baseline cold JIT",
    )
    ax.plot(
        x,
        cache_curve,
        color="#ffb86b",
        marker="s",
        linewidth=2.7,
        markersize=5.8,
        linestyle="--",
        label="Persistent cache hit",
    )
    ax.set_xticks(x)
    ax.set_xlabel("Invocation")
    ax.set_ylabel("Latency (milliseconds)")
    ax.set_title("Real Flax/MNIST Cold Start with JAX Persistent Cache", fontsize=15, pad=14)
    ax.grid(True, linewidth=0.8)
    ax.legend(loc="upper right", frameon=True, facecolor="#0a2a50", edgecolor="#3b76ad", labelcolor="#edf6ff")

    ax.text(
        0.015,
        0.07,
        f"First-request p50: {baseline_p50:.0f}ms -> {cache_p50:.0f}ms\n"
        f"Compile/load p50: {compile_p50:.0f}ms -> {cache_compile_p50:.1f}ms",
        transform=ax.transAxes,
        ha="left",
        va="bottom",
        fontsize=10.5,
        bbox={"boxstyle": "round,pad=0.4", "facecolor": "#0a2a50", "edgecolor": "#3b76ad", "alpha": 0.95},
    )

    cmp_categories = [
        ("First\nrequest", "startup_plus_first_request_ms"),
        ("Compile\nload", "compile_or_load_ms"),
        ("Execute", "first_execute_ms"),
    ]
    positions = list(range(len(cmp_categories)))
    width = 0.34
    baseline_values = [statistics.median(row[field] for row in baseline) for _label, field in cmp_categories]
    cache_values = [statistics.median(row[field] for row in cache) for _label, field in cmp_categories]
    cmp_ax.bar([pos - width / 2 for pos in positions], baseline_values, width=width, color="#7ec8ff", label="Baseline")
    cmp_ax.bar([pos + width / 2 for pos in positions], cache_values, width=width, color="#ffb86b", label="Cache hit")
    for pos, value in zip(positions, baseline_values):
        cmp_ax.text(pos - width / 2, value + 11, f"{value:.0f}", ha="center", va="bottom", fontsize=9.2, fontweight="bold")
    for pos, value in zip(positions, cache_values):
        label = f"{value:.1f}" if value < 10 else f"{value:.0f}"
        cmp_ax.text(pos + width / 2, value + 11, label, ha="center", va="bottom", fontsize=9.2, fontweight="bold")
    cmp_ax.set_title("p50 Component Bars", fontsize=13, pad=14)
    cmp_ax.set_ylabel("Milliseconds")
    cmp_ax.set_xticks(positions, [label for label, _field in cmp_categories])
    cmp_ax.set_ylim(0, max(baseline_values + cache_values) * 1.35)
    cmp_ax.grid(True, axis="y", linewidth=0.8)
    cmp_ax.legend(
        loc="upper right",
        frameon=True,
        facecolor="#0a2a50",
        edgecolor="#3b76ad",
        labelcolor="#edf6ff",
        fontsize=8.8,
    )
    cmp_ax.set_facecolor("#082344")

    fig.text(
        0.5,
        0.02,
        "Real MNIST training images, Flax Linen CNN train_step, CPU backend. "
        "Point 1 includes lower/compile/execute; later points are hot execution. Linear y-axis.",
        ha="center",
        va="bottom",
        fontsize=9.3,
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
