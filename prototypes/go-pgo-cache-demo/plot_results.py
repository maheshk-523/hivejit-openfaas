#!/usr/bin/env python3
"""Render Go PGO profile-cache benchmark CSVs as standalone SVG figures."""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
import re
from pathlib import Path


COLORS = {
    "go-nopgo": "#334155",
    "go-pgo-5": "#0f766e",
    "go-pgo-10": "#b45309",
}

PGO_COLORS = ["#0f766e", "#b45309", "#7c3aed", "#dc2626", "#2563eb", "#0891b2"]

DISPLAY = {
    "go-nopgo": "No PGO",
    "go-pgo-5": "PGO, 5 profiles",
    "go-pgo-10": "PGO, 10 profiles",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", required=True, type=Path, help="directory containing go-*.csv results")
    parser.add_argument("--out-dir", required=True, type=Path, help="directory for SVG outputs")
    parser.add_argument("--prefix", default="go-pgo-profile-cache", help="output filename prefix")
    args = parser.parse_args()

    series = read_series(args.results)
    if not series:
        raise SystemExit(f"no go-*.csv files found in {args.results}")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    summaries = summarize_all(series)

    outputs = {
        "invocation_curve": args.out_dir / f"{args.prefix}-invocation-curves.svg",
        "p50_p95": args.out_dir / f"{args.prefix}-p50-p95.svg",
        "improvement": args.out_dir / f"{args.prefix}-profile-budget-improvement.svg",
        "summary": args.out_dir / f"{args.prefix}-summary.json",
    }

    outputs["invocation_curve"].write_text(render_invocation_curve(series), encoding="utf-8")
    outputs["p50_p95"].write_text(render_p50_p95_bars(summaries), encoding="utf-8")
    outputs["improvement"].write_text(render_improvement_bars(summaries), encoding="utf-8")
    outputs["summary"].write_text(json.dumps(summaries, indent=2) + "\n", encoding="utf-8")

    for name, path in outputs.items():
        print(f"{name}: {path}")


def read_series(results_dir: Path) -> dict[str, list[dict[str, float]]]:
    series: dict[str, list[dict[str, float]]] = {}
    for path in sorted(results_dir.glob("go-*.csv")):
        with path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                label = row["label"]
                series.setdefault(label, []).append(
                    {
                        "iteration": float(row["iteration"]),
                        "wall_ms": float(row["wall_ms"]),
                        "work_ms": float(row["work_ms"]),
                    }
                )
    return {label: sorted(rows, key=lambda r: r["iteration"]) for label, rows in series.items()}


def summarize_all(series: dict[str, list[dict[str, float]]]) -> list[dict[str, float | int | str]]:
    summaries = []
    for label in label_order(series):
        walls = sorted(row["wall_ms"] for row in series[label])
        summaries.append(
            {
                "label": label,
                "display": display(label),
                "n": len(walls),
                "mean_wall_ms": sum(walls) / len(walls),
                "p50_wall_ms": percentile(walls, 0.50),
                "p95_wall_ms": percentile(walls, 0.95),
                "min_wall_ms": walls[0],
                "max_wall_ms": walls[-1],
            }
        )
    return summaries


def render_invocation_curve(series: dict[str, list[dict[str, float]]]) -> str:
    width, height = 1120, 640
    left, right, top, bottom = 92, 46, 76, 88
    chart_w = width - left - right
    chart_h = height - top - bottom
    max_iter = max(row["iteration"] for rows in series.values() for row in rows)
    max_wall = max(row["wall_ms"] for rows in series.values() for row in rows)
    y_max = nice_max(max_wall * 1.08)

    parts = svg_start(width, height, "Go PGO cold invocation latency")
    parts += chart_frame(left, top, chart_w, chart_h, y_max, "Wall latency (ms)")
    parts.append(text(width / 2, 32, "Go PGO Serverless Profile Cache: Cold Invocation Latency", 22, "middle", "#0f172a", 700))
    parts.append(text(width / 2, 56, "Each point is a separate process invocation of the function binary.", 13, "middle", "#475569"))

    for label in label_order(series):
        rows = series[label]
        color = color_for(label)
        points = []
        for row in rows:
            x = left + ((row["iteration"] - 1) / max(max_iter - 1, 1)) * chart_w
            y = top + chart_h - (row["wall_ms"] / y_max) * chart_h
            points.append((x, y))
        parts.append(polyline(points, color))
        for x, y in points:
            parts.append(circle(x, y, 3.0, color))

    for tick in range(1, int(max_iter) + 1):
        if tick == 1 or tick == int(max_iter) or tick % 5 == 0:
            x = left + ((tick - 1) / max(max_iter - 1, 1)) * chart_w
            parts.append(line(x, top + chart_h, x, top + chart_h + 6, "#64748b", 1))
            parts.append(text(x, top + chart_h + 24, str(tick), 11, "middle", "#475569"))
    parts.append(text(left + chart_w / 2, height - 28, "Cold invocation number", 13, "middle", "#334155", 600))
    parts += legend(series.keys(), width - right - 220, top + 6)
    parts.append("</svg>")
    return "\n".join(parts)


def render_p50_p95_bars(summaries: list[dict[str, float | int | str]]) -> str:
    width, height = 980, 600
    left, right, top, bottom = 92, 44, 84, 102
    chart_w = width - left - right
    chart_h = height - top - bottom
    y_max = nice_max(max(float(s["p95_wall_ms"]) for s in summaries) * 1.18)

    parts = svg_start(width, height, "Go PGO p50 and p95 latency")
    parts += chart_frame(left, top, chart_w, chart_h, y_max, "Wall latency (ms)")
    parts.append(text(width / 2, 34, "Go PGO Profile Cache: p50 and p95 Latency", 22, "middle", "#0f172a", 700))
    parts.append(text(width / 2, 58, "Lower is better. Bars summarize cold process invocations.", 13, "middle", "#475569"))

    group_w = chart_w / len(summaries)
    bar_w = min(58, group_w * 0.25)
    p50_color = "#2563eb"
    p95_color = "#dc2626"
    for idx, summary in enumerate(summaries):
        cx = left + group_w * (idx + 0.5)
        for offset, key, color in [(-bar_w * 0.58, "p50_wall_ms", p50_color), (bar_w * 0.58, "p95_wall_ms", p95_color)]:
            value = float(summary[key])
            x = cx + offset - bar_w / 2
            h = (value / y_max) * chart_h
            y = top + chart_h - h
            parts.append(rect(x, y, bar_w, h, color))
            parts.append(text(x + bar_w / 2, y - 8, f"{value:.1f}", 12, "middle", "#334155", 600))
        parts.append(text(cx, top + chart_h + 28, str(summary["display"]), 12, "middle", "#334155", 600))

    parts.append(rect(width - right - 190, top + 6, 12, 12, p50_color))
    parts.append(text(width - right - 172, top + 17, "p50", 12, "start", "#334155"))
    parts.append(rect(width - right - 126, top + 6, 12, 12, p95_color))
    parts.append(text(width - right - 108, top + 17, "p95", 12, "start", "#334155"))
    parts.append("</svg>")
    return "\n".join(parts)


def render_improvement_bars(summaries: list[dict[str, float | int | str]]) -> str:
    baseline = next((s for s in summaries if s["label"] == "go-nopgo"), summaries[0])
    rows = []
    for summary in summaries:
        p50 = pct_change(float(baseline["p50_wall_ms"]), float(summary["p50_wall_ms"]))
        p95 = pct_change(float(baseline["p95_wall_ms"]), float(summary["p95_wall_ms"]))
        rows.append((summary, p50, p95))

    width, height = 980, 600
    left, right, top, bottom = 96, 44, 84, 104
    chart_w = width - left - right
    chart_h = height - top - bottom
    max_gain = max(abs(v) for _, p50, p95 in rows for v in (p50, p95))
    y_max = max(5.0, math.ceil(max_gain * 1.3 / 5) * 5)

    parts = svg_start(width, height, "Go PGO percentage improvement")
    parts += percent_frame(left, top, chart_w, chart_h, y_max)
    parts.append(text(width / 2, 34, "Go PGO Profile Cache: Latency Improvement", 22, "middle", "#0f172a", 700))
    parts.append(text(width / 2, 58, "Percent improvement versus the no-PGO binary. Higher is better.", 13, "middle", "#475569"))

    group_w = chart_w / len(rows)
    bar_w = min(58, group_w * 0.25)
    zero_y = top + chart_h / 2
    p50_color = "#16a34a"
    p95_color = "#7c3aed"
    for idx, (summary, p50, p95) in enumerate(rows):
        cx = left + group_w * (idx + 0.5)
        for offset, value, color in [(-bar_w * 0.58, p50, p50_color), (bar_w * 0.58, p95, p95_color)]:
            h = abs(value) / y_max * (chart_h / 2)
            y = zero_y - h if value >= 0 else zero_y
            x = cx + offset - bar_w / 2
            parts.append(rect(x, y, bar_w, h, color))
            label_y = y - 8 if value >= 0 else y + h + 18
            parts.append(text(x + bar_w / 2, label_y, f"{value:+.1f}%", 12, "middle", "#334155", 600))
        parts.append(text(cx, top + chart_h + 28, str(summary["display"]), 12, "middle", "#334155", 600))

    parts.append(rect(width - right - 236, top + 6, 12, 12, p50_color))
    parts.append(text(width - right - 218, top + 17, "p50 improvement", 12, "start", "#334155"))
    parts.append(rect(width - right - 112, top + 6, 12, 12, p95_color))
    parts.append(text(width - right - 94, top + 17, "p95 improvement", 12, "start", "#334155"))
    parts.append("</svg>")
    return "\n".join(parts)


def chart_frame(left: int, top: int, chart_w: int, chart_h: int, y_max: float, y_label: str) -> list[str]:
    parts = []
    for i in range(6):
        value = y_max * i / 5
        y = top + chart_h - (value / y_max) * chart_h
        parts.append(line(left, y, left + chart_w, y, "#e2e8f0", 1))
        parts.append(text(left - 12, y + 4, f"{value:.0f}", 11, "end", "#64748b"))
    parts.append(line(left, top, left, top + chart_h, "#64748b", 1.2))
    parts.append(line(left, top + chart_h, left + chart_w, top + chart_h, "#64748b", 1.2))
    parts.append(text(24, top + chart_h / 2, y_label, 13, "middle", "#334155", 600, rotate=-90))
    return parts


def percent_frame(left: int, top: int, chart_w: int, chart_h: int, y_max: float) -> list[str]:
    parts = []
    for i in range(-2, 3):
        value = y_max * i / 2
        y = top + chart_h / 2 - (value / y_max) * (chart_h / 2)
        color = "#94a3b8" if i == 0 else "#e2e8f0"
        parts.append(line(left, y, left + chart_w, y, color, 1.2 if i == 0 else 1))
        parts.append(text(left - 12, y + 4, f"{value:.0f}%", 11, "end", "#64748b"))
    parts.append(line(left, top, left, top + chart_h, "#64748b", 1.2))
    parts.append(line(left, top + chart_h, left + chart_w, top + chart_h, "#64748b", 1.2))
    parts.append(text(24, top + chart_h / 2, "Improvement vs no PGO", 13, "middle", "#334155", 600, rotate=-90))
    return parts


def legend(labels, x: float, y: float) -> list[str]:
    parts = []
    for idx, label in enumerate(label_order({label: [] for label in labels})):
        row_y = y + idx * 24
        color = color_for(label)
        parts.append(line(x, row_y, x + 30, row_y, color, 3))
        parts.append(circle(x + 15, row_y, 4, color))
        parts.append(text(x + 42, row_y + 4, display(label), 12, "start", "#334155", 600))
    return parts


def label_order(series: dict[str, object]) -> list[str]:
    def key(label: str) -> tuple[int, int, str]:
        if label == "go-nopgo":
            return (0, 0, label)
        match = re.fullmatch(r"go-pgo-(\d+)", label)
        if match:
            return (1, int(match.group(1)), label)
        return (2, 0, label)

    return sorted(series, key=key)


def color_for(label: str) -> str:
    if label in COLORS:
        return COLORS[label]
    match = re.fullmatch(r"go-pgo-(\d+)", label)
    if match:
        index = max(int(match.group(1)) - 1, 0) % len(PGO_COLORS)
        return PGO_COLORS[index]
    return "#475569"


def display(label: str) -> str:
    match = re.fullmatch(r"go-pgo-(\d+)", label)
    if match:
        return f"PGO, {match.group(1)} profiles"
    return DISPLAY.get(label, label)


def percentile(sorted_values: list[float], p: float) -> float:
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = p * (len(sorted_values) - 1)
    lower = int(pos)
    upper = min(lower + 1, len(sorted_values) - 1)
    weight = pos - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight


def pct_change(baseline: float, value: float) -> float:
    return (baseline - value) / baseline * 100.0


def nice_max(value: float) -> float:
    if value <= 0:
        return 1
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


def svg_start(width: int, height: int, title: str) -> list[str]:
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title)}">',
        f"<title>{escape(title)}</title>",
        '<rect width="100%" height="100%" fill="#ffffff"/>',
    ]


def text(x: float, y: float, value: str, size: int, anchor: str, color: str, weight: int = 400, rotate: int | None = None) -> str:
    transform = f' transform="rotate({rotate} {x:.1f} {y:.1f})"' if rotate is not None else ""
    return f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" font-family="Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif" font-size="{size}" font-weight="{weight}" fill="{color}"{transform}>{escape(value)}</text>'


def line(x1: float, y1: float, x2: float, y2: float, color: str, width: float) -> str:
    return f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{color}" stroke-width="{width}"/>'


def polyline(points: list[tuple[float, float]], color: str) -> str:
    encoded = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    return f'<polyline points="{encoded}" fill="none" stroke="{color}" stroke-width="2.4" stroke-linejoin="round" stroke-linecap="round"/>'


def circle(x: float, y: float, radius: float, color: str) -> str:
    return f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{radius:.1f}" fill="{color}" stroke="#ffffff" stroke-width="1"/>'


def rect(x: float, y: float, width: float, height: float, color: str) -> str:
    return f'<rect x="{x:.1f}" y="{y:.1f}" width="{width:.1f}" height="{height:.1f}" rx="3" fill="{color}"/>'


def escape(value: str) -> str:
    return html.escape(value, quote=True)


if __name__ == "__main__":
    main()
