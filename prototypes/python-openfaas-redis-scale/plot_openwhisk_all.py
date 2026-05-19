#!/usr/bin/env python3
"""Plot OpenWhisk-style Python/OpenFaaS traces for all benchmark pairs."""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np


COLORS = {
    "baseline": "#202020",
    "saved": "#0072B2",
}
CHURN_COLOR = "#6f6f6f"


def bench_short(benchmark: str) -> str:
    return benchmark.removeprefix("dacapo-")


def read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    parsed = []
    for row in rows:
        try:
            parsed.append(
                {
                    "invocation": int(row["invocation"]),
                    "latency_ms": float(row["http_latency_ms"]),
                    "status": int(row.get("status") or 0),
                    "churn": row.get("churn") == "1",
                    "checksum": row.get("checksum", ""),
                    "error": row.get("error", ""),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    return parsed


def usable(row: dict[str, Any]) -> bool:
    return 200 <= row["status"] < 400 and not row["error"]


def filter_pair(baseline: list[dict[str, Any]], saved: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    baseline_rows = [row for row in baseline if usable(row)]
    checksums = {
        row["invocation"]: row["checksum"]
        for row in baseline_rows
        if row["checksum"]
    }
    saved_rows = [
        row
        for row in saved
        if usable(row) and row["checksum"] == checksums.get(row["invocation"])
    ]
    return baseline_rows, saved_rows


def ewma(values: list[float], alpha: float) -> list[float]:
    smoothed = []
    current = 0.0
    for index, value in enumerate(values):
        current = value if index == 0 else alpha * value + (1.0 - alpha) * current
        smoothed.append(current)
    return smoothed


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


def series_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    values = [row["latency_ms"] for row in rows]
    return {
        "ok": len(rows),
        "churn_points": [row["invocation"] for row in rows if row["churn"]],
        "mean_ms": statistics.fmean(values) if values else 0.0,
        "p50_ms": percentile(values, 50),
        "p95_ms": percentile(values, 95),
        "p99_ms": percentile(values, 99),
        "min_ms": min(values) if values else 0.0,
        "max_ms": max(values) if values else 0.0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument(
        "--benchmarks",
        nargs="+",
        default=["dacapo-lusearch", "dacapo-h2", "dacapo-eclipse", "dacapo-jython", "dacapo-fop"],
    )
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--summary", required=True, type=Path)
    parser.add_argument("--title", default="Real OpenFaaS Python OpenWhisk-style churn traces")
    parser.add_argument("--ewma-alpha", type=float, default=0.10)
    parser.add_argument("--dpi", type=int, default=170)
    args = parser.parse_args()

    pairs: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for benchmark in args.benchmarks:
        short = bench_short(benchmark)
        baseline = read_csv(args.input_dir / f"openwhisk-{short}-baseline.csv")
        saved = read_csv(args.input_dir / f"openwhisk-{short}-saved.csv")
        baseline_rows, saved_rows = filter_pair(baseline, saved)
        pairs[benchmark] = {"baseline": baseline_rows, "saved": saved_rows}

    panel_count = len(args.benchmarks)
    fig, axes = plt.subplots(panel_count, 1, figsize=(13.4, 3.35 * panel_count), sharex=True)
    if panel_count == 1:
        axes = [axes]

    summary: dict[str, Any] = {"schema": "python-openfaas-openwhisk-all-summary.v1", "benchmarks": {}}
    for ax, benchmark in zip(axes, args.benchmarks):
        by_treatment = pairs[benchmark]
        churn_points = sorted(
            {
                row["invocation"]
                for rows in by_treatment.values()
                for row in rows
                if row["churn"]
            }
        )
        for first, point in enumerate(churn_points):
            ax.axvline(
                point,
                color=CHURN_COLOR,
                linestyle="--",
                linewidth=1.0,
                alpha=0.50,
                label="pod restart" if first == 0 else "_nolegend_",
                zorder=1,
            )

        for index, treatment in enumerate(("baseline", "saved")):
            rows = by_treatment[treatment]
            x = np.array([row["invocation"] for row in rows])
            y = np.array([row["latency_ms"] for row in rows])
            color = COLORS[treatment]
            ax.plot(x, y, color=color, linewidth=0.7, alpha=0.22, label=f"{treatment} raw", zorder=2 + index)
            if len(y) > 0:
                ax.plot(
                    x,
                    ewma(y.tolist(), args.ewma_alpha),
                    color=color,
                    linewidth=2.4,
                    alpha=0.96,
                    label=f"{treatment} EWMA",
                    zorder=5 + index,
                )

        baseline_summary = series_summary(by_treatment["baseline"])
        saved_summary = series_summary(by_treatment["saved"])
        base_p50 = baseline_summary["p50_ms"]
        saved_p50 = saved_summary["p50_ms"]
        saved_pct = ((base_p50 - saved_p50) / base_p50 * 100.0) if base_p50 > 0 else 0.0
        summary["benchmarks"][benchmark] = {
            "baseline": baseline_summary,
            "saved": saved_summary,
            "p50_saved_pct": saved_pct,
        }

        label = f"{bench_short(benchmark)}   p50 saved {saved_pct:.1f}%"
        ax.set_title(label, loc="left", fontsize=11, fontweight="bold")
        ax.set_ylabel("latency (ms)", fontsize=10)
        ax.set_xlim(left=0)
        ax.set_ylim(bottom=0)
        ax.grid(axis="y", color="#e0e0e0", linewidth=0.6)
        ax.xaxis.set_major_locator(ticker.MaxNLocator(integer=True, nbins=10))

    axes[0].legend(loc="upper right", ncol=5, fontsize=9, framealpha=0.94)
    axes[-1].set_xlabel("Request index", fontsize=11)
    fig.suptitle(args.title, fontsize=15, fontweight="bold", y=0.996)
    plt.tight_layout(rect=(0, 0, 1, 0.975))

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
