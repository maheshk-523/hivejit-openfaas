#!/usr/bin/env python3
"""Summarize and plot the real JAX workload cache experiment."""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any


NUMERIC_FIELDS = [
    "trace_ms",
    "lower_ms",
    "compile_or_load_ms",
    "first_execute_ms",
    "execute_ms_median",
    "handler_ms",
    "startup_plus_first_request_ms",
    "artifact_import_ms",
    "artifact_export_ms",
]


def read_rows(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        row["iteration"] = int(row["iteration"])
        for field in NUMERIC_FIELDS:
            row[field] = float(row[field])
        for field in (
            "cache_files_before",
            "cache_bytes_before",
            "cache_files_after",
            "cache_bytes_after",
            "archive_bytes",
        ):
            row[field] = int(row[field])
        row["cache_enabled"] = str(row["cache_enabled"]).lower() == "true"
        row["artifact_imported"] = str(row["artifact_imported"]).lower() == "true"
    return rows


def load_named(inputs: list[str]) -> dict[str, list[dict[str, Any]]]:
    datasets = {}
    for item in inputs:
        if "=" not in item:
            raise ValueError("--input must be label=path")
        label, raw_path = item.split("=", 1)
        datasets[label] = read_rows(Path(raw_path))
    return datasets


def median(values: list[float]) -> float:
    if not values:
        return 0.0
    return float(statistics.median(values))


def median_by_scenario(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["scenario"]].append(row)
    summary = {}
    for scenario, values in sorted(grouped.items()):
        summary[scenario] = {
            "trials": len(values),
            "traceMsMedian": median([row["trace_ms"] for row in values]),
            "lowerMsMedian": median([row["lower_ms"] for row in values]),
            "compileOrLoadMsMedian": median([row["compile_or_load_ms"] for row in values]),
            "firstExecuteMsMedian": median([row["first_execute_ms"] for row in values]),
            "handlerMsMedian": median([row["handler_ms"] for row in values]),
            "startupPlusFirstRequestMsMedian": median(
                [row["startup_plus_first_request_ms"] for row in values]
            ),
            "artifactImportMsMedian": median([row["artifact_import_ms"] for row in values]),
            "artifactExportMsMedian": median([row["artifact_export_ms"] for row in values]),
            "cacheFilesLast": values[-1]["cache_files_after"],
            "cacheBytesLast": values[-1]["cache_bytes_after"],
            "archiveBytesLast": values[-1]["archive_bytes"],
        }
    return summary


def summarize(datasets: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    by_label = {label: median_by_scenario(rows) for label, rows in datasets.items()}
    scenarios = sorted({scenario for values in by_label.values() for scenario in values})
    comparisons = {}
    if "baseline" in by_label and "persistent-cache-reuse" in by_label:
        for scenario in scenarios:
            baseline = by_label["baseline"].get(scenario)
            reuse = by_label["persistent-cache-reuse"].get(scenario)
            if not baseline or not reuse:
                continue
            base_compile = baseline["compileOrLoadMsMedian"]
            reuse_compile = reuse["compileOrLoadMsMedian"]
            base_total = baseline["startupPlusFirstRequestMsMedian"]
            reuse_total = reuse["startupPlusFirstRequestMsMedian"]
            comparisons[scenario] = {
                "compileLoadSpeedup": base_compile / max(reuse_compile, 0.000001),
                "compileLoadMsSaved": base_compile - reuse_compile,
                "firstRequestSpeedup": base_total / max(reuse_total, 0.000001),
                "firstRequestMsSaved": base_total - reuse_total,
            }
    return {
        "schema": "jax-real-workload-cache-summary.v1",
        "labels": by_label,
        "comparisons": comparisons,
    }


def svg_header(width: int, height: int, title: str, subtitle: str) -> list[str]:
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#111827"/>',
        f'<text x="28" y="36" font-family="Helvetica, Arial, sans-serif" font-size="21" font-weight="700" fill="#f9fafb">{escape(title)}</text>',
        f'<text x="28" y="60" font-family="Helvetica, Arial, sans-serif" font-size="13" fill="#cbd5e1">{escape(subtitle)}</text>',
    ]


def escape(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def render_phase_svg(summary: dict[str, Any], out: Path) -> None:
    labels = [label for label in ("baseline", "persistent-cache-reuse") if label in summary["labels"]]
    scenarios = sorted({scenario for label in labels for scenario in summary["labels"][label]})
    if not labels or not scenarios:
        return

    phases = [
        ("artifactImportMsMedian", "artifact import", "#f59e0b"),
        ("traceMsMedian", "trace", "#38bdf8"),
        ("lowerMsMedian", "lower", "#818cf8"),
        ("compileOrLoadMsMedian", "compile/load", "#ef4444"),
        ("firstExecuteMsMedian", "execute", "#22c55e"),
    ]
    width = 1220
    row_h = 36
    scenario_gap = 34
    top = 96
    height = top + len(scenarios) * (len(labels) * (row_h + 12) + scenario_gap) + 88
    left = 230
    chart_w = 760
    max_value = max(
        sum(float(summary["labels"][label][scenario].get(field, 0.0)) for field, _name, _color in phases)
        for label in labels
        for scenario in scenarios
        if scenario in summary["labels"][label]
    )
    max_value = max(max_value, 1.0)

    lines = svg_header(
        width,
        height,
        "JAX real-workload first-request phase breakdown",
        "Fresh process baseline vs restored persistent compilation cache. Lower is better.",
    )
    y = top
    for scenario in scenarios:
        lines.append(
            f'<text x="28" y="{y + 20}" font-family="Helvetica, Arial, sans-serif" '
            f'font-size="15" font-weight="700" fill="#f9fafb">{escape(scenario)}</text>'
        )
        y += 28
        for label in labels:
            if scenario not in summary["labels"][label]:
                continue
            data = summary["labels"][label][scenario]
            lines.append(
                f'<text x="{left - 12}" y="{y + 23}" text-anchor="end" '
                f'font-family="Helvetica, Arial, sans-serif" font-size="12" fill="#d1d5db">{escape(label)}</text>'
            )
            x = left
            total = 0.0
            for field, _name, color in phases:
                value = float(data.get(field, 0.0))
                total += value
                width_px = (value / max_value) * chart_w
                if width_px > 0:
                    lines.append(
                        f'<rect x="{x:.2f}" y="{y}" width="{max(width_px, 1.5):.2f}" '
                        f'height="{row_h}" rx="3" fill="{color}"/>'
                    )
                x += width_px
            lines.append(
                f'<text x="{left + chart_w + 18}" y="{y + 23}" font-family="Helvetica, Arial, sans-serif" '
                f'font-size="12" fill="#f9fafb">{total:.1f} ms</text>'
            )
            y += row_h + 12
        y += scenario_gap

    legend_x = 28
    legend_y = height - 50
    x = legend_x
    for _field, name, color in phases:
        lines.append(f'<rect x="{x}" y="{legend_y}" width="16" height="16" rx="3" fill="{color}"/>')
        lines.append(
            f'<text x="{x + 22}" y="{legend_y + 13}" font-family="Helvetica, Arial, sans-serif" '
            f'font-size="12" fill="#d1d5db">{escape(name)}</text>'
        )
        x += 138
    lines.append("</svg>")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def render_compile_svg(summary: dict[str, Any], out: Path) -> None:
    labels = [label for label in ("baseline", "persistent-cache-populate", "persistent-cache-reuse", "mismatch-control") if label in summary["labels"]]
    scenarios = sorted({scenario for label in labels for scenario in summary["labels"][label]})
    if not labels or not scenarios:
        return

    width = 1180
    top = 96
    row_h = 26
    height = top + len(scenarios) * (len(labels) * (row_h + 8) + 34) + 40
    left = 235
    chart_w = 730
    colors = {
        "baseline": "#ef4444",
        "persistent-cache-populate": "#94a3b8",
        "persistent-cache-reuse": "#22c55e",
        "mismatch-control": "#f59e0b",
    }
    max_value = max(
        summary["labels"][label][scenario]["compileOrLoadMsMedian"]
        for label in labels
        for scenario in scenarios
        if scenario in summary["labels"][label]
    )
    max_value = max(max_value, 1.0)
    lines = svg_header(
        width,
        height,
        "JAX/XLA persistent-cache compile-or-load time",
        "The reuse rows are fresh Python processes reading a restored cache artifact.",
    )
    y = top
    for scenario in scenarios:
        lines.append(
            f'<text x="28" y="{y + 18}" font-family="Helvetica, Arial, sans-serif" '
            f'font-size="15" font-weight="700" fill="#f9fafb">{escape(scenario)}</text>'
        )
        y += 26
        for label in labels:
            if scenario not in summary["labels"][label]:
                continue
            value = float(summary["labels"][label][scenario]["compileOrLoadMsMedian"])
            bar_w = max(2.0, (value / max_value) * chart_w)
            color = colors.get(label, "#64748b")
            lines.append(
                f'<text x="{left - 12}" y="{y + 18}" text-anchor="end" '
                f'font-family="Helvetica, Arial, sans-serif" font-size="12" fill="#d1d5db">{escape(label)}</text>'
            )
            lines.append(
                f'<rect x="{left}" y="{y}" width="{bar_w:.2f}" height="{row_h}" rx="3" fill="{color}"/>'
            )
            lines.append(
                f'<text x="{left + bar_w + 10:.2f}" y="{y + 18}" font-family="Helvetica, Arial, sans-serif" '
                f'font-size="12" fill="#f9fafb">{value:.1f} ms</text>'
            )
            y += row_h + 8
        y += 34
    lines.append("</svg>")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def render_speedup_svg(summary: dict[str, Any], out: Path) -> None:
    comparisons = summary.get("comparisons", {})
    scenarios = sorted(comparisons)
    if not scenarios:
        return
    width = 980
    height = 112 + len(scenarios) * 58
    left = 210
    chart_w = 520
    max_value = max(float(comparisons[scenario]["compileLoadSpeedup"]) for scenario in scenarios)
    max_value = max(max_value, 1.0)
    lines = svg_header(
        width,
        height,
        "Compile-cache speedup on real JAX workload",
        "Baseline compile/load divided by restored-cache compile/load. Higher is better.",
    )
    y = 94
    for scenario in scenarios:
        value = float(comparisons[scenario]["compileLoadSpeedup"])
        saved = float(comparisons[scenario]["compileLoadMsSaved"])
        bar_w = max(2.0, (value / max_value) * chart_w)
        lines.append(
            f'<text x="28" y="{y + 20}" font-family="Helvetica, Arial, sans-serif" '
            f'font-size="14" font-weight="700" fill="#f9fafb">{escape(scenario)}</text>'
        )
        lines.append(
            f'<rect x="{left}" y="{y}" width="{bar_w:.2f}" height="30" rx="3" fill="#22c55e"/>'
        )
        lines.append(
            f'<text x="{left + bar_w + 10:.2f}" y="{y + 20}" font-family="Helvetica, Arial, sans-serif" '
            f'font-size="12" fill="#f9fafb">{value:.2f}x, {saved:.1f} ms saved</text>'
        )
        y += 58
    lines.append("</svg>")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", action="append", required=True, help="label=csv path; repeatable")
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--phase-svg", type=Path, required=True)
    parser.add_argument("--compile-svg", type=Path, required=True)
    parser.add_argument("--speedup-svg", type=Path, required=True)
    args = parser.parse_args()

    datasets = load_named(args.input)
    summary = summarize(datasets)
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    render_phase_svg(summary, args.phase_svg)
    render_compile_svg(summary, args.compile_svg)
    render_speedup_svg(summary, args.speedup_svg)
    print(f"wrote summary: {args.summary}")
    print(f"wrote phase figure: {args.phase_svg}")
    print(f"wrote compile figure: {args.compile_svg}")
    print(f"wrote speedup figure: {args.speedup_svg}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
