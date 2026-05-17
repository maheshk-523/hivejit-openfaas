#!/usr/bin/env python3
"""Plot C#/.NET raw latency curves with pod-churn markers."""

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
    "il": "#202020",
    "r2r": "#D55E00",
    "nativeaot": "#0072B2",
}

FALLBACK_COLORS = ["#202020", "#D55E00", "#0072B2", "#CC79A7", "#009E73"]
CHURN_COLOR = "#6f6f6f"


def pretty_label(value: str) -> str:
    return {
        "il": "IL/JIT",
        "r2r": "ReadyToRun",
        "nativeaot": "NativeAOT",
    }.get(value, value)


def color_for(label: str, index: int = 0) -> str:
    return MODE_COLORS.get(label, FALLBACK_COLORS[index % len(FALLBACK_COLORS)])


def read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    parsed: list[dict[str, Any]] = []
    for row in rows:
        try:
            parsed.append(
                {
                    "invocation": int(row["invocation"]),
                    "latency_ms": float(row["http_latency_ms"]),
                    "status": int(row.get("status") or 0),
                    "churn": row.get("churn") == "1",
                    "scenario": row.get("scenario") or row.get("workload", ""),
                    "mode": row.get("mode") or row.get("cache_mode", ""),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    return parsed


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
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def write_summary(rows: list[dict[str, Any]], out: Path) -> None:
    ok_rows = [row for row in rows if 200 <= row["status"] < 400]
    latencies = [row["latency_ms"] for row in ok_rows]
    summary = {
        "ok": len(ok_rows),
        "total": len(rows),
        "churn_points": [row["invocation"] for row in rows if row["churn"]],
        "min_ms": min(latencies) if latencies else 0.0,
        "max_ms": max(latencies) if latencies else 0.0,
        "mean_ms": sum(latencies) / len(latencies) if latencies else 0.0,
        "p50_ms": percentile(latencies, 50),
        "p95_ms": percentile(latencies, 95),
        "p99_ms": percentile(latencies, 99),
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")


def plot_single(ax: plt.Axes, rows: list[dict[str, Any]], title: str) -> None:
    ok_rows = [row for row in rows if 200 <= row["status"] < 400]
    x = np.array([row["invocation"] for row in ok_rows])
    y = np.array([row["latency_ms"] for row in ok_rows])
    mode = ok_rows[0].get("mode", "") if ok_rows else ""
    line_color = color_for(mode)
    ax.plot(x, y, color=line_color, linewidth=1.25, alpha=0.92, label="raw invocation latency")
    for first, churn_x in enumerate([row["invocation"] for row in ok_rows if row["churn"]]):
        ax.axvline(
            churn_x,
            color=CHURN_COLOR,
            linestyle="--",
            linewidth=1.0,
            alpha=0.62,
            label="pod restart (churn)" if first == 0 else "_nolegend_",
        )
    finish_axes(ax, title)


def plot_overlay(ax: plt.Axes, all_rows: list[list[dict[str, Any]]], labels: list[str], title: str) -> None:
    churn_points: set[int] = set()
    for idx, (rows, label) in enumerate(zip(all_rows, labels)):
        ok_rows = [row for row in rows if 200 <= row["status"] < 400]
        x = np.array([row["invocation"] for row in ok_rows])
        y = np.array([row["latency_ms"] for row in ok_rows])
        color = color_for(label, idx)
        ax.plot(
            x,
            y,
            color=color,
            linewidth=1.35,
            alpha=0.86,
            label=f"{pretty_label(label)} (raw)",
            zorder=3 + idx,
        )
        churn_points.update(row["invocation"] for row in ok_rows if row["churn"])
    for first, churn_x in enumerate(sorted(churn_points)):
        ax.axvline(
            churn_x,
            color=CHURN_COLOR,
            linestyle="--",
            linewidth=1.0,
            alpha=0.62,
            zorder=1,
            label="pod restart (churn)" if first == 0 else "_nolegend_",
        )
    finish_axes(ax, title)


def finish_axes(ax: plt.Axes, title: str) -> None:
    ax.set_xlabel("Request index", fontsize=12)
    ax.set_ylabel("End-to-end latency (ms)", fontsize=12)
    ax.set_title(title, fontsize=13, fontweight="bold")
    ax.legend(loc="upper right", fontsize=10, framealpha=0.9)
    ax.set_xlim(left=0)
    ax.set_ylim(bottom=0)
    ax.xaxis.set_major_locator(ticker.MaxNLocator(integer=True, nbins=10))
    ax.grid(axis="y", color="#e0e0e0", linewidth=0.5)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", nargs="+", required=True, type=Path)
    parser.add_argument("--labels", nargs="*", default=None)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--title", default="")
    parser.add_argument("--dpi", type=int, default=150)
    parser.add_argument("--figsize", nargs=2, type=float, default=[10.5, 5.0])
    args = parser.parse_args()

    all_rows = [read_csv(path) for path in args.csv]
    if not any(all_rows):
        raise SystemExit("no data found in input CSV files")

    fig, ax = plt.subplots(1, 1, figsize=tuple(args.figsize))
    if len(all_rows) == 1:
        rows = all_rows[0]
        scenario = rows[0]["scenario"] if rows else "unknown"
        mode = rows[0]["mode"] if rows else "unknown"
        title = args.title or f"OpenFaaS pod-churn C#/.NET {scenario} - {pretty_label(mode)} raw latency"
        plot_single(ax, rows, title)
        if args.summary:
            write_summary(rows, args.summary)
    else:
        labels = args.labels or [path.stem for path in args.csv]
        scenario = all_rows[0][0]["scenario"] if all_rows[0] else "unknown"
        title = args.title or f"OpenFaaS pod-churn C#/.NET {scenario} - IL vs AOT"
        plot_overlay(ax, all_rows, labels, title)
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
