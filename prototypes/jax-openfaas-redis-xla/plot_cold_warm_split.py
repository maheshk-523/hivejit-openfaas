#!/usr/bin/env python3
"""Render split cold/warm warmup plots with a full view and warm-start zoom."""

from __future__ import annotations

import argparse
import csv
import math
import statistics
from pathlib import Path
from typing import Any


COLORS = {"baseline": "#1f77b4", "redis-cache": "#2ca02c"}


def read_rows(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        row["invocation"] = int(row["invocation"])
        row["latency_ms"] = float(row["latency_ms"])
        row["compile_or_load_ms"] = float(row.get("compile_or_load_ms") or 0.0)
    return sorted(rows, key=lambda row: row["invocation"])


def load_inputs(inputs: list[str]) -> dict[str, list[dict[str, Any]]]:
    datasets = {}
    for item in inputs:
        if "=" not in item:
            raise ValueError("--input must use label=path")
        label, raw_path = item.split("=", 1)
        datasets[label] = read_rows(Path(raw_path))
    return datasets


def svg_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def percent_saved(before: float, after: float) -> float:
    if before <= 0:
        return 0.0
    return (before - after) / before * 100.0


def first_row(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return sorted(rows, key=lambda row: row["invocation"])[0]


def warm_median(rows: list[dict[str, Any]]) -> float:
    warm_rows = [row for row in rows if row["invocation"] >= 2]
    if not warm_rows:
        return first_row(rows)["latency_ms"]
    return statistics.median(row["latency_ms"] for row in warm_rows)


def improvement_lines(datasets: dict[str, list[dict[str, Any]]]) -> list[str]:
    if "baseline" not in datasets or "redis-cache" not in datasets:
        return []

    baseline_first = first_row(datasets["baseline"])
    redis_first = first_row(datasets["redis-cache"])
    baseline_warm = warm_median(datasets["baseline"])
    cold_drop = percent_saved(baseline_first["latency_ms"], redis_first["latency_ms"])
    compile_drop = percent_saved(baseline_first["compile_or_load_ms"], redis_first["compile_or_load_ms"])
    warmup_factor = baseline_first["latency_ms"] / baseline_warm if baseline_warm > 0 else 0.0

    return [
        (
            "Redis/XLA cache cold peak: "
            f"{baseline_first['latency_ms']:.1f} -> {redis_first['latency_ms']:.1f} ms "
            f"({cold_drop:.1f}% lower)."
        ),
        (
            "Compile/load on first request: "
            f"{baseline_first['compile_or_load_ms']:.1f} -> {redis_first['compile_or_load_ms']:.1f} ms "
            f"({compile_drop:.1f}% lower); "
            f"same-pod JIT warmup: {warmup_factor:.1f}x lower than baseline cold."
        ),
    ]


def nice_max(value: float) -> float:
    if value <= 0:
        return 1.0
    exponent = math.floor(math.log10(value))
    fraction = value / (10**exponent)
    if fraction <= 1:
        nice = 1
    elif fraction <= 2:
        nice = 2
    elif fraction <= 5:
        nice = 5
    else:
        nice = 10
    return nice * (10**exponent)


def draw_panel(
    lines: list[str],
    datasets: dict[str, list[dict[str, Any]]],
    *,
    x: int,
    y: int,
    width: int,
    height: int,
    title: str,
    start_invocation: int,
    tight: bool,
) -> None:
    rows = [row for values in datasets.values() for row in values if row["invocation"] >= start_invocation]
    min_inv = min(row["invocation"] for row in rows)
    max_inv = max(row["invocation"] for row in rows)
    min_latency = min(row["latency_ms"] for row in rows)
    max_latency = max(row["latency_ms"] for row in rows)
    if tight:
        pad = max((max_latency - min_latency) * 0.2, 0.5)
        y_min = max(0.0, min_latency - pad)
        y_max = max_latency + pad
    else:
        y_min = 0.0
        y_max = nice_max(max_latency * 1.08)
    if y_max <= y_min:
        y_max = y_min + 1.0

    def x_scale(invocation: int) -> float:
        return x + ((invocation - min_inv) / max(max_inv - min_inv, 1)) * width

    def y_scale(latency: float) -> float:
        return y + height - ((latency - y_min) / (y_max - y_min)) * height

    lines.append(
        f'<text x="{x}" y="{y - 16}" font-family="Helvetica, Arial, sans-serif" '
        f'font-size="16" font-weight="700" fill="#111827">{svg_escape(title)}</text>'
    )
    for index in range(6):
        value = y_min + (y_max - y_min) * index / 5
        yy = y_scale(value)
        label = f"{value:.1f}" if tight else f"{value:.0f}"
        lines.append(f'<line x1="{x}" y1="{yy:.1f}" x2="{x + width}" y2="{yy:.1f}" stroke="#c7c7c7"/>')
        lines.append(
            f'<text x="{x - 10}" y="{yy + 4:.1f}" text-anchor="end" '
            f'font-family="Helvetica, Arial, sans-serif" font-size="12" fill="#111827">{label}</text>'
        )

    for invocation in range(min_inv, max_inv + 1):
        xx = x_scale(invocation)
        if invocation == min_inv or invocation == max_inv or invocation % 2 == 0:
            lines.append(f'<line x1="{xx:.1f}" y1="{y}" x2="{xx:.1f}" y2="{y + height}" stroke="#d6d6d6"/>')
            lines.append(
                f'<text x="{xx:.1f}" y="{y + height + 22}" text-anchor="middle" '
                f'font-family="Helvetica, Arial, sans-serif" font-size="12" fill="#111827">{invocation}</text>'
            )

    lines.append(f'<line x1="{x}" y1="{y + height}" x2="{x + width}" y2="{y + height}" stroke="#111827"/>')
    lines.append(f'<line x1="{x}" y1="{y}" x2="{x}" y2="{y + height}" stroke="#111827"/>')

    if start_invocation == 1:
        xx = x_scale(1)
        lines.append(
            f'<line x1="{xx:.1f}" y1="{y}" x2="{xx:.1f}" y2="{y + height}" '
            'stroke="#6b7280" stroke-dasharray="5 5"/>'
        )
        lines.append(
            f'<text x="{xx + 10:.1f}" y="{y + 18:.1f}" '
            f'font-family="Helvetica, Arial, sans-serif" font-size="12" fill="#374151">'
            "fresh pod first request</text>"
        )

    for label, values in datasets.items():
        color = COLORS.get(label, "#1f77b4")
        panel_rows = [row for row in values if row["invocation"] >= start_invocation]
        points = [(x_scale(row["invocation"]), y_scale(row["latency_ms"])) for row in panel_rows]
        joined = " ".join(f"{xx:.1f},{yy:.1f}" for xx, yy in points)
        lines.append(f'<polyline points="{joined}" fill="none" stroke="{color}" stroke-width="2.6"/>')
        for xx, yy in points:
            lines.append(f'<circle cx="{xx:.1f}" cy="{yy:.1f}" r="4.0" fill="{color}"/>')

    if start_invocation >= 2:
        for index, (label, values) in enumerate(datasets.items()):
            color = COLORS.get(label, "#1f77b4")
            median = warm_median(values)
            yy = y_scale(median)
            lines.append(
                f'<line x1="{x}" y1="{yy:.1f}" x2="{x + width}" y2="{yy:.1f}" '
                f'stroke="{color}" stroke-width="1.5" stroke-dasharray="6 6" opacity="0.75"/>'
            )
            label_y = yy - 8 - index * 2
            lines.append(
                f'<text x="{x + width - 8}" y="{label_y:.1f}" text-anchor="end" '
                f'font-family="Helvetica, Arial, sans-serif" font-size="12" fill="{color}">'
                f'{svg_escape(label)} warm median {median:.1f} ms</text>'
            )


def render_svg(datasets: dict[str, list[dict[str, Any]]], title: str, out: Path) -> None:
    width = 1060
    height = 920
    left = 92
    chart_w = 900
    top_y = 134
    panel_h = 285
    bottom_y = 548
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text x="{width / 2:.1f}" y="38" text-anchor="middle" font-family="Helvetica, Arial, sans-serif" font-size="24" font-weight="700" fill="#111827">{svg_escape(title)}</text>',
    ]
    for index, line in enumerate(improvement_lines(datasets)):
        lines.append(
            f'<text x="{left}" y="{68 + index * 20}" font-family="Helvetica, Arial, sans-serif" '
            f'font-size="13" fill="#111827">{svg_escape(line)}</text>'
        )
    draw_panel(
        lines,
        datasets,
        x=left,
        y=top_y,
        width=chart_w,
        height=panel_h,
        title="Full cold-to-warm sequence",
        start_invocation=1,
        tight=False,
    )
    draw_panel(
        lines,
        datasets,
        x=left,
        y=bottom_y,
        width=chart_w,
        height=panel_h,
        title="Warm-start zoom (invocations 2-20)",
        start_invocation=2,
        tight=True,
    )

    legend_x = width - 260
    legend_y = 70
    for index, label in enumerate(datasets):
        y = legend_y + index * 26
        color = COLORS.get(label, "#1f77b4")
        lines.append(f'<line x1="{legend_x}" y1="{y}" x2="{legend_x + 32}" y2="{y}" stroke="{color}" stroke-width="3"/>')
        lines.append(f'<circle cx="{legend_x + 16}" cy="{y}" r="4" fill="{color}"/>')
        lines.append(
            f'<text x="{legend_x + 42}" y="{y + 4}" font-family="Helvetica, Arial, sans-serif" '
            f'font-size="13" fill="#111827">{svg_escape(label)}</text>'
        )
    lines.append(
        f'<text x="{left + chart_w / 2:.1f}" y="{height - 24}" text-anchor="middle" '
        f'font-family="Helvetica, Arial, sans-serif" font-size="17" fill="#111827">Invocation #</text>'
    )
    lines.append(
        f'<text x="28" y="{height / 2:.1f}" text-anchor="middle" font-family="Helvetica, Arial, sans-serif" '
        f'font-size="17" fill="#111827" transform="rotate(-90 28 {height / 2:.1f})">Latency (ms)</text>'
    )
    lines.append("</svg>")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", action="append", required=True, help="label=csv")
    parser.add_argument("--title", required=True)
    parser.add_argument("--svg", required=True, type=Path)
    args = parser.parse_args()
    render_svg(load_inputs(args.input), args.title, args.svg)
    print(f"wrote {args.svg}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
