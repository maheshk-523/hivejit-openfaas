#!/usr/bin/env python3
"""Render large-scale Python OpenFaaS Redis verification CSVs as an SVG."""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
import statistics
from pathlib import Path
from typing import Any


COLORS = {
    "baseline": "#222222",
    "saved": "#0077b6",
}

DISPLAY = {
    "baseline": "no saved state",
    "saved": "saved artifact",
}

PHASES = {
    "cold": ("#5b84d7", 0.16),
    "warmup": ("#f2c46d", 0.22),
    "hot": ("#73c7b7", 0.20),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True, type=Path)
    parser.add_argument("--svg", required=True, type=Path)
    parser.add_argument("--json", required=True, type=Path)
    parser.add_argument("--benchmarks", nargs="+", default=[])
    parser.add_argument("--warmup-requests", type=int, default=3)
    parser.add_argument("--metric", default="latency_ms", choices=["latency_ms", "work_ms"])
    parser.add_argument("--title", default="Real OpenFaaS Python Redis profile-specialization cache at scale")
    return parser.parse_args()


def load_rows(path: Path, metric: str) -> list[dict[str, Any]]:
    parsed_rows = []
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if int(row.get("status") or 0) != 200 or row.get("error"):
                continue
            parsed_rows.append(
                {
                    "benchmark": row["benchmark"],
                    "treatment": row["treatment"],
                    "wave": int(row["wave"]),
                    "shard": int(row["shard"]),
                    "request_in_pod": int(row["request_in_pod"]),
                    "phase": row["phase"],
                    "value": float(row[metric]),
                    "checksum": row.get("checksum", ""),
                }
            )
    baseline_checksums = {
        (row["benchmark"], row["wave"], row["shard"], row["request_in_pod"]): row["checksum"]
        for row in parsed_rows
        if row["treatment"] == "baseline" and row["checksum"]
    }
    rows = []
    for row in parsed_rows:
        if row["treatment"] == "saved":
            key = (row["benchmark"], row["wave"], row["shard"], row["request_in_pod"])
            if row["checksum"] != baseline_checksums.get(key):
                continue
        rows.append(row)
    return rows


def aggregate(rows: list[dict[str, Any]]) -> dict[str, dict[str, dict[int, dict[str, float]]]]:
    grouped: dict[str, dict[str, dict[int, list[float]]]] = {}
    for row in rows:
        grouped.setdefault(row["benchmark"], {}).setdefault(row["treatment"], {}).setdefault(
            row["request_in_pod"], []
        ).append(row["value"])

    out: dict[str, dict[str, dict[int, dict[str, float]]]] = {}
    for benchmark, by_treatment in grouped.items():
        out[benchmark] = {}
        for treatment, by_request in by_treatment.items():
            out[benchmark][treatment] = {}
            for request, values in by_request.items():
                out[benchmark][treatment][request] = {
                    "median": statistics.median(values),
                    "count": len(values),
                }
    return out


def percent_saved(baseline: float, saved: float) -> float:
    if baseline <= 0:
        return 0.0
    return ((baseline - saved) / baseline) * 100.0


def summarize(
    data: dict[str, dict[str, dict[int, dict[str, float]]]],
    benchmarks: list[str],
    warmup_requests: int,
) -> dict[str, Any]:
    summary: dict[str, Any] = {"schema": "python-openfaas-redis-scale-plot-summary.v1", "benchmarks": {}}
    for benchmark in benchmarks:
        baseline = data.get(benchmark, {}).get("baseline", {})
        saved = data.get(benchmark, {}).get("saved", {})
        positions = sorted(set(baseline) & set(saved))
        wins = 0
        by_request = []
        for request in positions:
            base = baseline[request]["median"]
            sav = saved[request]["median"]
            if sav < base:
                wins += 1
            by_request.append(
                {
                    "request_in_pod": request,
                    "baseline_median_ms": base,
                    "saved_median_ms": sav,
                    "saved_pct": percent_saved(base, sav),
                    "baseline_samples": int(baseline[request]["count"]),
                    "saved_samples": int(saved[request]["count"]),
                }
            )
        cold_saved_pct = percent_saved(baseline[1]["median"], saved[1]["median"]) if 1 in positions else 0.0
        hot_positions = [request for request in positions if request > warmup_requests]
        if hot_positions:
            base_hot = statistics.median(baseline[request]["median"] for request in hot_positions)
            saved_hot = statistics.median(saved[request]["median"] for request in hot_positions)
            hot_saved_pct = percent_saved(base_hot, saved_hot)
        else:
            hot_saved_pct = 0.0
        summary["benchmarks"][benchmark] = {
            "median_position_wins": wins,
            "positions": len(positions),
            "cold_saved_pct": cold_saved_pct,
            "hot_saved_pct": hot_saved_pct,
            "by_request": by_request,
        }
    return summary


def text(x: float, y: float, body: str, size: int, anchor: str = "start", weight: int = 400, color: str = "#111827") -> str:
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" '
        f'font-family="Helvetica, Arial, sans-serif" font-size="{size}" '
        f'font-weight="{weight}" fill="{color}">{html.escape(body)}</text>'
    )


def line(x1: float, y1: float, x2: float, y2: float, color: str = "#111827", width: float = 1.0, opacity: float = 1.0) -> str:
    return (
        f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
        f'stroke="{color}" stroke-width="{width:.1f}" opacity="{opacity:.3f}"/>'
    )


def nice_ticks(min_value: float, max_value: float, count: int = 5) -> list[float]:
    if math.isclose(min_value, max_value):
        return [min_value]
    raw_step = (max_value - min_value) / max(count - 1, 1)
    magnitude = 10 ** math.floor(math.log10(raw_step))
    residual = raw_step / magnitude
    if residual <= 1:
        step = magnitude
    elif residual <= 2:
        step = 2 * magnitude
    elif residual <= 5:
        step = 5 * magnitude
    else:
        step = 10 * magnitude
    start = math.floor(min_value / step) * step
    stop = math.ceil(max_value / step) * step
    ticks = []
    value = start
    while value <= stop + step / 2:
        ticks.append(value)
        value += step
    return ticks


def phase_for(request: int, warmup_requests: int) -> str:
    if request == 1:
        return "cold"
    if request <= warmup_requests:
        return "warmup"
    return "hot"


def render_svg(
    data: dict[str, dict[str, dict[int, dict[str, float]]]],
    summary: dict[str, Any],
    benchmarks: list[str],
    warmup_requests: int,
    title: str,
    out: Path,
    metric_label: str,
) -> None:
    panel_count = max(len(benchmarks), 1)
    width = 640 * panel_count
    height = 650
    margin_left = 78
    margin_right = 28
    top = 122
    bottom = 82
    gap = 44
    panel_w = (width - margin_left - margin_right - gap * (panel_count - 1)) / panel_count
    panel_h = height - top - bottom
    max_request = max(
        [request for bench in data.values() for treatment in bench.values() for request in treatment] or [8]
    )

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        text(width / 2, 44, title, 28, "middle", 700),
    ]
    legend_x = width / 2 - 255
    legend_y = 78
    phase_x = legend_x
    for label, (color, opacity) in PHASES.items():
        lines.append(f'<rect x="{phase_x:.1f}" y="{legend_y - 14:.1f}" width="32" height="16" fill="{color}" opacity="{opacity:.3f}"/>')
        lines.append(text(phase_x + 42, legend_y, label, 15))
        phase_x += 118
    series_x = phase_x + 16
    for treatment in ("baseline", "saved"):
        color = COLORS[treatment]
        lines.append(line(series_x, legend_y - 7, series_x + 34, legend_y - 7, color, 3))
        lines.append(f'<circle cx="{series_x + 17:.1f}" cy="{legend_y - 7:.1f}" r="5" fill="{color}"/>')
        lines.append(text(series_x + 44, legend_y, DISPLAY[treatment], 15))
        series_x += 174

    for panel_index, benchmark in enumerate(benchmarks):
        x0 = margin_left + panel_index * (panel_w + gap)
        y0 = top
        baseline = data.get(benchmark, {}).get("baseline", {})
        saved = data.get(benchmark, {}).get("saved", {})
        values = [item["median"] for item in baseline.values()] + [item["median"] for item in saved.values()]
        if not values:
            values = [0.0, 1.0]
        y_min = min(values)
        y_max = max(values)
        padding = max((y_max - y_min) * 0.16, y_max * 0.025, 1.0)
        y_min = max(0.0, y_min - padding)
        y_max = y_max + padding
        ticks = nice_ticks(y_min, y_max)
        y_min = min(ticks)
        y_max = max(ticks)

        def x_scale(request: int | float) -> float:
            return x0 + ((float(request) - 1) / max(max_request - 1, 1)) * panel_w

        def y_scale(value: float) -> float:
            if math.isclose(y_min, y_max):
                return y0 + panel_h / 2
            return y0 + panel_h - ((value - y_min) / (y_max - y_min)) * panel_h

        for request in range(1, max_request + 1):
            phase = phase_for(request, warmup_requests)
            color, opacity = PHASES[phase]
            x1 = x_scale(request - 0.5)
            x2 = x_scale(request + 0.5)
            if request == 1:
                x1 = x0
            if request == max_request:
                x2 = x0 + panel_w
            lines.append(
                f'<rect x="{x1:.1f}" y="{y0:.1f}" width="{x2 - x1:.1f}" height="{panel_h:.1f}" '
                f'fill="{color}" opacity="{opacity:.3f}"/>'
            )

        for tick in ticks:
            y = y_scale(tick)
            lines.append(line(x0, y, x0 + panel_w, y, "#94a3b8", 0.8, 0.35))
            lines.append(text(x0 - 10, y + 5, f"{tick:.0f}", 13, "end", 400, "#111827"))

        for request in range(1, max_request + 1):
            x = x_scale(request)
            lines.append(line(x, y0 + panel_h, x, y0 + panel_h + 6))
            lines.append(text(x, y0 + panel_h + 27, str(request), 14, "middle"))

        lines.append(line(x0, y0 + panel_h, x0 + panel_w, y0 + panel_h, "#111827", 1.2))
        lines.append(line(x0, y0, x0, y0 + panel_h, "#111827", 1.2))
        lines.append(text(x0 + panel_w / 2, y0 - 14, benchmark.removeprefix("dacapo-"), 22, "middle", 500))

        for treatment, series in (("baseline", baseline), ("saved", saved)):
            points = [
                (x_scale(request), y_scale(series[request]["median"]))
                for request in sorted(series)
                if 1 <= request <= max_request
            ]
            if not points:
                continue
            color = COLORS[treatment]
            lines.append(
                '<polyline points="'
                + " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
                + f'" fill="none" stroke="{color}" stroke-width="3.2"/>'
            )
            for x, y in points:
                lines.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5.0" fill="{color}"/>')

        bench_summary = summary["benchmarks"].get(benchmark, {})
        note = [
            f"{bench_summary.get('median_position_wins', 0)}/{bench_summary.get('positions', 0)} median-position wins",
            f"cold saved {bench_summary.get('cold_saved_pct', 0.0):.1f}%",
            f"hot saved {bench_summary.get('hot_saved_pct', 0.0):.1f}%",
        ]
        box_w = min(276, panel_w - 18)
        box_h = 82
        lines.append(
            f'<rect x="{x0 + 10:.1f}" y="{y0 + 18:.1f}" width="{box_w:.1f}" height="{box_h}" '
            'fill="#ffffff" opacity="0.86" stroke="#d1d5db"/>'
        )
        for index, item in enumerate(note):
            lines.append(text(x0 + 22, y0 + 43 + index * 21, item, 14))

        if panel_index == 0:
            lines.append(
                f'<text x="28" y="{y0 + panel_h / 2:.1f}" text-anchor="middle" '
                'font-family="Helvetica, Arial, sans-serif" font-size="20" fill="#111827" '
                f'transform="rotate(-90 28 {y0 + panel_h / 2:.1f})">{html.escape(metric_label)}</text>'
            )

    lines.append(text(width / 2, height - 28, "request in fresh pod", 20, "middle"))
    lines.append("</svg>")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    rows = load_rows(args.csv, args.metric)
    data = aggregate(rows)
    benchmarks = args.benchmarks or sorted(data)
    summary = summarize(data, benchmarks, args.warmup_requests)
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    render_svg(
        data,
        summary,
        benchmarks,
        args.warmup_requests,
        args.title,
        args.svg,
        "median gateway latency (ms)" if args.metric == "latency_ms" else "median handler latency (ms)",
    )
    print(f"wrote {args.svg}")
    print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
