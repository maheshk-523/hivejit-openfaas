#!/usr/bin/env python3
"""Plot Julia/OpenFaaS latency curves with pod churn markers.

Produces a matplotlib PNG with raw invocation latency and dashed vertical lines
at pod restart (container churn) points. EWMA smoothing is available as an
opt-in debug overlay.

Usage:
  python3 plot_churn.py --csv results/lusearch-baseline.csv --out results/lusearch-baseline.png
  python3 plot_churn.py --csv results/lusearch-redis.csv --out results/lusearch-redis.png

  # Overlay baseline vs redis in one figure:
  python3 plot_churn.py \
    --csv results/lusearch-baseline.csv results/lusearch-redis.csv \
    --labels "baseline (no cache)" "redis precompile cache" \
    --out results/lusearch-comparison.png
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np


MODE_COLORS = {
    "baseline": "#202020",
    "redis": "#009E73",
    "sysimage5": "#D55E00",
    "sysimage10": "#0072B2",
}

FALLBACK_COLORS = ["#202020", "#D55E00", "#0072B2", "#CC79A7", "#009E73"]
CHURN_COLOR = "#6f6f6f"


def pretty_label(value: str) -> str:
    return {
        "baseline": "Baseline",
        "redis": "Redis precompile cache",
        "sysimage5": "AOT cache (5 profiles)",
        "sysimage10": "AOT cache (10 profiles)",
    }.get(value, value)


def color_for(label: str, index: int = 0) -> str:
    return MODE_COLORS.get(label, FALLBACK_COLORS[index % len(FALLBACK_COLORS)])


def read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    parsed = []
    for row in rows:
        try:
            status = int(row.get("status") or 0)
            latency = float(row.get("http_latency_ms") or 0.0)
        except (ValueError, TypeError):
            continue
        parsed.append(
            {
                "invocation": int(row["invocation"]),
                "latency_ms": latency,
                "status": status,
                "churn": row.get("churn") == "1",
                "workload": row.get("workload", ""),
                "cache_mode": row.get("cache_mode", ""),
            }
        )
    return parsed


def ewma(values: list[float], alpha: float = 0.16) -> np.ndarray:
    result = np.empty(len(values))
    current = 0.0
    for i, v in enumerate(values):
        current = v if i == 0 else alpha * v + (1.0 - alpha) * current
        result[i] = current
    return result


def plot_single(
    ax: plt.Axes,
    rows: list[dict[str, Any]],
    title: str,
    alpha: float = 0.16,
    smooth: bool = False,
) -> None:
    """Plot a single latency series on the given axes."""
    ok_rows = [r for r in rows if 200 <= r["status"] < 400]
    if not ok_rows:
        ax.text(0.5, 0.5, "No successful invocations", transform=ax.transAxes,
                ha="center", va="center", fontsize=14, color="red")
        return

    x = np.array([r["invocation"] for r in ok_rows])
    y = np.array([r["latency_ms"] for r in ok_rows])

    mode = ok_rows[0].get("cache_mode", "") if ok_rows else ""
    line_color = color_for(mode)
    ax.plot(x, y, color=line_color, linewidth=1.25, alpha=0.92, label="raw invocation latency")

    if smooth:
        smooth_vals = ewma(y.tolist(), alpha)
        ax.plot(x, smooth_vals, color="#E69F00", linewidth=2.2, alpha=0.92, label="EWMA smoothed")

    # Dashed vertical lines at churn points
    churn_x = [r["invocation"] for r in ok_rows if r["churn"]]
    for first, cx in enumerate(churn_x):
        ax.axvline(
            cx,
            color=CHURN_COLOR,
            linestyle="--",
            linewidth=1.0,
            alpha=0.62,
            label="pod restart (churn)" if first == 0 else "_nolegend_",
        )

    ax.set_xlabel("Request index", fontsize=12)
    ax.set_ylabel("End-to-end latency (ms)", fontsize=12)
    ax.set_title(title, fontsize=13, fontweight="bold")
    ax.legend(loc="upper right", fontsize=10, framealpha=0.9)
    ax.set_xlim(left=0)
    ax.set_ylim(bottom=0)
    ax.xaxis.set_major_locator(ticker.MaxNLocator(integer=True, nbins=10))
    ax.grid(axis="y", color="#e0e0e0", linewidth=0.5)


def plot_overlay(
    ax: plt.Axes,
    all_rows: list[list[dict[str, Any]]],
    labels: list[str],
    title: str,
    alpha: float = 0.16,
    smooth: bool = False,
) -> None:
    """Overlay multiple series (e.g., baseline vs redis) on one axes."""
    churn_points: set[int] = set()

    for idx, (rows, label) in enumerate(zip(all_rows, labels)):
        ok_rows = [r for r in rows if 200 <= r["status"] < 400]
        if not ok_rows:
            continue
        x = np.array([r["invocation"] for r in ok_rows])
        y = np.array([r["latency_ms"] for r in ok_rows])

        raw_color = color_for(label, idx)
        ax.plot(
            x,
            y,
            color=raw_color,
            linewidth=1.35,
            alpha=0.86,
            label=f"{pretty_label(label)} (raw)",
            zorder=3 + idx,
        )

        if smooth:
            smooth_vals = ewma(y.tolist(), alpha)
            ax.plot(
                x,
                smooth_vals,
                color=raw_color,
                linewidth=2.3,
                alpha=0.95,
                label=f"{pretty_label(label)} (EWMA)",
                zorder=6 + idx,
            )

        churn_points.update(r["invocation"] for r in ok_rows if r["churn"])

    for first, cx in enumerate(sorted(churn_points)):
        ax.axvline(
            cx,
            color=CHURN_COLOR,
            linestyle="--",
            linewidth=1.0,
            alpha=0.62,
            zorder=1,
            label="pod restart (churn)" if first == 0 else "_nolegend_",
        )

    ax.set_xlabel("Request index", fontsize=12)
    ax.set_ylabel("End-to-end latency (ms)", fontsize=12)
    ax.set_title(title, fontsize=13, fontweight="bold")
    ax.legend(loc="upper right", fontsize=10, framealpha=0.9)
    ax.set_xlim(left=0)
    ax.set_ylim(bottom=0)
    ax.xaxis.set_major_locator(ticker.MaxNLocator(integer=True, nbins=10))
    ax.grid(axis="y", color="#e0e0e0", linewidth=0.5)


def write_summary(rows: list[dict[str, Any]], out: Path) -> None:
    ok_rows = [r for r in rows if 200 <= r["status"] < 400]
    latencies = [r["latency_ms"] for r in ok_rows]
    if not latencies:
        summary = {"ok": 0}
    else:
        sorted_lat = sorted(latencies)

        def pct(p: float) -> float:
            idx = (len(sorted_lat) - 1) * p / 100.0
            lo = int(idx)
            hi = min(lo + 1, len(sorted_lat) - 1)
            w = idx - lo
            return sorted_lat[lo] * (1 - w) + sorted_lat[hi] * w

        summary = {
            "ok": len(ok_rows),
            "total": len(rows),
            "churn_points": [r["invocation"] for r in rows if r["churn"]],
            "min_ms": min(latencies),
            "max_ms": max(latencies),
            "mean_ms": sum(latencies) / len(latencies),
            "p50_ms": pct(50),
            "p95_ms": pct(95),
            "p99_ms": pct(99),
        }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Plot Julia/OpenFaaS warmup latency graphs with pod-churn markers."
    )
    parser.add_argument(
        "--csv", nargs="+", required=True, type=Path,
        help="One or more CSV files from run_churn_bench.py",
    )
    parser.add_argument(
        "--labels", nargs="*", default=None,
        help="Labels for each CSV (used when overlaying multiple series)",
    )
    parser.add_argument("--out", required=True, type=Path, help="Output PNG path")
    parser.add_argument("--summary", type=Path, help="Optional JSON summary output")
    parser.add_argument("--title", default="", help="Plot title override")
    parser.add_argument("--ewma-alpha", type=float, default=0.16, help="EWMA smoothing factor")
    parser.add_argument("--smooth", action="store_true", help="Overlay EWMA-smoothed latency")
    parser.add_argument("--no-smooth", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--dpi", type=int, default=150, help="Output DPI")
    parser.add_argument(
        "--figsize", nargs=2, type=float, default=[10.5, 5.0],
        help="Figure size in inches (width height)",
    )
    args = parser.parse_args()

    all_rows = [read_csv(p) for p in args.csv]
    if not any(all_rows):
        raise SystemExit("no data found in input CSV files")

    fig, ax = plt.subplots(1, 1, figsize=tuple(args.figsize))

    if len(all_rows) == 1:
        rows = all_rows[0]
        workload = rows[0]["workload"] if rows else "unknown"
        mode = rows[0]["cache_mode"] if rows else ""
        default_title = (
            f"Julia {workload} on OpenFaaS (with container churn + JIT warmup)"
            if not mode
            else f"Julia {workload} on OpenFaaS — {mode} (with container churn + JIT warmup)"
        )
        title = args.title or default_title
        plot_single(ax, rows, title, args.ewma_alpha, smooth=args.smooth and not args.no_smooth)
        if args.summary:
            write_summary(rows, args.summary)
    else:
        labels = args.labels or [p.stem for p in args.csv]
        workload = all_rows[0][0]["workload"] if all_rows[0] else "unknown"
        default_title = f"Julia {workload} on OpenFaaS (with container churn + JIT warmup)"
        title = args.title or default_title
        plot_overlay(ax, all_rows, labels, title, args.ewma_alpha, smooth=args.smooth and not args.no_smooth)
        if args.summary:
            write_summary(all_rows[0], args.summary)

    plt.tight_layout()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.out, dpi=args.dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
