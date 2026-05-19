#!/usr/bin/env python3
"""Render split line graphs for profile-guided optimization effects."""

from __future__ import annotations

import argparse
import csv
import math
import re
import statistics
from pathlib import Path
from typing import Any


COLORS = {
    "go-nopgo": "#334155",
    "go-pgo-3": "#0f766e",
    "go-pgo-5": "#2563eb",
    "go-pgo-10": "#b45309",
    "go-openfaas-nopgo": "#334155",
    "go-openfaas-pgo-3": "#0f766e",
    "go-openfaas-pgo-5": "#2563eb",
    "go-openfaas-pgo-10": "#b45309",
}


def display(label: str) -> str:
    if label in {"go-nopgo", "go-openfaas-nopgo"}:
        return "No saved profile state"
    match = re.fullmatch(r"go(?:-openfaas)?-pgo-(\d+)", label)
    if match:
        return f"Saved profile state ({match.group(1)} profiles)"
    return label


def color_for(label: str) -> str:
    return COLORS.get(label, "#475569")


def read_rows(path: Path, label: str, metric: str | None) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise ValueError(f"empty CSV: {path}")

    index_field = "iteration" if "iteration" in rows[0] else "invocation"
    metric_field = metric
    if metric_field is None:
        if "wall_ms" in rows[0]:
            metric_field = "wall_ms"
        elif "latency_ms" in rows[0]:
            metric_field = "latency_ms"
        else:
            raise ValueError(f"cannot infer latency metric for {path}")

    parsed = []
    for row in rows:
        parsed.append(
            {
                "label": label,
                "invocation": int(float(row[index_field])),
                "latency_ms": float(row[metric_field]),
            }
        )
    return sorted(parsed, key=lambda row: row["invocation"])


def load_inputs(inputs: list[str], metric: str | None) -> dict[str, list[dict[str, Any]]]:
    datasets = {}
    for item in inputs:
        if "=" not in item:
            raise ValueError("--input must use label=path")
        label, raw_path = item.split("=", 1)
        datasets[label] = read_rows(Path(raw_path), label, metric)
    return datasets


def escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def nice_max(value: float) -> float:
    if value <= 0:
        return 1.0
    magnitude = 10 ** math.floor(math.log10(value))
    normalized = value / magnitude
    if normalized <= 1.5:
        nice = 1.5
    elif normalized <= 2:
        nice = 2
    elif normalized <= 3:
        nice = 3
    elif normalized <= 5:
        nice = 5
    else:
        nice = 10
    return nice * magnitude


def baseline_label(datasets: dict[str, list[dict[str, Any]]]) -> str:
    for candidate in ("go-nopgo", "go-openfaas-nopgo"):
        if candidate in datasets:
            return candidate
    return next(iter(datasets))


def median_after_first(rows: list[dict[str, Any]]) -> float:
    values = [row["latency_ms"] for row in rows if row["invocation"] >= 2]
    if not values:
        values = [row["latency_ms"] for row in rows]
    return statistics.median(values)


def best_optimized_label(datasets: dict[str, list[dict[str, Any]]], baseline: str) -> str:
    optimized = [label for label in datasets if label != baseline]
    if not optimized:
        return baseline
    return min(optimized, key=lambda label: median_after_first(datasets[label]))


def percent_lower(before: float, after: float) -> float:
    if before <= 0:
        return 0.0
    return (before - after) / before * 100.0


def paired_wins(
    baseline_rows: list[dict[str, Any]],
    optimized_rows: list[dict[str, Any]],
    start_invocation: int,
) -> tuple[int, int]:
    baseline = {row["invocation"]: row["latency_ms"] for row in baseline_rows if row["invocation"] >= start_invocation}
    optimized = {row["invocation"]: row["latency_ms"] for row in optimized_rows if row["invocation"] >= start_invocation}
    common = sorted(set(baseline) & set(optimized))
    wins = sum(1 for invocation in common if optimized[invocation] < baseline[invocation])
    return wins, len(common)


def annotation_lines(datasets: dict[str, list[dict[str, Any]]]) -> list[str]:
    baseline = baseline_label(datasets)
    optimized = best_optimized_label(datasets, baseline)
    if optimized == baseline:
        return []
    base_median = median_after_first(datasets[baseline])
    opt_median = median_after_first(datasets[optimized])
    wins, total = paired_wins(datasets[baseline], datasets[optimized], start_invocation=2)
    return [
        (
            "Profile-optimized warm median: "
            f"{base_median:.1f} -> {opt_median:.1f} ms "
            f"({percent_lower(base_median, opt_median):.1f}% lower)."
        ),
        (
            f"{display(optimized)} is below the no-save baseline on {wins}/{total} paired warm invocations; "
            "invocation 1 is kept separate so startup noise does not hide the optimized steady path."
        ),
    ]


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
    min_invocation = min(row["invocation"] for row in rows)
    max_invocation = max(row["invocation"] for row in rows)
    min_latency = min(row["latency_ms"] for row in rows)
    max_latency = max(row["latency_ms"] for row in rows)
    if tight:
        padding = max((max_latency - min_latency) * 0.22, 0.5)
        y_min = max(0.0, min_latency - padding)
        y_max = max_latency + padding
    else:
        y_min = 0.0
        y_max = nice_max(max_latency * 1.08)
    if y_max <= y_min:
        y_max = y_min + 1.0

    def x_scale(invocation: int) -> float:
        return x + ((invocation - min_invocation) / max(max_invocation - min_invocation, 1)) * width

    def y_scale(latency: float) -> float:
        return y + height - ((latency - y_min) / (y_max - y_min)) * height

    lines.append(
        f'<text x="{x}" y="{y - 16}" font-family="Helvetica, Arial, sans-serif" '
        f'font-size="16" font-weight="700" fill="#111827">{escape(title)}</text>'
    )
    for index in range(6):
        value = y_min + (y_max - y_min) * index / 5
        yy = y_scale(value)
        label = f"{value:.1f}" if tight else f"{value:.0f}"
        lines.append(f'<line x1="{x}" y1="{yy:.1f}" x2="{x + width}" y2="{yy:.1f}" stroke="#d1d5db"/>')
        lines.append(
            f'<text x="{x - 10}" y="{yy + 4:.1f}" text-anchor="end" '
            f'font-family="Helvetica, Arial, sans-serif" font-size="12" fill="#111827">{label}</text>'
        )

    for invocation in range(min_invocation, max_invocation + 1):
        xx = x_scale(invocation)
        if invocation == min_invocation or invocation == max_invocation or invocation % 2 == 0:
            lines.append(f'<line x1="{xx:.1f}" y1="{y}" x2="{xx:.1f}" y2="{y + height}" stroke="#e5e7eb"/>')
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
            "startup / first invocation</text>"
        )

    for label, values in datasets.items():
        color = color_for(label)
        panel_rows = [row for row in values if row["invocation"] >= start_invocation]
        points = [(x_scale(row["invocation"]), y_scale(row["latency_ms"])) for row in panel_rows]
        joined = " ".join(f"{xx:.1f},{yy:.1f}" for xx, yy in points)
        lines.append(f'<polyline points="{joined}" fill="none" stroke="{color}" stroke-width="2.6"/>')
        for xx, yy in points:
            lines.append(f'<circle cx="{xx:.1f}" cy="{yy:.1f}" r="4.0" fill="{color}"/>')

    if start_invocation >= 2:
        for index, (label, values) in enumerate(datasets.items()):
            median = median_after_first(values)
            color = color_for(label)
            yy = y_scale(median)
            lines.append(
                f'<line x1="{x}" y1="{yy:.1f}" x2="{x + width}" y2="{yy:.1f}" '
                f'stroke="{color}" stroke-width="1.5" stroke-dasharray="6 6" opacity="0.75"/>'
            )
            lines.append(
                f'<text x="{x + width - 8}" y="{yy - 8 - index * 2:.1f}" text-anchor="end" '
                f'font-family="Helvetica, Arial, sans-serif" font-size="12" fill="{color}">'
                f'{escape(display(label))} median {median:.1f} ms</text>'
            )


def render_svg(datasets: dict[str, list[dict[str, Any]]], title: str, out: Path) -> None:
    width = 1060
    height = 920
    left = 92
    chart_w = 900
    top_y = 134
    bottom_y = 548
    panel_h = 285
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text x="{width / 2:.1f}" y="38" text-anchor="middle" font-family="Helvetica, Arial, sans-serif" '
        f'font-size="24" font-weight="700" fill="#111827">{escape(title)}</text>',
    ]
    for index, value in enumerate(annotation_lines(datasets)):
        lines.append(
            f'<text x="{left}" y="{68 + index * 20}" font-family="Helvetica, Arial, sans-serif" '
            f'font-size="13" fill="#111827">{escape(value)}</text>'
        )

    draw_panel(
        lines,
        datasets,
        x=left,
        y=top_y,
        width=chart_w,
        height=panel_h,
        title="Full invocation sequence",
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
        title="Optimized steady path zoom (invocations 2+)",
        start_invocation=2,
        tight=True,
    )

    legend_x = width - 302
    legend_y = 68
    for index, label in enumerate(datasets):
        y = legend_y + index * 24
        color = color_for(label)
        lines.append(f'<line x1="{legend_x}" y1="{y}" x2="{legend_x + 32}" y2="{y}" stroke="{color}" stroke-width="3"/>')
        lines.append(f'<circle cx="{legend_x + 16}" cy="{y}" r="4" fill="{color}"/>')
        lines.append(
            f'<text x="{legend_x + 42}" y="{y + 4}" font-family="Helvetica, Arial, sans-serif" '
            f'font-size="13" fill="#111827">{escape(display(label))}</text>'
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
    parser.add_argument("--metric", help="metric column; defaults to wall_ms or latency_ms")
    parser.add_argument("--title", required=True)
    parser.add_argument("--svg", required=True, type=Path)
    args = parser.parse_args()
    render_svg(load_inputs(args.input, args.metric), args.title, args.svg)
    print(f"wrote {args.svg}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
