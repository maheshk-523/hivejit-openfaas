#!/usr/bin/env python3
"""Render cold/warm/hot pod lifecycle CSVs as SVG figures."""

from __future__ import annotations

import argparse
import csv
import math
import statistics
from pathlib import Path
from typing import Any


COLORS = {
    "python-generic": "#334155",
    "python-specialized-3": "#0f766e",
}

DISPLAY = {
    "python-generic": "No saved specialization state",
    "python-specialized-3": "Saved specialization artifact",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", action="append", required=True, help="label=csv")
    parser.add_argument("--title", required=True)
    parser.add_argument("--subtitle", default="")
    parser.add_argument(
        "--metric",
        choices=["latency_ms", "work_ms", "cold_total_ms"],
        default="latency_ms",
        help="metric to plot; cold_total_ms adds pod restart time to the first request in each pod",
    )
    parser.add_argument(
        "--aggregate-by-request",
        action="store_true",
        help="plot the median at each request-in-pod position across repeated pod restarts",
    )
    parser.add_argument("--svg", required=True, type=Path)
    args = parser.parse_args()

    datasets = load_inputs(args.input)
    datasets = prepare_datasets(datasets, args.metric, args.aggregate_by_request)
    render_svg(
        datasets,
        args.title,
        args.subtitle,
        args.svg,
        metric_label(args.metric, args.aggregate_by_request),
        args.aggregate_by_request,
    )
    print(f"wrote {args.svg}")
    return 0


def load_inputs(inputs: list[str]) -> dict[str, list[dict[str, Any]]]:
    datasets = {}
    for item in inputs:
        if "=" not in item:
            raise ValueError("--input must use label=path")
        label, raw_path = item.split("=", 1)
        with Path(raw_path).open(newline="", encoding="utf-8") as f:
            rows = []
            for row in csv.DictReader(f):
                rows.append(
                    {
                        "label": label,
                        "benchmark": row["benchmark"],
                        "global_invocation": int(row["global_invocation"]),
                        "pod": int(row["pod"]),
                        "request_in_pod": int(row["request_in_pod"]),
                        "phase": row["phase"],
                        "latency_ms": float(row["latency_ms"]),
                        "work_ms": float(row["work_ms"]),
                        "cold_start_ms": float(row["cold_start_ms"]),
                        "restart_ms": float(row.get("restart_ms", 0.0)),
                    }
                )
        datasets[label] = sorted(rows, key=lambda row: row["global_invocation"])
    return datasets


def prepare_datasets(
    datasets: dict[str, list[dict[str, Any]]],
    metric: str,
    aggregate_by_request: bool,
) -> dict[str, list[dict[str, Any]]]:
    prepared: dict[str, list[dict[str, Any]]] = {}
    for label, rows in datasets.items():
        if not aggregate_by_request:
            prepared[label] = [
                {
                    **row,
                    "plot_ms": metric_value(row, metric),
                    "sample_count": 1,
                }
                for row in rows
            ]
            continue

        grouped: dict[int, list[dict[str, Any]]] = {}
        for row in rows:
            grouped.setdefault(int(row["request_in_pod"]), []).append(row)
        aggregated = []
        for request_in_pod, group in sorted(grouped.items()):
            values = [metric_value(row, metric) for row in group]
            first = group[0]
            aggregated.append(
                {
                    **first,
                    "global_invocation": request_in_pod,
                    "pod": 1,
                    "pod_uid": "median",
                    "latency_ms": statistics.median(row["latency_ms"] for row in group),
                    "work_ms": statistics.median(row["work_ms"] for row in group),
                    "cold_start_ms": statistics.median(row["cold_start_ms"] for row in group),
                    "restart_ms": statistics.median(row["restart_ms"] for row in group),
                    "plot_ms": statistics.median(values),
                    "sample_count": len(values),
                }
            )
        prepared[label] = aggregated
    return prepared


def metric_value(row: dict[str, Any], metric: str) -> float:
    if metric == "work_ms":
        return float(row["work_ms"])
    if metric == "cold_total_ms":
        latency = float(row["latency_ms"])
        if row["phase"] == "cold":
            return latency + float(row.get("restart_ms", 0.0))
        return latency
    return float(row["latency_ms"])


def metric_label(metric: str, aggregated: bool) -> str:
    prefix = "Median " if aggregated else ""
    if metric == "work_ms":
        return f"{prefix}Handler latency (ms)"
    if metric == "cold_total_ms":
        return f"{prefix}Latency incl. pod start (ms)"
    return f"{prefix}Gateway latency (ms)"


def render_svg(
    datasets: dict[str, list[dict[str, Any]]],
    title: str,
    subtitle: str,
    out: Path,
    y_label: str,
    aggregated: bool,
) -> None:
    width, height = 1220, 720
    left, right, top, bottom = 112, 56, 102, 112
    chart_w = width - left - right
    chart_h = height - top - bottom

    all_rows = [row for rows in datasets.values() for row in rows]
    min_x = min(row["global_invocation"] for row in all_rows)
    max_x = max(row["global_invocation"] for row in all_rows)
    y_max = nice_max(max(row["plot_ms"] for row in all_rows) * 1.15)

    def x_scale(invocation: float) -> float:
        return left + ((invocation - min_x) / max(max_x - min_x, 1)) * chart_w

    def y_scale(latency: float) -> float:
        return top + chart_h - (latency / y_max) * chart_h

    first_dataset = next(iter(datasets.values()))
    pods = sorted({row["pod"] for row in first_dataset})
    requests_by_pod = {
        pod: [row for row in first_dataset if row["pod"] == pod]
        for pod in pods
    }

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        text(width / 2, 36, title, 28, "middle", "#111827", 700, serif=True),
    ]
    if subtitle:
        lines.append(text(width / 2, 62, subtitle, 13, "middle", "#374151"))

    draw_phase_bands(lines, requests_by_pod, x_scale, top)
    add_phase_legend(lines, left, top - 20)
    draw_grid(lines, left, top, chart_w, chart_h, y_max, y_scale)

    for pod, rows in requests_by_pod.items():
        first_x = x_scale(rows[0]["global_invocation"])
        lines.append(
            f'<line x1="{first_x:.1f}" y1="{top}" x2="{first_x:.1f}" y2="{top + chart_h}" '
            'stroke="#111827" stroke-dasharray="5 7" opacity="0.38"/>'
        )
        lines.append(text(first_x + 8, top + 18, f"pod {pod} start", 11, "start", "#374151", 600))
        if aggregated:
            lines[-1] = text(first_x + 8, top + 18, "new pod starts", 11, "start", "#374151", 600)

    for label, rows in datasets.items():
        color = COLORS.get(label, "#475569")
        points = [(x_scale(row["global_invocation"]), y_scale(row["plot_ms"])) for row in rows]
        lines.append(
            '<polyline points="'
            + " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
            + f'" fill="none" stroke="{color}" stroke-width="2.8"/>'
        )
        for row, (x, y) in zip(rows, points):
            radius = 4.7 if row["phase"] == "cold" else 3.7
            lines.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{radius:.1f}" fill="{color}"/>')

    for tick in x_ticks(min_x, max_x):
        x = x_scale(tick)
        lines.append(f'<line x1="{x:.1f}" y1="{top + chart_h}" x2="{x:.1f}" y2="{top + chart_h + 7}" stroke="#111827"/>')
        lines.append(text(x, top + chart_h + 29, str(tick), 14, "middle", "#111827"))

    lines.append(f'<line x1="{left}" y1="{top + chart_h}" x2="{left + chart_w}" y2="{top + chart_h}" stroke="#111827"/>')
    lines.append(f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + chart_h}" stroke="#111827"/>')
    x_label = "Invocation # within a new pod" if aggregated else "Invocation Number"
    lines.append(text(left + chart_w / 2, height - 54, x_label, 24, "middle", "#111827"))
    lines.append(
        f'<text x="42" y="{top + chart_h / 2:.1f}" text-anchor="middle" font-family="Helvetica, Arial, sans-serif" '
        f'font-size="24" fill="#111827" transform="rotate(-90 42 {top + chart_h / 2:.1f})">{y_label}</text>'
    )

    add_summary(lines, datasets, left, height, aggregated)
    add_legend(lines, datasets, left + chart_w - 310, top + 86)

    lines.append("</svg>")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def draw_phase_bands(
    lines: list[str],
    requests_by_pod: dict[int, list[dict[str, Any]]],
    x_scale: Any,
    top: int,
) -> None:
    phase_y = top
    phase_h = 42
    for rows in requests_by_pod.values():
        for row in rows:
            x1 = x_scale(row["global_invocation"] - 0.45)
            x2 = x_scale(row["global_invocation"] + 0.45)
            fill = {"cold": "#5b84d7", "warmup": "#f4d7ad", "hot": "#df7774"}[row["phase"]]
            opacity = {"cold": "0.82", "warmup": "0.66", "hot": "0.56"}[row["phase"]]
            lines.append(
                f'<rect x="{x1:.1f}" y="{phase_y}" width="{x2 - x1:.1f}" height="{phase_h}" '
                f'fill="{fill}" opacity="{opacity}"/>'
            )


def add_phase_legend(lines: list[str], x: float, y: float) -> None:
    entries = [
        ("#5b84d7", "Cold start"),
        ("#f4d7ad", "Warmup"),
        ("#df7774", "Hot"),
    ]
    cursor = x
    for color, label in entries:
        lines.append(f'<rect x="{cursor:.1f}" y="{y - 12:.1f}" width="18" height="12" fill="{color}" opacity="0.82"/>')
        lines.append(text(cursor + 25, y - 2, label, 12, "start", "#374151", 600))
        cursor += 104 if label != "Cold start" else 122


def draw_grid(
    lines: list[str],
    left: int,
    top: int,
    chart_w: int,
    chart_h: int,
    y_max: float,
    y_scale: Any,
) -> None:
    for index in range(6):
        value = y_max * index / 5
        y = y_scale(value)
        lines.append(f'<line x1="{left}" y1="{y:.1f}" x2="{left + chart_w}" y2="{y:.1f}" stroke="#e5e7eb"/>')
        lines.append(text(left - 14, y + 5, f"{value:.0f}", 14, "end", "#111827"))


def add_summary(
    lines: list[str],
    datasets: dict[str, list[dict[str, Any]]],
    left: int,
    height: int,
    aggregated: bool,
) -> None:
    baseline_label = "python-generic" if "python-generic" in datasets else next(iter(datasets))
    optimized_label = next((label for label in datasets if label != baseline_label), baseline_label)
    if optimized_label == baseline_label:
        return
    baseline = datasets[baseline_label]
    optimized = datasets[optimized_label]
    base_by_inv = {row["global_invocation"]: row for row in baseline}
    opt_by_inv = {row["global_invocation"]: row for row in optimized}
    common = sorted(set(base_by_inv) & set(opt_by_inv))
    wins = sum(1 for invocation in common if opt_by_inv[invocation]["plot_ms"] < base_by_inv[invocation]["plot_ms"])
    base_hot = statistics.median(row["plot_ms"] for row in baseline if row["phase"] == "hot")
    opt_hot = statistics.median(row["plot_ms"] for row in optimized if row["phase"] == "hot")
    base_cold = statistics.median(row["plot_ms"] for row in baseline if row["phase"] == "cold")
    opt_cold = statistics.median(row["plot_ms"] for row in optimized if row["phase"] == "cold")
    unit = "request positions" if aggregated else "requests"
    lines.append(
        text(
            left,
            height - 26,
            (
                f"Cold median: {display(baseline_label)} {base_cold:.1f} ms, "
                f"{display(optimized_label)} {opt_cold:.1f} ms. Hot median: "
                f"{base_hot:.1f} ms vs {opt_hot:.1f} ms. Saved-state wins {wins}/{len(common)} {unit}."
            ),
            13,
            "start",
            "#111827",
        )
    )


def add_legend(lines: list[str], datasets: dict[str, list[dict[str, Any]]], x: float, y: float) -> None:
    for index, label in enumerate(datasets):
        row_y = y + index * 28
        color = COLORS.get(label, "#475569")
        lines.append(f'<line x1="{x:.1f}" y1="{row_y:.1f}" x2="{x + 34:.1f}" y2="{row_y:.1f}" stroke="{color}" stroke-width="3"/>')
        lines.append(f'<circle cx="{x + 17:.1f}" cy="{row_y:.1f}" r="4" fill="{color}"/>')
        lines.append(text(x + 44, row_y + 5, display(label), 13, "start", "#111827"))


def display(label: str) -> str:
    return DISPLAY.get(label, label)


def x_ticks(min_x: int, max_x: int) -> list[int]:
    if max_x <= 30:
        return list(range(min_x, max_x + 1))
    ticks = [min_x]
    step = 5 if max_x <= 40 else 10
    ticks.extend(range(step, max_x + 1, step))
    ticks.append(max_x)
    return sorted(set(tick for tick in ticks if min_x <= tick <= max_x))


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


def escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def text(
    x: float,
    y: float,
    value: str,
    size: int,
    anchor: str,
    fill: str,
    weight: int | None = None,
    *,
    serif: bool = False,
) -> str:
    family = "Georgia, Times New Roman, serif" if serif else "Helvetica, Arial, sans-serif"
    weight_attr = f' font-weight="{weight}"' if weight else ""
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" font-family="{family}" '
        f'font-size="{size}" fill="{fill}"{weight_attr}>{escape(value)}</text>'
    )


if __name__ == "__main__":
    raise SystemExit(main())
