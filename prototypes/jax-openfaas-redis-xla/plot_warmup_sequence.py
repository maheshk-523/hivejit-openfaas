#!/usr/bin/env python3
"""Plot warmup-style latency curves from sequential single-pod invocations."""

from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path
from typing import Any


COLORS = {"baseline": "#1f77b4", "redis-cache": "#2ca02c"}


def read_rows(path: Path, start_invocation: int) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        row["invocation"] = int(row["invocation"])
        row["latency_ms"] = float(row["latency_ms"])
        row["compile_or_load_ms"] = float(row.get("compile_or_load_ms") or 0.0)
    return sorted(
        [row for row in rows if int(row["invocation"]) >= start_invocation],
        key=lambda row: int(row["invocation"]),
    )


def load_inputs(inputs: list[str], start_invocation: int) -> dict[str, list[dict[str, Any]]]:
    datasets = {}
    for item in inputs:
        if "=" not in item:
            raise ValueError("--input must use label=path")
        label, raw_path = item.split("=", 1)
        datasets[label] = read_rows(Path(raw_path), start_invocation)
    return datasets


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


def svg_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def render_svg(datasets: dict[str, list[dict[str, Any]]], title: str, ylabel: str, out: Path, tight_y: bool) -> None:
    width = 980
    height = 620
    left = 82
    right = 34
    top = 70
    bottom = 76
    chart_w = width - left - right
    chart_h = height - top - bottom
    max_invocation = max(row["invocation"] for rows in datasets.values() for row in rows)
    max_latency = max(row["latency_ms"] for rows in datasets.values() for row in rows)
    min_latency = min(row["latency_ms"] for rows in datasets.values() for row in rows)
    if tight_y:
        padding = max((max_latency - min_latency) * 0.18, 0.5)
        y_min = max(0.0, min_latency - padding)
        y_max = max_latency + padding
    else:
        y_min = max(0.0, math.floor((min_latency * 0.92) / 10.0) * 10.0)
        y_max = nice_max(max_latency * 1.06)
    if y_max <= y_min:
        y_max = y_min + 1.0

    def x_scale(invocation: int) -> float:
        return left + ((invocation - 1) / max(max_invocation - 1, 1)) * chart_w

    def y_scale(latency: float) -> float:
        return top + chart_h - ((latency - y_min) / (y_max - y_min)) * chart_h

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text x="{width / 2:.1f}" y="34" text-anchor="middle" font-family="Helvetica, Arial, sans-serif" font-size="24" font-weight="700" fill="#111827">{svg_escape(title)}</text>',
    ]

    for index in range(7):
        value = y_min + (y_max - y_min) * index / 6
        y = y_scale(value)
        lines.append(f'<line x1="{left}" y1="{y:.1f}" x2="{width - right}" y2="{y:.1f}" stroke="#c7c7c7"/>')
        label = f"{value:.1f}" if tight_y else f"{value:.0f}"
        lines.append(
            f'<text x="{left - 12}" y="{y + 4:.1f}" text-anchor="end" '
            f'font-family="Helvetica, Arial, sans-serif" font-size="13" fill="#111827">{label}</text>'
        )
    min_invocation = min(row["invocation"] for rows in datasets.values() for row in rows)
    for invocation in range(min_invocation, max_invocation + 1):
        x = x_scale(invocation)
        if invocation == 1 or invocation == max_invocation or invocation % 2 == 0:
            lines.append(f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{top + chart_h}" stroke="#d6d6d6"/>')
            lines.append(
                f'<text x="{x:.1f}" y="{top + chart_h + 26}" text-anchor="middle" '
                f'font-family="Helvetica, Arial, sans-serif" font-size="13" fill="#111827">{invocation}</text>'
            )
    lines.append(f'<line x1="{left}" y1="{top + chart_h}" x2="{width - right}" y2="{top + chart_h}" stroke="#111827"/>')
    lines.append(f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + chart_h}" stroke="#111827"/>')

    for label, rows in datasets.items():
        color = COLORS.get(label, "#1f77b4")
        points = [(x_scale(row["invocation"]), y_scale(row["latency_ms"])) for row in rows]
        joined = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
        lines.append(f'<polyline points="{joined}" fill="none" stroke="{color}" stroke-width="2.6"/>')
        for x, y in points:
            lines.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.4" fill="{color}"/>')

    legend_x = width - right - 180
    legend_y = top + 8
    for index, label in enumerate(datasets):
        y = legend_y + index * 28
        color = COLORS.get(label, "#1f77b4")
        lines.append(f'<line x1="{legend_x}" y1="{y}" x2="{legend_x + 34}" y2="{y}" stroke="{color}" stroke-width="3"/>')
        lines.append(f'<circle cx="{legend_x + 17}" cy="{y}" r="4.4" fill="{color}"/>')
        lines.append(
            f'<text x="{legend_x + 44}" y="{y + 4}" font-family="Helvetica, Arial, sans-serif" '
            f'font-size="13" fill="#111827">{svg_escape(label)}</text>'
        )

    lines.append(
        f'<text x="{left + chart_w / 2:.1f}" y="{height - 22}" text-anchor="middle" '
        f'font-family="Helvetica, Arial, sans-serif" font-size="17" fill="#111827">Invocation #</text>'
    )
    lines.append(
        f'<text x="26" y="{top + chart_h / 2:.1f}" text-anchor="middle" font-family="Helvetica, Arial, sans-serif" '
        f'font-size="17" fill="#111827" transform="rotate(-90 26 {top + chart_h / 2:.1f})">{svg_escape(ylabel)}</text>'
    )
    lines.append("</svg>")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", action="append", required=True, help="label=csv")
    parser.add_argument("--title", required=True)
    parser.add_argument("--ylabel", default="Latency (ms)")
    parser.add_argument("--start-invocation", type=int, default=1)
    parser.add_argument("--tight-y", action="store_true")
    parser.add_argument("--svg", required=True, type=Path)
    args = parser.parse_args()
    render_svg(load_inputs(args.input, args.start_invocation), args.title, args.ylabel, args.svg, args.tight_y)
    print(f"wrote {args.svg}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
