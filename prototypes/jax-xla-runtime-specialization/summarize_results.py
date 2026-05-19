#!/usr/bin/env python3
"""Summarize JAX/XLA compile-cache measurements and render a small SVG."""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any


def read_rows(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        row["iteration"] = int(row["iteration"])
        row["compile_or_load_ms"] = float(row["compile_or_load_ms"])
        row["execute_ms_median"] = float(row["execute_ms_median"])
        row["cache_files"] = int(row["cache_files"])
        row["cache_bytes"] = int(row["cache_bytes"])
    return rows


def median_by_signature(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["signature"]].append(row)
    return {
        signature: {
            "compileOrLoadMsMedian": statistics.median(row["compile_or_load_ms"] for row in values),
            "executeMsMedian": statistics.median(row["execute_ms_median"] for row in values),
            "cacheFilesLast": values[-1]["cache_files"],
            "cacheBytesLast": values[-1]["cache_bytes"],
        }
        for signature, values in sorted(grouped.items())
    }


def load_named(inputs: list[str]) -> dict[str, list[dict[str, Any]]]:
    datasets = {}
    for item in inputs:
        if "=" not in item:
            raise ValueError("--input must be label=path")
        label, raw_path = item.split("=", 1)
        datasets[label] = read_rows(Path(raw_path))
    return datasets


def summarize(datasets: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    by_label = {label: median_by_signature(rows) for label, rows in datasets.items()}
    signatures = sorted({signature for values in by_label.values() for signature in values})
    comparisons = {}
    if "no-cache" in by_label and "persistent-cache-reuse" in by_label:
        for signature in signatures:
            baseline = by_label["no-cache"].get(signature, {}).get("compileOrLoadMsMedian")
            reused = by_label["persistent-cache-reuse"].get(signature, {}).get("compileOrLoadMsMedian")
            if baseline is not None and reused is not None and baseline > 0:
                comparisons[signature] = {
                    "compileLoadSpeedup": baseline / max(reused, 0.000001),
                    "compileLoadMsSaved": baseline - reused,
                }
    return {
        "schema": "jax-xla-runtime-specialization-summary.v1",
        "labels": by_label,
        "comparisons": comparisons,
    }


def render_svg(summary: dict[str, Any], out: Path) -> None:
    labels = list(summary["labels"].keys())
    signatures = sorted({sig for values in summary["labels"].values() for sig in values})
    if not labels or not signatures:
        return

    width = 980
    height = 160 + 44 * len(signatures) * len(labels)
    left = 190
    chart_width = 660
    row_h = 28
    gap = 16
    max_value = max(
        summary["labels"][label][sig]["compileOrLoadMsMedian"]
        for label in labels
        for sig in signatures
        if sig in summary["labels"][label]
    )
    max_value = max(max_value, 1.0)
    colors = {
        "no-cache": "#b9413c",
        "persistent-cache-populate": "#526d94",
        "persistent-cache-reuse": "#2f7d59",
    }

    y = 86
    lines = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="{0}" height="{1}" viewBox="0 0 {0} {1}">'.format(
            width, height
        ),
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<text x="24" y="36" font-family="Helvetica, Arial, sans-serif" font-size="22" font-weight="700" fill="#202124">JAX/XLA compile-or-load time by runtime signature</text>',
        '<text x="24" y="60" font-family="Helvetica, Arial, sans-serif" font-size="13" fill="#5f6368">Lower is better. Persistent-cache reuse is a fresh Python process reading artifacts from disk.</text>',
    ]
    for signature in signatures:
        lines.append(
            f'<text x="24" y="{y + 18}" font-family="Helvetica, Arial, sans-serif" '
            f'font-size="14" font-weight="700" fill="#202124">{signature}</text>'
        )
        for label in labels:
            if signature not in summary["labels"][label]:
                continue
            value = summary["labels"][label][signature]["compileOrLoadMsMedian"]
            bar_w = max(2.0, (value / max_value) * chart_width)
            color = colors.get(label, "#6f6f6f")
            lines.append(
                f'<text x="{left - 8}" y="{y + 18}" text-anchor="end" '
                f'font-family="Helvetica, Arial, sans-serif" font-size="12" fill="#3c4043">{label}</text>'
            )
            lines.append(
                f'<rect x="{left}" y="{y}" width="{bar_w:.2f}" height="{row_h}" '
                f'rx="3" fill="{color}"/>'
            )
            lines.append(
                f'<text x="{left + bar_w + 8:.2f}" y="{y + 18}" '
                f'font-family="Helvetica, Arial, sans-serif" font-size="12" fill="#202124">{value:.2f} ms</text>'
            )
            y += row_h + 5
        y += gap
    lines.append("</svg>")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def render_speedup_svg(summary: dict[str, Any], out: Path) -> None:
    comparisons = summary.get("comparisons", {})
    signatures = sorted(comparisons)
    if not signatures:
        return

    width = 920
    height = 150 + 58 * len(signatures)
    left = 190
    chart_width = 560
    row_h = 30
    max_value = max(float(comparisons[sig]["compileLoadSpeedup"]) for sig in signatures)
    max_value = max(max_value, 1.0)

    y = 88
    lines = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="{0}" height="{1}" viewBox="0 0 {0} {1}">'.format(
            width, height
        ),
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<text x="24" y="36" font-family="Helvetica, Arial, sans-serif" font-size="22" font-weight="700" fill="#202124">JAX/XLA persistent-cache speedup</text>',
        '<text x="24" y="60" font-family="Helvetica, Arial, sans-serif" font-size="13" fill="#5f6368">No-cache compile-or-load time divided by fresh-process persistent-cache reuse time. Higher is better.</text>',
    ]
    for signature in signatures:
        value = float(comparisons[signature]["compileLoadSpeedup"])
        saved = float(comparisons[signature]["compileLoadMsSaved"])
        bar_w = max(2.0, (value / max_value) * chart_width)
        lines.append(
            f'<text x="24" y="{y + 20}" font-family="Helvetica, Arial, sans-serif" '
            f'font-size="14" font-weight="700" fill="#202124">{signature}</text>'
        )
        lines.append(
            f'<rect x="{left}" y="{y}" width="{bar_w:.2f}" height="{row_h}" rx="3" fill="#2f7d59"/>'
        )
        lines.append(
            f'<text x="{left + bar_w + 8:.2f}" y="{y + 20}" '
            f'font-family="Helvetica, Arial, sans-serif" font-size="12" fill="#202124">{value:.2f}x, {saved:.2f} ms saved</text>'
        )
        y += 58
    lines.append("</svg>")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def render_invocation_curve(datasets: dict[str, list[dict[str, Any]]], out: Path) -> None:
    labels = list(datasets)
    signatures = sorted({row["signature"] for rows in datasets.values() for row in rows})
    if not labels or not signatures:
        return

    width = 1120
    panel_h = 280
    top = 78
    bottom = 66
    panel_gap = 48
    height = top + bottom + len(signatures) * panel_h + (len(signatures) - 1) * panel_gap
    left = 92
    right = 230
    chart_w = width - left - right
    chart_h = panel_h - 54
    colors = {
        "no-cache": "#b9413c",
        "persistent-cache-populate": "#526d94",
        "persistent-cache-reuse": "#2f7d59",
    }

    max_iteration = max(int(row["iteration"]) for rows in datasets.values() for row in rows)
    max_latency = max(float(row["compile_or_load_ms"]) for rows in datasets.values() for row in rows)
    y_max = nice_max(max_latency * 1.08)

    lines = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="{0}" height="{1}" viewBox="0 0 {0} {1}">'.format(
            width, height
        ),
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<text x="24" y="36" font-family="Helvetica, Arial, sans-serif" font-size="22" font-weight="700" fill="#202124">JAX/XLA latency vs invocation number</text>',
        '<text x="24" y="60" font-family="Helvetica, Arial, sans-serif" font-size="13" fill="#5f6368">Compile-or-load latency for DaCapo-shaped JAX signatures. Lower is better.</text>',
    ]

    for panel_index, signature in enumerate(signatures):
        panel_top = top + panel_index * (panel_h + panel_gap)
        lines.append(
            f'<text x="24" y="{panel_top + 18}" font-family="Helvetica, Arial, sans-serif" '
            f'font-size="16" font-weight="700" fill="#202124">{signature}</text>'
        )
        frame_top = panel_top + 34
        lines.extend(curve_frame(left, frame_top, chart_w, chart_h, y_max, max_iteration))
        for label in labels:
            rows = sorted(
                [row for row in datasets[label] if row["signature"] == signature],
                key=lambda row: int(row["iteration"]),
            )
            if not rows:
                continue
            color = colors.get(label, "#6f6f6f")
            points = []
            for row in rows:
                x = left + ((int(row["iteration"]) - 1) / max(max_iteration - 1, 1)) * chart_w
                y = frame_top + chart_h - (float(row["compile_or_load_ms"]) / y_max) * chart_h
                points.append((x, y))
            lines.append(polyline(points, color))
            for x, y in points:
                lines.append(circle(x, y, 3.0, color))
        if panel_index == len(signatures) - 1:
            lines.append(
                f'<text x="{left + chart_w / 2}" y="{frame_top + chart_h + 42}" '
                f'font-family="Helvetica, Arial, sans-serif" font-size="13" font-weight="600" '
                f'text-anchor="middle" fill="#334155">Invocation number</text>'
            )

    legend_x = width - right + 28
    legend_y = top + 42
    for index, label in enumerate(labels):
        y = legend_y + index * 26
        color = colors.get(label, "#6f6f6f")
        lines.append(f'<line x1="{legend_x}" y1="{y}" x2="{legend_x + 30}" y2="{y}" stroke="{color}" stroke-width="3"/>')
        lines.append(circle(legend_x + 15, y, 4, color))
        lines.append(
            f'<text x="{legend_x + 42}" y="{y + 4}" font-family="Helvetica, Arial, sans-serif" '
            f'font-size="12" font-weight="600" fill="#334155">{label}</text>'
        )

    lines.append("</svg>")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def curve_frame(
    left: int,
    top: int,
    chart_w: int,
    chart_h: int,
    y_max: float,
    max_iteration: int,
) -> list[str]:
    lines = []
    for tick in range(6):
        value = y_max * tick / 5
        y = top + chart_h - (value / y_max) * chart_h
        lines.append(f'<line x1="{left}" y1="{y:.2f}" x2="{left + chart_w}" y2="{y:.2f}" stroke="#e2e8f0" stroke-width="1"/>')
        lines.append(
            f'<text x="{left - 12}" y="{y + 4:.2f}" font-family="Helvetica, Arial, sans-serif" '
            f'font-size="11" text-anchor="end" fill="#64748b">{value:.0f}</text>'
        )
    lines.append(f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + chart_h}" stroke="#64748b" stroke-width="1.2"/>')
    lines.append(f'<line x1="{left}" y1="{top + chart_h}" x2="{left + chart_w}" y2="{top + chart_h}" stroke="#64748b" stroke-width="1.2"/>')
    for iteration in range(1, max_iteration + 1):
        if iteration == 1 or iteration == max_iteration or iteration % 5 == 0:
            x = left + ((iteration - 1) / max(max_iteration - 1, 1)) * chart_w
            lines.append(f'<line x1="{x:.2f}" y1="{top + chart_h}" x2="{x:.2f}" y2="{top + chart_h + 6}" stroke="#64748b" stroke-width="1"/>')
            lines.append(
                f'<text x="{x:.2f}" y="{top + chart_h + 22}" font-family="Helvetica, Arial, sans-serif" '
                f'font-size="11" text-anchor="middle" fill="#475569">{iteration}</text>'
            )
    lines.append(
        f'<text x="24" y="{top + chart_h / 2:.2f}" font-family="Helvetica, Arial, sans-serif" '
        f'font-size="12" font-weight="600" text-anchor="middle" fill="#334155" '
        f'transform="rotate(-90 24 {top + chart_h / 2:.2f})">Compile/load ms</text>'
    )
    return lines


def nice_max(value: float) -> float:
    if value <= 10:
        return 10.0
    step = 10 ** (len(str(int(value))) - 1)
    for scale in (1, 2, 5, 10):
        candidate = step * scale
        if candidate >= value:
            return float(candidate)
    return float(step * 10)


def polyline(points: list[tuple[float, float]], color: str) -> str:
    encoded = " ".join(f"{x:.2f},{y:.2f}" for x, y in points)
    return f'<polyline points="{encoded}" fill="none" stroke="{color}" stroke-width="2.4" stroke-linejoin="round" stroke-linecap="round"/>'


def circle(x: float, y: float, radius: float, color: str) -> str:
    return f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{radius:.2f}" fill="{color}"/>'


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", action="append", required=True, help="label=csv path; repeatable")
    parser.add_argument("--summary", required=True, type=Path)
    parser.add_argument("--svg", required=True, type=Path)
    parser.add_argument("--speedup-svg", type=Path)
    parser.add_argument("--invocation-svg", type=Path)
    args = parser.parse_args()

    datasets = load_named(args.input)
    summary = summarize(datasets)
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    render_svg(summary, args.svg)
    if args.speedup_svg:
        render_speedup_svg(summary, args.speedup_svg)
    if args.invocation_svg:
        render_invocation_curve(datasets, args.invocation_svg)
    print(f"wrote summary: {args.summary}")
    print(f"wrote figure:  {args.svg}")
    if args.speedup_svg:
        print(f"wrote figure:  {args.speedup_svg}")
    if args.invocation_svg:
        print(f"wrote figure:  {args.invocation_svg}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
