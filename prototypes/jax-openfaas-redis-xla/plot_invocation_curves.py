#!/usr/bin/env python3
"""Render latency-vs-invocation line graphs for JAX/OpenFaaS Redis runs."""

from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path
from typing import Any


LABELS = ("baseline", "redis-cache", "progressive-cache")
COLORS = {"baseline": "#b9413c", "redis-cache": "#2f7d59", "progressive-cache": "#2563eb"}
METRIC_TITLES = {
    "http_latency_ms": "HTTP first-request latency",
    "compile_or_load_ms": "JAX compile/load latency",
    "handler_ms": "Handler-side latency",
    "restart_ms": "Pod restart-to-ready latency",
    "cold_start_total_ms": "End-to-end cold-start latency",
}


def read_rows(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        row["invocation"] = int(row.get("invocation") or row.get("trial") or 0)
        for key in ("http_latency_ms", "compile_or_load_ms", "handler_ms", "restart_ms"):
            row[key] = float(row.get(key) or 0.0)
        row["cold_start_total_ms"] = row["restart_ms"] + row["http_latency_ms"]
    return sorted(rows, key=lambda row: int(row["invocation"]))


def load_run(run_dir: Path) -> dict[str, dict[str, list[dict[str, Any]]]]:
    datasets: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for signature_dir in sorted(path for path in run_dir.iterdir() if path.is_dir()):
        labels = {}
        for label in LABELS:
            csv_path = signature_dir / f"{label}.csv"
            if csv_path.exists():
                labels[label] = read_rows(csv_path)
        if labels:
            datasets[signature_dir.name] = labels
    if not datasets:
        raise ValueError(f"no signature CSVs found under {run_dir}")
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


def polyline(points: list[tuple[float, float]], color: str) -> str:
    joined = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    return f'<polyline points="{joined}" fill="none" stroke="{color}" stroke-width="2.5"/>'


def circle(x: float, y: float, radius: float, color: str) -> str:
    return f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{radius:.1f}" fill="{color}"/>'


def render_frame(left: int, top: int, width: int, height: int, y_max: float, max_trial: int) -> list[str]:
    lines = []
    for index in range(6):
        value = y_max * index / 5
        y = top + height - (value / y_max) * height
        lines.append(f'<line x1="{left}" y1="{y:.1f}" x2="{left + width}" y2="{y:.1f}" stroke="#e5e7eb"/>')
        lines.append(
            f'<text x="{left - 10}" y="{y + 4:.1f}" text-anchor="end" '
            f'font-family="Helvetica, Arial, sans-serif" font-size="11" fill="#4b5563">{value:.0f}</text>'
        )
    for trial in range(1, max_trial + 1):
        x = left + ((trial - 1) / max(max_trial - 1, 1)) * width
        lines.append(f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{top + height}" stroke="#f1f5f9"/>')
        lines.append(
            f'<text x="{x:.1f}" y="{top + height + 22}" text-anchor="middle" '
            f'font-family="Helvetica, Arial, sans-serif" font-size="11" fill="#4b5563">{trial}</text>'
        )
    lines.append(f'<line x1="{left}" y1="{top + height}" x2="{left + width}" y2="{top + height}" stroke="#94a3b8"/>')
    lines.append(f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + height}" stroke="#94a3b8"/>')
    return lines


def render_svg(datasets: dict[str, dict[str, list[dict[str, Any]]]], metric: str, out: Path) -> None:
    signatures = list(datasets)
    max_trial = max(row["invocation"] for labels in datasets.values() for rows in labels.values() for row in rows)
    max_value = max(float(row[metric]) for labels in datasets.values() for rows in labels.values() for row in rows)
    y_max = nice_max(max_value * 1.08)

    width = 1120
    panel_h = 280
    top = 82
    bottom = 70
    gap = 46
    height = top + bottom + len(signatures) * panel_h + max(0, len(signatures) - 1) * gap
    left = 92
    right = 220
    chart_w = width - left - right
    chart_h = panel_h - 58
    title = METRIC_TITLES.get(metric, metric)

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text x="24" y="36" font-family="Helvetica, Arial, sans-serif" font-size="22" font-weight="700" fill="#202124">{svg_escape(title)} vs invocation number</text>',
        '<text x="24" y="60" font-family="Helvetica, Arial, sans-serif" font-size="13" fill="#5f6368">Each point is a fresh OpenFaaS pod. Lower is better.</text>',
    ]

    for panel_index, signature in enumerate(signatures):
        panel_top = top + panel_index * (panel_h + gap)
        frame_top = panel_top + 34
        lines.append(
            f'<text x="24" y="{panel_top + 18}" font-family="Helvetica, Arial, sans-serif" '
            f'font-size="16" font-weight="700" fill="#202124">{svg_escape(signature)}</text>'
        )
        lines.extend(render_frame(left, frame_top, chart_w, chart_h, y_max, max_trial))
        for label in LABELS:
            rows = datasets[signature].get(label, [])
            if not rows:
                continue
            points = []
            for row in rows:
                x = left + ((int(row["invocation"]) - 1) / max(max_trial - 1, 1)) * chart_w
                y = frame_top + chart_h - (float(row[metric]) / y_max) * chart_h
                points.append((x, y))
            color = COLORS.get(label, "#64748b")
            lines.append(polyline(points, color))
            for x, y in points:
                lines.append(circle(x, y, 3.2, color))
        if panel_index == len(signatures) - 1:
            lines.append(
                f'<text x="{left + chart_w / 2:.1f}" y="{frame_top + chart_h + 46}" '
                f'text-anchor="middle" font-family="Helvetica, Arial, sans-serif" '
                f'font-size="13" font-weight="600" fill="#334155">Invocation number</text>'
            )

    legend_x = width - right + 28
    legend_y = top + 42
    for index, label in enumerate(LABELS):
        y = legend_y + index * 28
        color = COLORS[label]
        lines.append(f'<line x1="{legend_x}" y1="{y}" x2="{legend_x + 32}" y2="{y}" stroke="{color}" stroke-width="3"/>')
        lines.append(circle(legend_x + 16, y, 4, color))
        lines.append(
            f'<text x="{legend_x + 44}" y="{y + 4}" font-family="Helvetica, Arial, sans-serif" '
            f'font-size="12" font-weight="600" fill="#334155">{svg_escape(label)}</text>'
        )

    lines.append("</svg>")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument(
        "--metric",
        default="http_latency_ms",
        choices=("http_latency_ms", "compile_or_load_ms", "handler_ms", "restart_ms", "cold_start_total_ms"),
    )
    parser.add_argument("--svg", required=True, type=Path)
    args = parser.parse_args()

    render_svg(load_run(args.run_dir), args.metric, args.svg)
    print(f"wrote {args.svg}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
