#!/usr/bin/env python3
"""Render a combined Go PGO profile-cache figure across benchmarks."""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np


COLORS = {
    "go-nopgo": "#222222",
    "go-pgo-5": "#0072B2",
    "go-pgo-10": "#009E73",
}
LABELS = {
    "go-nopgo": "No PGO",
    "go-pgo-5": "PGO, 5 profiles",
    "go-pgo-10": "PGO, 10 profiles",
}


def read_rows(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows.append(
                {
                    "label": row["label"],
                    "iteration": int(row["iteration"]),
                    "wall_ms": float(row["wall_ms"]),
                    "work_ms": float(row["work_ms"]),
                }
            )
    return rows


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = (len(ordered) - 1) * p / 100.0
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    if lower == upper:
        return ordered[lower]
    weight = index - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def benchmark_name(path: Path) -> str:
    return path.name.removeprefix("dacapo-")


def collect(results_root: Path) -> dict[str, dict[str, list[float]]]:
    data: dict[str, dict[str, list[float]]] = {}
    for bench_dir in sorted(path for path in results_root.iterdir() if path.is_dir()):
        benchmark = benchmark_name(bench_dir)
        for csv_path in sorted(bench_dir.glob("go-*.csv")):
            for row in read_rows(csv_path):
                data.setdefault(benchmark, {}).setdefault(row["label"], []).append(row["wall_ms"])
    return data


def summarize(data: dict[str, dict[str, list[float]]]) -> dict[str, Any]:
    summary: dict[str, Any] = {"schema": "go-pgo-profile-cache-all-summary.v1", "benchmarks": {}}
    for benchmark, series in data.items():
        bench_summary: dict[str, Any] = {}
        baseline_p50 = percentile(series.get("go-nopgo", []), 50)
        for label, values in series.items():
            p50 = percentile(values, 50)
            p95 = percentile(values, 95)
            bench_summary[label] = {
                "n": len(values),
                "mean_ms": statistics.fmean(values) if values else 0.0,
                "p50_ms": p50,
                "p95_ms": p95,
                "min_ms": min(values) if values else 0.0,
                "max_ms": max(values) if values else 0.0,
                "p50_saved_pct": ((baseline_p50 - p50) / baseline_p50 * 100.0) if baseline_p50 > 0 else 0.0,
            }
        summary["benchmarks"][benchmark] = bench_summary
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-root", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--summary", required=True, type=Path)
    parser.add_argument("--title", default="Go PGO profile-cache results across DaCapo-shaped workloads")
    parser.add_argument("--dpi", type=int, default=170)
    args = parser.parse_args()

    data = collect(args.results_root)
    if not data:
        raise SystemExit(f"no benchmark result directories found under {args.results_root}")

    summary = summarize(data)
    benchmarks = sorted(data)
    labels = ["go-nopgo", "go-pgo-5", "go-pgo-10"]
    x = np.arange(len(benchmarks))
    width = 0.24

    fig, axes = plt.subplots(2, 1, figsize=(12.8, 8.0), sharex=True)
    for idx, label in enumerate(labels):
        offset = (idx - 1) * width
        p50_values = [summary["benchmarks"][benchmark].get(label, {}).get("p50_ms", 0.0) for benchmark in benchmarks]
        p95_values = [summary["benchmarks"][benchmark].get(label, {}).get("p95_ms", 0.0) for benchmark in benchmarks]
        axes[0].bar(x + offset, p50_values, width=width, color=COLORS[label], label=LABELS[label])
        axes[1].bar(x + offset, p95_values, width=width, color=COLORS[label], label=LABELS[label])

    axes[0].set_ylabel("p50 latency (ms)")
    axes[1].set_ylabel("p95 latency (ms)")
    axes[1].set_xlabel("Benchmark")
    axes[0].set_title(args.title, fontsize=15, fontweight="bold")
    axes[1].set_xticks(x)
    axes[1].set_xticklabels(benchmarks)
    for ax in axes:
        ax.grid(axis="y", color="#e0e0e0", linewidth=0.6)
        ax.set_axisbelow(True)
    axes[0].legend(loc="upper right", ncol=3, framealpha=0.94)
    plt.tight_layout()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.out, dpi=args.dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.out}")
    print(f"wrote {args.summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
