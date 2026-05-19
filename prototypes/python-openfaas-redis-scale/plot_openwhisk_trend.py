#!/usr/bin/env python3
"""Plot Python/OpenFaaS OpenWhisk-style raw latency traces."""

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
                    "handler_ms": float(row.get("handler_elapsed_ms") or 0.0),
                    "status": int(row.get("status") or 0),
                    "churn": row.get("churn") == "1",
                    "benchmark": row.get("benchmark", ""),
                    "treatment": row.get("treatment", ""),
                    "checksum": row.get("checksum", ""),
                    "error": row.get("error", ""),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    return parsed


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


def filter_rows(all_rows: list[list[dict[str, Any]]], labels: list[str]) -> list[list[dict[str, Any]]]:
    baseline_index = labels.index("baseline") if "baseline" in labels else -1
    if baseline_index < 0:
        return all_rows
    baseline_checksums = {
        row["invocation"]: row["checksum"]
        for row in all_rows[baseline_index]
        if 200 <= row["status"] < 400 and not row["error"] and row["checksum"]
    }
    filtered = []
    for rows, label in zip(all_rows, labels):
        current = []
        for row in rows:
            if not (200 <= row["status"] < 400) or row["error"]:
                continue
            if label != "baseline" and row["checksum"] != baseline_checksums.get(row["invocation"]):
                continue
            current.append(row)
        filtered.append(current)
    return filtered


def write_summary(all_rows: list[list[dict[str, Any]]], labels: list[str], out: Path) -> None:
    summary: dict[str, Any] = {"schema": "python-openfaas-openwhisk-trend-summary.v1", "series": {}}
    for rows, label in zip(all_rows, labels):
        values = [row["latency_ms"] for row in rows]
        summary["series"][label] = {
            "ok": len(rows),
            "churn_points": [row["invocation"] for row in rows if row["churn"]],
            "mean_ms": statistics.fmean(values) if values else 0.0,
            "p50_ms": percentile(values, 50),
            "p95_ms": percentile(values, 95),
            "p99_ms": percentile(values, 99),
            "min_ms": min(values) if values else 0.0,
            "max_ms": max(values) if values else 0.0,
        }
    if "baseline" in summary["series"] and "saved" in summary["series"]:
        base = summary["series"]["baseline"]["p50_ms"]
        saved = summary["series"]["saved"]["p50_ms"]
        summary["p50_saved_pct"] = ((base - saved) / base * 100.0) if base > 0 else 0.0
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", nargs="+", required=True, type=Path)
    parser.add_argument("--labels", nargs="+", required=True)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--summary", required=True, type=Path)
    parser.add_argument("--title", default="Real OpenFaaS Python OpenWhisk-style churn trace")
    parser.add_argument("--ewma-alpha", type=float, default=0.10)
    parser.add_argument("--dpi", type=int, default=170)
    args = parser.parse_args()

    all_rows = filter_rows([read_csv(path) for path in args.csv], args.labels)
    if not any(all_rows):
        raise SystemExit("no successful rows to plot")

    fig, ax = plt.subplots(1, 1, figsize=(12.8, 5.4))
    churn_points = sorted({row["invocation"] for rows in all_rows for row in rows if row["churn"]})
    for first, point in enumerate(churn_points):
        ax.axvline(
            point,
            color=CHURN_COLOR,
            linestyle="--",
            linewidth=1.0,
            alpha=0.58,
            label="pod restart (churn)" if first == 0 else "_nolegend_",
            zorder=1,
        )

    for index, (rows, label) in enumerate(zip(all_rows, args.labels)):
        x = np.array([row["invocation"] for row in rows])
        y = np.array([row["latency_ms"] for row in rows])
        color = COLORS.get(label, f"C{index}")
        ax.plot(x, y, color=color, linewidth=0.8, alpha=0.24, label=f"{label} raw", zorder=2 + index)
        if len(y) > 0:
            ax.plot(
                x,
                ewma(y.tolist(), args.ewma_alpha),
                color=color,
                linewidth=2.8,
                alpha=0.96,
                label=f"{label} EWMA",
                zorder=5 + index,
            )

    ax.set_xlabel("Request index", fontsize=12)
    ax.set_ylabel("End-to-end latency (ms)", fontsize=12)
    ax.set_title(args.title, fontsize=14, fontweight="bold")
    ax.set_xlim(left=0)
    ax.set_ylim(bottom=0)
    ax.xaxis.set_major_locator(ticker.MaxNLocator(integer=True, nbins=10))
    ax.grid(axis="y", color="#e0e0e0", linewidth=0.6)
    ax.legend(loc="upper right", fontsize=10, framealpha=0.92)
    plt.tight_layout()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.out, dpi=args.dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    write_summary(all_rows, args.labels, args.summary)
    print(f"wrote {args.out}")
    print(f"wrote {args.summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
