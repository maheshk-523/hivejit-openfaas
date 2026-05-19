#!/usr/bin/env python3
"""Plot DaCapo/OpenFaaS latency curves with pod churn markers."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any


def read_rows(path: Path, metric: str) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    parsed = []
    for row in rows:
        try:
            status = int(row.get("status") or 0)
            latency = float(row.get(metric) or 0.0)
        except ValueError:
            continue
        parsed.append(
            {
                "invocation": int(row["invocation"]),
                "latency_ms": latency,
                "status": status,
                "churn": row.get("churn") == "1",
                "benchmark": row.get("benchmark", ""),
            }
        )
    return parsed


def ewma(values: list[float], alpha: float) -> list[float]:
    smoothed = []
    current = 0.0
    for index, value in enumerate(values):
        current = value if index == 0 else alpha * value + (1.0 - alpha) * current
        smoothed.append(current)
    return smoothed


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


def render_svg(rows: list[dict[str, Any]], out: Path, title: str, alpha: float) -> None:
    ok_rows = [row for row in rows if 200 <= row["status"] < 400]
    if not ok_rows:
        raise SystemExit("no successful rows to plot")

    width = 1500
    height = 720
    left = 86
    right = 40
    top = 74
    bottom = 82
    chart_w = width - left - right
    chart_h = height - top - bottom
    max_invocation = max(row["invocation"] for row in ok_rows)
    max_latency = max(row["latency_ms"] for row in ok_rows)
    y_max = nice_max(max_latency * 1.08)

    def x_scale(invocation: int) -> float:
        return left + ((invocation - 1) / max(max_invocation - 1, 1)) * chart_w

    def y_scale(latency: float) -> float:
        return top + chart_h - (latency / y_max) * chart_h

    raw_points = " ".join(
        f'{x_scale(row["invocation"]):.1f},{y_scale(row["latency_ms"]):.1f}' for row in ok_rows
    )
    smooth = ewma([row["latency_ms"] for row in ok_rows], alpha)
    smooth_points = " ".join(
        f'{x_scale(row["invocation"]):.1f},{y_scale(latency):.1f}' for row, latency in zip(ok_rows, smooth)
    )

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text x="{width / 2:.1f}" y="36" text-anchor="middle" font-family="Helvetica, Arial, sans-serif" font-size="24" font-weight="700" fill="#111827">{svg_escape(title)}</text>',
    ]

    for index in range(7):
        value = y_max * index / 6
        y = y_scale(value)
        lines.append(f'<line x1="{left}" y1="{y:.1f}" x2="{width - right}" y2="{y:.1f}" stroke="#d1d5db"/>')
        lines.append(
            f'<text x="{left - 12}" y="{y + 4:.1f}" text-anchor="end" font-family="Helvetica, Arial, sans-serif" font-size="13" fill="#111827">{value:.0f}</text>'
        )

    tick_count = 10
    for index in range(tick_count + 1):
        invocation = 1 + round((max_invocation - 1) * index / tick_count)
        x = x_scale(invocation)
        lines.append(f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{top + chart_h}" stroke="#f3f4f6"/>')
        lines.append(
            f'<text x="{x:.1f}" y="{top + chart_h + 28}" text-anchor="middle" font-family="Helvetica, Arial, sans-serif" font-size="13" fill="#111827">{invocation}</text>'
        )

    for row in ok_rows:
        if row["churn"]:
            x = x_scale(row["invocation"])
            lines.append(
                f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{top + chart_h}" stroke="#1f77b4" stroke-width="1.8" stroke-dasharray="7 5" opacity="0.9"/>'
            )

    lines.extend(
        [
            f'<line x1="{left}" y1="{top + chart_h}" x2="{width - right}" y2="{top + chart_h}" stroke="#111827"/>',
            f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + chart_h}" stroke="#111827"/>',
            f'<polyline points="{raw_points}" fill="none" stroke="#1f77b4" stroke-width="1.9" opacity="0.72"/>',
            f'<polyline points="{smooth_points}" fill="none" stroke="#ff7f0e" stroke-width="4.2" opacity="0.95"/>',
        ]
    )

    legend_x = width - right - 260
    legend_y = top + 18
    lines.extend(
        [
            f'<line x1="{legend_x}" y1="{legend_y}" x2="{legend_x + 36}" y2="{legend_y}" stroke="#1f77b4" stroke-width="2"/>',
            f'<text x="{legend_x + 48}" y="{legend_y + 4}" font-family="Helvetica, Arial, sans-serif" font-size="13" fill="#111827">raw invocation latency</text>',
            f'<line x1="{legend_x}" y1="{legend_y + 28}" x2="{legend_x + 36}" y2="{legend_y + 28}" stroke="#ff7f0e" stroke-width="4"/>',
            f'<text x="{legend_x + 48}" y="{legend_y + 32}" font-family="Helvetica, Arial, sans-serif" font-size="13" fill="#111827">EWMA latency</text>',
            f'<line x1="{legend_x}" y1="{legend_y + 56}" x2="{legend_x + 36}" y2="{legend_y + 56}" stroke="#1f77b4" stroke-width="1.8" stroke-dasharray="7 5"/>',
            f'<text x="{legend_x + 48}" y="{legend_y + 60}" font-family="Helvetica, Arial, sans-serif" font-size="13" fill="#111827">pod restart</text>',
        ]
    )

    lines.append(
        f'<text x="{left + chart_w / 2:.1f}" y="{height - 26}" text-anchor="middle" font-family="Helvetica, Arial, sans-serif" font-size="17" fill="#111827">Request index</text>'
    )
    lines.append(
        f'<text x="28" y="{top + chart_h / 2:.1f}" text-anchor="middle" font-family="Helvetica, Arial, sans-serif" font-size="17" fill="#111827" transform="rotate(-90 28 {top + chart_h / 2:.1f})">Latency (ms)</text>'
    )
    lines.append("</svg>")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_summary(rows: list[dict[str, Any]], out: Path, metric: str) -> None:
    ok_rows = [row for row in rows if 200 <= row["status"] < 400]
    values = [row["latency_ms"] for row in ok_rows]
    summary = {
        "metric": metric,
        "rows": len(rows),
        "ok": len(ok_rows),
        "churn_invocations": [row["invocation"] for row in rows if row["churn"]],
        "min_ms": min(values) if values else 0.0,
        "max_ms": max(values) if values else 0.0,
        "mean_ms": sum(values) / len(values) if values else 0.0,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True, type=Path)
    parser.add_argument("--svg", required=True, type=Path)
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--metric", choices=["http_latency_ms", "handler_elapsed_ms"], default="http_latency_ms")
    parser.add_argument("--title", default="")
    parser.add_argument("--ewma-alpha", type=float, default=0.16)
    args = parser.parse_args()

    rows = read_rows(args.csv, args.metric)
    title = args.title or f"DaCapo OpenFaaS warmup with pod churn ({args.csv.stem})"
    render_svg(rows, args.svg, title, args.ewma_alpha)
    if args.summary:
        write_summary(rows, args.summary, args.metric)
    print(f"wrote {args.svg}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
