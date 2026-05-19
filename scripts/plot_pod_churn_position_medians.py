#!/usr/bin/env python3
"""Render median-by-position pod-churn plots from real CSVs."""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt


COLORS = {
    "baseline": "#202020",
    "il": "#202020",
    "il-baseline": "#202020",
    "sysimage5": "#D55E00",
    "sysimage10": "#0072B2",
    "r2r": "#D55E00",
    "readytorun": "#D55E00",
    "nativeaot": "#0072B2",
    "nopgo": "#202020",
    "pgo5": "#D55E00",
    "pgo10": "#0072B2",
    "pgo-5": "#D55E00",
    "pgo-10": "#0072B2",
    "saved": "#0072B2",
    "v8cache": "#0072B2",
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
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    parsed: list[dict[str, Any]] = []
    for row in rows:
        try:
            status = int(row.get("status") or 0)
            error = row.get("error", "")
            if not (200 <= status < 400) or error:
                continue
            position_raw = row.get("invocation_in_segment") or row.get("request_in_pod") or row.get("invocation")
            parsed.append(
                {
                    "position": int(position_raw),
                    "latency_ms": value_from_row(row, metric),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    return parsed


def medians_by_position(rows: list[dict[str, Any]]) -> dict[int, float]:
    grouped: dict[int, list[float]] = {}
    for row in rows:
        grouped.setdefault(row["position"], []).append(row["latency_ms"])
    return {position: statistics.median(values) for position, values in sorted(grouped.items())}


def phase_value(position_medians: dict[int, float], phase: str) -> float:
    if phase == "cold":
        values = [value for position, value in position_medians.items() if position == 1]
    elif phase == "warmup":
        values = [value for position, value in position_medians.items() if 2 <= position <= 3]
    else:
        values = [value for position, value in position_medians.items() if position >= 4]
    return statistics.median(values) if values else 0.0


def pct_saved(before: float, after: float) -> float:
    return ((before - after) / before * 100.0) if before > 0 else 0.0


def short_label(label: str) -> str:
    return {
        "il": "IL/JIT baseline",
        "r2r": "ReadyToRun AOT",
        "nativeaot": "NativeAOT",
        "nopgo": "no PGO",
        "pgo5": "PGO, 5 profiles",
        "pgo10": "PGO, 10 profiles",
        "pgo-5": "PGO, 5 profiles",
        "pgo-10": "PGO, 10 profiles",
        "sysimage5": "AOT sysimage, 5 profiles",
        "sysimage10": "AOT sysimage, 10 profiles",
        "baseline": "baseline",
        "saved": "saved artifact",
        "v8cache": "V8 cachedData",
        "redis-cache": "saved XLA artifact",
        "xla-cache": "saved XLA artifact",
    }.get(label, label)


def color_for(label: str, index: int) -> str:
    return COLORS.get(label.lower(), FALLBACK_COLORS[index % len(FALLBACK_COLORS)])


def render(
    panels: list[tuple[str, list[tuple[str, Path]]]],
    out: Path,
    summary_path: Path,
    title: str,
    metric: str,
    ylabel: str,
) -> None:
    fig, axes = plt.subplots(1, len(panels), figsize=(5.4 * len(panels), 4.7), squeeze=False)
    axes_list = list(axes[0])
    summary: dict[str, Any] = {"schema": "pod-churn-position-medians.v1", "metric": metric, "panels": {}}

    for ax, (panel_name, series_specs) in zip(axes_list, panels):
        datasets: dict[str, dict[int, float]] = {}
        max_position = 1
        for label, path in series_specs:
            rows = read_rows(path, metric)
            medians = medians_by_position(rows)
            datasets[label] = medians
            if medians:
                max_position = max(max_position, max(medians))

        ax.axvspan(0.8, 1.2, color="#5B84D7", alpha=0.16, label="cold" if ax is axes_list[0] else None)
        ax.axvspan(1.8, 3.2, color="#E69F00", alpha=0.12, label="warmup" if ax is axes_list[0] else None)
        ax.axvspan(3.8, max_position + 0.2, color="#009E73", alpha=0.10, label="hot" if ax is axes_list[0] else None)

        for index, (label, _path) in enumerate(series_specs):
            medians = datasets[label]
            positions = sorted(medians)
            values = [medians[position] for position in positions]
            ax.plot(
                positions,
                values,
                marker="o",
                linewidth=2.2,
                color=color_for(label, index),
                label=short_label(label),
            )

        baseline_label = next((label for label, _ in series_specs if label in {"baseline", "il", "il-baseline"}), series_specs[0][0])
        baseline = datasets.get(baseline_label, {})
        candidates = [label for label, _ in series_specs if label != baseline_label and datasets.get(label)]
        best_label = ""
        best_saved = float("-inf")
        for candidate in candidates:
            shared = sorted(set(baseline) & set(datasets[candidate]))
            if not shared:
                continue
            base_hot = phase_value(baseline, "hot")
            cand_hot = phase_value(datasets[candidate], "hot")
            saved = pct_saved(base_hot, cand_hot)
            if saved > best_saved:
                best_saved = saved
                best_label = candidate

        if best_label:
            shared = sorted(set(baseline) & set(datasets[best_label]))
            wins = sum(1 for position in shared if datasets[best_label][position] < baseline[position])
            cold_saved = pct_saved(phase_value(baseline, "cold"), phase_value(datasets[best_label], "cold"))
            hot_saved = pct_saved(phase_value(baseline, "hot"), phase_value(datasets[best_label], "hot"))
            ax.text(
                0.03,
                0.95,
                f"best {short_label(best_label)}\n"
                f"{wins}/{len(shared)} median-position wins\n"
                f"cold saved {cold_saved:.1f}%\n"
                f"hot saved {hot_saved:.1f}%",
                transform=ax.transAxes,
                va="top",
                ha="left",
                fontsize=9.2,
                bbox={"facecolor": "white", "edgecolor": "#cccccc", "alpha": 0.88},
            )

        panel_summary: dict[str, Any] = {}
        for label, _path in series_specs:
            medians = datasets[label]
            panel_summary[label] = {
                "positions": medians,
                "cold_median_ms": phase_value(medians, "cold"),
                "warmup_median_ms": phase_value(medians, "warmup"),
                "hot_median_ms": phase_value(medians, "hot"),
            }
        summary["panels"][panel_name] = panel_summary

        ax.set_title(panel_name, fontsize=13)
        ax.set_xlabel("request in fresh pod")
        ax.grid(True, axis="y", alpha=0.25)
        ax.set_xlim(0.8, max_position + 0.2)

    axes_list[0].set_ylabel(ylabel)
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
    parser.add_argument("--title", default="Real OpenFaaS pod-churn median-position traces")
    parser.add_argument("--metric", default="http_latency_ms")
    parser.add_argument("--ylabel", default="")
    args = parser.parse_args()
    ylabel = args.ylabel or ("median gateway latency (ms)" if args.metric == "http_latency_ms" else f"median {args.metric} (ms)")
    render(args.panel, args.out, args.summary, args.title, args.metric, ylabel)
    print(f"wrote {args.out}")
    print(f"wrote {args.summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
