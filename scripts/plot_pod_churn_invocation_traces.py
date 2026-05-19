#!/usr/bin/env python3
"""Render real OpenFaaS pod-churn timeline traces from benchmark CSVs."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import matplotlib.ticker as ticker


COLORS = {
    "baseline": "#202020",
    "nopgo": "#202020",
    "il": "#202020",
    "sysimage5": "#D55E00",
    "sysimage10": "#0072B2",
    "pgo5": "#D55E00",
    "pgo10": "#0072B2",
    "pgo-5": "#D55E00",
    "pgo-10": "#0072B2",
    "v8cache": "#0072B2",
    "r2r": "#D55E00",
    "readytorun": "#D55E00",
    "nativeaot": "#0072B2",
    "redis-cache": "#0072B2",
    "xla-cache": "#0072B2",
}
FALLBACK_COLORS = ["#202020", "#D55E00", "#0072B2", "#CC79A7", "#009E73"]


def parse_panel(raw: str) -> tuple[str, list[tuple[str, Path]]]:
    name, _, rest = raw.partition(":")
    if not name or not rest:
        raise argparse.ArgumentTypeError("panel must be name:label=csv,label=csv")
    series: list[tuple[str, Path]] = []
    for item in rest.split(","):
        label, _, path = item.partition("=")
        if not label or not path:
            raise argparse.ArgumentTypeError("series must be label=csv")
        series.append((label, Path(path)))
    return name, series


def value_from_row(row: dict[str, str], metric: str) -> float:
    if metric == "http_latency_ms":
        return float(row.get("http_latency_ms") or row.get("latency_ms"))
    return float(row[metric])


def read_rows(path: Path, metric: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                status = int(row.get("status") or 0)
                if not (200 <= status < 400) or row.get("error", ""):
                    continue
                position_raw = row.get("invocation_in_segment") or row.get("request_in_pod") or row.get("invocation")
                rows.append(
                    {
                        "invocation": int(row["invocation"]),
                        "position": int(position_raw),
                        "churn": str(row.get("churn", "")).lower() in {"1", "true", "yes"},
                        "latency_ms": value_from_row(row, metric),
                        "pod": row.get("pod", ""),
                    }
                )
            except (KeyError, TypeError, ValueError):
                continue
    return rows


def short_label(label: str) -> str:
    return {
        "il": "IL/JIT baseline",
        "r2r": "ReadyToRun AOT",
        "nativeaot": "NativeAOT",
        "redis-cache": "saved XLA artifact",
        "xla-cache": "saved XLA artifact",
        "sysimage5": "AOT sysimage, 5 profiles",
        "sysimage10": "AOT sysimage, 10 profiles",
        "nopgo": "no PGO",
        "pgo5": "PGO, 5 profiles",
        "pgo10": "PGO, 10 profiles",
        "pgo-5": "PGO, 5 profiles",
        "pgo-10": "PGO, 10 profiles",
        "v8cache": "V8 cachedData",
        "baseline": "baseline",
        "saved": "saved artifact",
    }.get(label, label)


def color_for(label: str, index: int) -> str:
    return COLORS.get(label.lower(), FALLBACK_COLORS[index % len(FALLBACK_COLORS)])


def render(
    panels: list[tuple[str, list[tuple[str, Path]]]],
    out: Path,
    summary_path: Path,
    title: str,
    yscale: str,
    metric: str,
    ylabel: str,
) -> None:
    cols = min(3, len(panels))
    rows = math.ceil(len(panels) / cols)
    fig, axes = plt.subplots(rows, cols, figsize=(5.6 * cols, 4.3 * rows), squeeze=False)
    axes_list = [ax for row in axes for ax in row]
    for ax in axes_list[len(panels) :]:
        ax.set_visible(False)
    summary: dict[str, Any] = {"schema": "pod-churn-invocation-traces.v1", "metric": metric, "panels": {}}

    for ax, (panel_name, series_specs) in zip(axes_list, panels):
        panel_summary: dict[str, Any] = {}
        all_churns: set[int] = set()
        max_invocation = 1

        for index, (label, path) in enumerate(series_specs):
            rows = read_rows(path, metric)
            rows.sort(key=lambda row: row["invocation"])
            invocations = [row["invocation"] for row in rows]
            latencies = [row["latency_ms"] for row in rows]
            churns = [row["invocation"] for row in rows if row["churn"]]
            all_churns.update(churns)
            if invocations:
                max_invocation = max(max_invocation, max(invocations))

            ax.plot(
                invocations,
                latencies,
                marker="o",
                markersize=3.8,
                linewidth=1.7,
                color=color_for(label, index),
                label=short_label(label),
            )
            panel_summary[label] = {
                "points": len(rows),
                "churn_invocations": churns,
                "max_latency_ms": max(latencies) if latencies else 0.0,
                "min_latency_ms": min(latencies) if latencies else 0.0,
            }

        for churn in sorted(all_churns):
            ax.axvline(churn, color="#5B84D7", alpha=0.22, linestyle="--", linewidth=1.0)
            ax.axvspan(churn - 0.2, churn + 0.2, color="#5B84D7", alpha=0.08)

        if all_churns:
            shown = ", ".join(str(item) for item in sorted(all_churns))
            ax.text(
                0.03,
                0.95,
                f"new pods at {shown}",
                transform=ax.transAxes,
                va="top",
                ha="left",
                fontsize=8.8,
                bbox={"facecolor": "white", "edgecolor": "#cccccc", "alpha": 0.88},
            )

        ax.set_title(panel_name, fontsize=13)
        ax.set_xlabel("invocation number")
        ax.set_xlim(1, max_invocation)
        if all_churns:
            ax.set_xticks(sorted(all_churns | {max_invocation}))
        else:
            ax.xaxis.set_major_locator(ticker.MaxNLocator(integer=True, nbins=8))
        if yscale != "linear":
            ax.set_yscale(yscale)
        ax.grid(True, axis="y", alpha=0.25)
        summary["panels"][panel_name] = panel_summary

    for index, ax in enumerate(axes_list[: len(panels)]):
        if index % cols == 0:
            ax.set_ylabel(ylabel)
    handles, labels = axes_list[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=min(6, len(handles)), frameon=False, bbox_to_anchor=(0.5, 1.08))
    fig.suptitle(title, fontsize=16, fontweight="bold")
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)

    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--panel", action="append", type=parse_panel, required=True)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--summary", required=True, type=Path)
    parser.add_argument("--title", default="Real OpenFaaS pod-churn invocation traces")
    parser.add_argument("--yscale", choices=["linear", "log"], default="linear")
    parser.add_argument("--metric", default="http_latency_ms")
    parser.add_argument("--ylabel", default="")
    args = parser.parse_args()
    ylabel = args.ylabel or ("gateway latency (ms)" if args.metric == "http_latency_ms" else f"{args.metric} (ms)")
    render(args.panel, args.out, args.summary, args.title, args.yscale, args.metric, ylabel)
    print(f"wrote {args.out}")
    print(f"wrote {args.summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
