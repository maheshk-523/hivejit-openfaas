#!/usr/bin/env python3
"""Render paper-style warmup phase plots with a log latency axis."""

from __future__ import annotations

import argparse
import csv
import math
import re
import statistics
from pathlib import Path
from typing import Any


COLORS = {
    "baseline": "#1d4ed8",
    "redis-cache": "#16a34a",
    "go-nopgo": "#334155",
    "go-pgo-3": "#2563eb",
    "go-pgo-5": "#16a34a",
    "go-pgo-10": "#b45309",
    "go-openfaas-nopgo": "#334155",
    "go-openfaas-pgo-10": "#16a34a",
    "python-generic": "#334155",
    "python-specialized-3": "#0f766e",
}


DISPLAY = {
    "baseline": "No saved warm state",
    "redis-cache": "Saved XLA warm state",
    "go-nopgo": "No saved profile state",
    "go-openfaas-nopgo": "No saved profile state",
    "python-generic": "No saved specialization state",
    "python-specialized-3": "Saved specialization artifact",
}


def display(label: str) -> str:
    if label in DISPLAY:
        return DISPLAY[label]
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
        latency = float(row[metric_field])
        if latency <= 0:
            continue
        parsed.append(
            {
                "label": label,
                "invocation": int(float(row[index_field])),
                "latency_ms": latency,
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


def baseline_label(datasets: dict[str, list[dict[str, Any]]]) -> str:
    for candidate in ("baseline", "go-nopgo", "go-openfaas-nopgo", "python-generic"):
        if candidate in datasets:
            return candidate
    return next(iter(datasets))


def optimized_label(datasets: dict[str, list[dict[str, Any]]], baseline: str, hot_start: int) -> str:
    candidates = [label for label in datasets if label != baseline]
    if not candidates:
        return baseline
    return min(candidates, key=lambda label: median_from(datasets[label], hot_start))


def median_from(rows: list[dict[str, Any]], start_invocation: int) -> float:
    values = [row["latency_ms"] for row in rows if row["invocation"] >= start_invocation]
    if not values:
        values = [row["latency_ms"] for row in rows]
    return statistics.median(values)


def first_latency(rows: list[dict[str, Any]]) -> float:
    return min(rows, key=lambda row: row["invocation"])["latency_ms"]


def paired_wins(
    baseline_rows: list[dict[str, Any]],
    optimized_rows: list[dict[str, Any]],
    start_invocation: int,
) -> tuple[int, int]:
    baseline = {row["invocation"]: row["latency_ms"] for row in baseline_rows if row["invocation"] >= start_invocation}
    optimized = {row["invocation"]: row["latency_ms"] for row in optimized_rows if row["invocation"] >= start_invocation}
    common = sorted(set(baseline) & set(optimized))
    return sum(1 for invocation in common if optimized[invocation] < baseline[invocation]), len(common)


def log_bounds(datasets: dict[str, list[dict[str, Any]]]) -> tuple[float, float]:
    values = [row["latency_ms"] for rows in datasets.values() for row in rows]
    lower = 10 ** math.floor(math.log10(min(values) * 0.8))
    upper = 10 ** math.ceil(math.log10(max(values) * 1.25))
    return max(lower, 0.001), upper


def log_ticks(y_min: float, y_max: float) -> list[float]:
    start = math.floor(math.log10(y_min))
    end = math.ceil(math.log10(y_max))
    ticks = []
    for exponent in range(start, end + 1):
        for multiplier in (1, 2, 5):
            value = multiplier * (10**exponent)
            if y_min <= value <= y_max:
                ticks.append(value)
    return ticks


def tick_label(value: float) -> str:
    exponent = math.log10(value)
    if abs(exponent - round(exponent)) < 1e-9:
        return f"10^{int(round(exponent))}"
    if value >= 1:
        return f"{value:g}"
    return f"{value:.2g}"


def render_svg(
    datasets: dict[str, list[dict[str, Any]]],
    *,
    title: str,
    out: Path,
    cold_end: float,
    hot_start: int,
    subtitle: str,
) -> None:
    width = 1120
    height = 680
    left = 118
    right = 50
    top = 96
    bottom = 92
    chart_w = width - left - right
    chart_h = height - top - bottom
    all_rows = [row for rows in datasets.values() for row in rows]
    min_invocation = min(row["invocation"] for row in all_rows)
    max_invocation = max(row["invocation"] for row in all_rows)
    y_min, y_max = log_bounds(datasets)

    def x_scale(invocation: float) -> float:
        return left + ((invocation - min_invocation) / max(max_invocation - min_invocation, 1)) * chart_w

    def y_scale(latency: float) -> float:
        return top + chart_h - ((math.log10(latency) - math.log10(y_min)) / (math.log10(y_max) - math.log10(y_min))) * chart_h

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text x="{width / 2:.1f}" y="36" text-anchor="middle" font-family="Georgia, Times New Roman, serif" '
        f'font-size="28" font-weight="700" fill="#111827">{escape(title)}</text>',
    ]
    if subtitle:
        lines.append(
            f'<text x="{width / 2:.1f}" y="62" text-anchor="middle" font-family="Helvetica, Arial, sans-serif" '
            f'font-size="13" fill="#374151">{escape(subtitle)}</text>'
        )

    phase_y = top
    phase_h = 44
    cold_x1 = x_scale(min_invocation)
    cold_x2 = x_scale(cold_end)
    hot_x1 = x_scale(hot_start)
    hot_x2 = x_scale(max_invocation)
    lines.append(f'<rect x="{cold_x1:.1f}" y="{phase_y}" width="{max(8, cold_x2 - cold_x1):.1f}" height="{phase_h}" fill="#5b84d7" opacity="0.85"/>')
    lines.append(f'<rect x="{cold_x2:.1f}" y="{phase_y}" width="{max(0, hot_x1 - cold_x2):.1f}" height="{phase_h}" fill="#f4d7ad" opacity="0.9"/>')
    lines.append(f'<rect x="{hot_x1:.1f}" y="{phase_y}" width="{max(0, hot_x2 - hot_x1):.1f}" height="{phase_h}" fill="#df7774" opacity="0.9"/>')
    lines.append(f'<text x="{(cold_x2 + hot_x1) / 2:.1f}" y="{phase_y + 30}" text-anchor="middle" font-family="Helvetica, Arial, sans-serif" font-size="26" font-weight="700" fill="#111827">Warm</text>')
    lines.append(f'<text x="{(hot_x1 + hot_x2) / 2:.1f}" y="{phase_y + 30}" text-anchor="middle" font-family="Helvetica, Arial, sans-serif" font-size="26" font-weight="700" fill="#111827">Hot</text>')

    for tick in log_ticks(y_min, y_max):
        y = y_scale(tick)
        is_power = tick_label(tick).startswith("10^")
        stroke = "#cbd5e1" if is_power else "#e5e7eb"
        lines.append(f'<line x1="{left}" y1="{y:.1f}" x2="{left + chart_w}" y2="{y:.1f}" stroke="{stroke}"/>')
        if is_power:
            label = tick_label(tick)
            if "^" in label:
                base, exponent = label.split("^", 1)
                lines.append(
                    f'<text x="{left - 14}" y="{y + 5:.1f}" text-anchor="end" font-family="Helvetica, Arial, sans-serif" '
                    f'font-size="18" fill="#111827">{base}<tspan baseline-shift="super" font-size="12">{exponent}</tspan></text>'
                )
            else:
                lines.append(
                    f'<text x="{left - 14}" y="{y + 5:.1f}" text-anchor="end" font-family="Helvetica, Arial, sans-serif" '
                    f'font-size="16" fill="#111827">{label}</text>'
                )

    for boundary, color in ((cold_end, "#f4c08a"), (hot_start, "#df7774")):
        x = x_scale(boundary)
        lines.append(
            f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{top + chart_h}" '
            f'stroke="{color}" stroke-dasharray="7 7" stroke-width="2.0" opacity="0.95"/>'
        )

    x_ticks = [min_invocation]
    for step in (5, 10, 20):
        x_ticks.extend(range(step, max_invocation + 1, step))
        if max_invocation <= 25:
            break
    x_ticks.append(max_invocation)
    for tick in sorted(set(value for value in x_ticks if min_invocation <= value <= max_invocation)):
        x = x_scale(tick)
        lines.append(f'<line x1="{x:.1f}" y1="{top + chart_h}" x2="{x:.1f}" y2="{top + chart_h + 7}" stroke="#111827"/>')
        lines.append(
            f'<text x="{x:.1f}" y="{top + chart_h + 31}" text-anchor="middle" font-family="Helvetica, Arial, sans-serif" '
            f'font-size="16" fill="#111827">{tick}</text>'
        )

    lines.append(f'<line x1="{left}" y1="{top + chart_h}" x2="{left + chart_w}" y2="{top + chart_h}" stroke="#111827"/>')
    lines.append(f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + chart_h}" stroke="#111827"/>')

    for label, rows in datasets.items():
        color = color_for(label)
        points = [(x_scale(row["invocation"]), y_scale(row["latency_ms"])) for row in rows]
        joined = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
        lines.append(f'<polyline points="{joined}" fill="none" stroke="{color}" stroke-width="2.8"/>')
        for x, y in points:
            lines.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.0" fill="{color}"/>')

    baseline = baseline_label(datasets)
    optimized = optimized_label(datasets, baseline, hot_start)
    if optimized != baseline:
        base_hot = median_from(datasets[baseline], hot_start)
        opt_hot = median_from(datasets[optimized], hot_start)
        wins, total = paired_wins(datasets[baseline], datasets[optimized], hot_start)
        ratio = base_hot / opt_hot if opt_hot > 0 else 0.0
        arrow_x = x_scale(max_invocation) - 80
        y1 = y_scale(base_hot)
        y2 = y_scale(opt_hot)
        lines.append(f'<line x1="{left}" y1="{y1:.1f}" x2="{arrow_x:.1f}" y2="{y1:.1f}" stroke="#7e22ce" stroke-dasharray="7 5" stroke-width="2"/>')
        lines.append(f'<line x1="{left}" y1="{y2:.1f}" x2="{arrow_x:.1f}" y2="{y2:.1f}" stroke="#7e22ce" stroke-dasharray="7 5" stroke-width="2"/>')
        lines.append(f'<line x1="{arrow_x:.1f}" y1="{y1:.1f}" x2="{arrow_x:.1f}" y2="{y2:.1f}" stroke="#111827" stroke-width="2.4"/>')
        lines.append(f'<path d="M {arrow_x - 7:.1f} {y1 + 9:.1f} L {arrow_x:.1f} {y1:.1f} L {arrow_x + 7:.1f} {y1 + 9:.1f}" fill="none" stroke="#111827" stroke-width="2.4"/>')
        lines.append(f'<path d="M {arrow_x - 7:.1f} {y2 - 9:.1f} L {arrow_x:.1f} {y2:.1f} L {arrow_x + 7:.1f} {y2 - 9:.1f}" fill="none" stroke="#111827" stroke-width="2.4"/>')
        lines.append(
            f'<text x="{arrow_x + 16:.1f}" y="{(y1 + y2) / 2 + 5:.1f}" font-family="Helvetica, Arial, sans-serif" '
            f'font-size="18" font-weight="700" fill="#111827">{ratio:.2f}x</text>'
        )
        lines.append(
            f'<text x="{left}" y="{height - 26}" font-family="Helvetica, Arial, sans-serif" font-size="13" fill="#111827">'
            f'Hot median: {display(baseline)} {base_hot:.1f} ms, {display(optimized)} {opt_hot:.1f} ms; '
            f'saved-state line below no-save baseline on {wins}/{total} hot requests.</text>'
        )

    if optimized in datasets:
        cold = first_latency(datasets[optimized])
        hot = median_from(datasets[optimized], hot_start)
        ratio = cold / hot if hot > 0 else 0.0
        x0 = x_scale(min_invocation)
        y_cold = y_scale(cold)
        y_hot = y_scale(hot)
        label_x = max(left + 18, x0 - 120)
        lines.append(
            f'<rect x="{label_x:.1f}" y="{y_cold + 22:.1f}" width="80" height="30" rx="3" fill="#5b84d7" opacity="0.9"/>'
        )
        lines.append(
            f'<text x="{label_x + 40:.1f}" y="{y_cold + 44:.1f}" text-anchor="middle" font-family="Helvetica, Arial, sans-serif" '
            f'font-size="22" font-weight="700" fill="#111827">Cold</text>'
        )
        lines.append(
            f'<line x1="{label_x + 80:.1f}" y1="{y_cold + 22:.1f}" x2="{x0 - 6:.1f}" y2="{y_cold + 4:.1f}" '
            'stroke="#111827" stroke-width="2.2"/>'
        )
        if ratio >= 1.5:
            ratio_x = x_scale(max(hot_start, min_invocation + 1))
            lines.append(f'<line x1="{ratio_x:.1f}" y1="{y_cold:.1f}" x2="{ratio_x:.1f}" y2="{y_hot:.1f}" stroke="#111827" stroke-width="2.2"/>')
            lines.append(f'<path d="M {ratio_x - 7:.1f} {y_cold + 9:.1f} L {ratio_x:.1f} {y_cold:.1f} L {ratio_x + 7:.1f} {y_cold + 9:.1f}" fill="none" stroke="#111827" stroke-width="2.2"/>')
            lines.append(f'<path d="M {ratio_x - 7:.1f} {y_hot - 9:.1f} L {ratio_x:.1f} {y_hot:.1f} L {ratio_x + 7:.1f} {y_hot - 9:.1f}" fill="none" stroke="#111827" stroke-width="2.2"/>')
            lines.append(
                f'<text x="{ratio_x + 14:.1f}" y="{(y_cold + y_hot) / 2 + 5:.1f}" font-family="Helvetica, Arial, sans-serif" '
                f'font-size="18" font-weight="700" fill="#111827">{ratio:.1f}x</text>'
            )

    legend_x = left + chart_w - 250
    legend_y = top + chart_h - 70
    for index, label in enumerate(datasets):
        y = legend_y + index * 26
        color = color_for(label)
        lines.append(f'<line x1="{legend_x}" y1="{y}" x2="{legend_x + 32}" y2="{y}" stroke="{color}" stroke-width="3"/>')
        lines.append(f'<circle cx="{legend_x + 16}" cy="{y}" r="4" fill="{color}"/>')
        lines.append(
            f'<text x="{legend_x + 42}" y="{y + 4}" font-family="Helvetica, Arial, sans-serif" font-size="13" fill="#111827">{escape(display(label))}</text>'
        )

    lines.append(
        f'<text x="{left + chart_w / 2:.1f}" y="{height - 48}" text-anchor="middle" font-family="Helvetica, Arial, sans-serif" '
        f'font-size="24" fill="#111827">Request Number</text>'
    )
    lines.append(
        f'<text x="42" y="{top + chart_h / 2:.1f}" text-anchor="middle" font-family="Helvetica, Arial, sans-serif" '
        f'font-size="24" fill="#111827" transform="rotate(-90 42 {top + chart_h / 2:.1f})">Latency (ms)</text>'
    )
    lines.append("</svg>")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", action="append", required=True, help="label=csv")
    parser.add_argument("--metric", help="metric column; defaults to wall_ms or latency_ms")
    parser.add_argument("--title", required=True)
    parser.add_argument("--subtitle", default="")
    parser.add_argument("--cold-end", type=float, default=1.4)
    parser.add_argument("--hot-start", type=int, default=7)
    parser.add_argument("--svg", required=True, type=Path)
    args = parser.parse_args()
    render_svg(
        load_inputs(args.input, args.metric),
        title=args.title,
        out=args.svg,
        cold_end=args.cold_end,
        hot_start=args.hot_start,
        subtitle=args.subtitle,
    )
    print(f"wrote {args.svg}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
