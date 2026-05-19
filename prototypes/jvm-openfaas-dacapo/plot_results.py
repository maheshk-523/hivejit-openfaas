#!/usr/bin/env python3
"""Render combined graphs for JVM DaCapo OpenFaaS HTTP benchmark runs."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any


COLORS = ["#2563eb", "#0f766e", "#b45309", "#7c3aed", "#dc2626", "#0891b2"]


def load_rows(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        row["invocation"] = int(row["invocation"])
        row["latency_ms"] = float(row["latency_ms"])
        row["status"] = int(float(row["status"]))
    return rows


def load_summary(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def svg_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def render_curves(path: Path, series: dict[str, list[dict[str, Any]]], summaries: list[dict[str, Any]]) -> None:
    width = 1040
    height = 460
    margin_left = 74
    margin_right = 32
    margin_top = 58
    margin_bottom = 78
    plot_w = width - margin_left - margin_right
    plot_h = height - margin_top - margin_bottom

    all_rows = [row for rows in series.values() for row in rows]
    max_invocation = max(row["invocation"] for row in all_rows)
    max_latency = max(row["latency_ms"] for row in all_rows)
    y_max = max(1.0, math.ceil(max_latency / 100.0) * 100.0)

    def x_scale(invocation: int) -> float:
        return margin_left + ((invocation - 1) / max(1, max_invocation - 1)) * plot_w

    def y_scale(latency: float) -> float:
        return margin_top + plot_h - (latency / y_max) * plot_h

    grid = []
    for i in range(6):
        value = y_max * i / 5
        y = y_scale(value)
        grid.append(f'<line x1="{margin_left}" y1="{y:.1f}" x2="{width - margin_right}" y2="{y:.1f}" stroke="#e5e7eb" />')
        grid.append(f'<text x="{margin_left - 10}" y="{y + 4:.1f}" text-anchor="end" font-size="12" fill="#475569">{value:.0f}</text>')

    paths = []
    for index, (label, rows) in enumerate(series.items()):
        points = " ".join(f'{x_scale(row["invocation"]):.1f},{y_scale(row["latency_ms"]):.1f}' for row in rows)
        color = COLORS[index % len(COLORS)]
        paths.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2.4" opacity="0.86" />')

    legend = []
    x = margin_left
    y = height - 48
    for index, summary in enumerate(summaries):
        label = summary["label"]
        color = COLORS[index % len(COLORS)]
        text = f"{label} p50={summary['p50_ms']:.1f}ms p95={summary['p95_ms']:.1f}ms"
        legend.append(f'<rect x="{x}" y="{y - 11}" width="13" height="13" fill="{color}" />')
        legend.append(f'<text x="{x + 18}" y="{y}" font-size="12" fill="#334155">{svg_escape(text)}</text>')
        x += 315
        if x > width - 260:
            x = margin_left
            y += 18

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <rect width="100%" height="100%" fill="#ffffff" />
  <text x="{margin_left}" y="29" font-family="Arial, sans-serif" font-size="20" font-weight="700" fill="#111827">JVM DaCapo on OpenFaaS latency</text>
  <text x="{margin_left}" y="48" font-family="Arial, sans-serif" font-size="12" fill="#475569">Gateway request latency through OpenFaaS; lower is better.</text>
  <g font-family="Arial, sans-serif">
    {''.join(grid)}
    <line x1="{margin_left}" y1="{margin_top + plot_h}" x2="{width - margin_right}" y2="{margin_top + plot_h}" stroke="#94a3b8" />
    <line x1="{margin_left}" y1="{margin_top}" x2="{margin_left}" y2="{margin_top + plot_h}" stroke="#94a3b8" />
    {''.join(paths)}
    <text x="{margin_left + plot_w / 2:.1f}" y="{height - 18}" text-anchor="middle" font-size="13" fill="#334155">HTTP invocation number</text>
    <text x="18" y="{margin_top + plot_h / 2:.1f}" text-anchor="middle" font-size="13" fill="#334155" transform="rotate(-90 18 {margin_top + plot_h / 2:.1f})">Latency (ms)</text>
    {''.join(legend)}
  </g>
</svg>
"""
    path.write_text(svg, encoding="utf-8")


def render_bars(path: Path, summaries: list[dict[str, Any]]) -> None:
    width = 860
    height = 430
    margin_left = 82
    margin_right = 38
    margin_top = 54
    margin_bottom = 76
    plot_w = width - margin_left - margin_right
    plot_h = height - margin_top - margin_bottom

    max_value = max(max(float(summary["p50_ms"]), float(summary["p95_ms"])) for summary in summaries)
    y_max = max(1.0, math.ceil(max_value / 100.0) * 100.0)

    def y_scale(value: float) -> float:
        return margin_top + plot_h - (value / y_max) * plot_h

    grid = []
    for i in range(6):
        value = y_max * i / 5
        y = y_scale(value)
        grid.append(f'<line x1="{margin_left}" y1="{y:.1f}" x2="{width - margin_right}" y2="{y:.1f}" stroke="#e5e7eb" />')
        grid.append(f'<text x="{margin_left - 10}" y="{y + 4:.1f}" text-anchor="end" font-size="12" fill="#475569">{value:.0f}</text>')

    group_w = plot_w / len(summaries)
    bars = []
    for i, summary in enumerate(summaries):
        base_x = margin_left + i * group_w + max(20, group_w * 0.18)
        for j, metric in enumerate(("p50_ms", "p95_ms")):
            value = float(summary[metric])
            bar_w = min(42, group_w * 0.22)
            gap = 12
            x = base_x + j * (bar_w + gap)
            y = y_scale(value)
            color = "#2563eb" if metric == "p50_ms" else "#dc8618"
            bars.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{margin_top + plot_h - y:.1f}" fill="{color}" />')
            bars.append(f'<text x="{x + bar_w / 2:.1f}" y="{y - 7:.1f}" text-anchor="middle" font-size="12" fill="#334155">{value:.0f}</text>')
        bars.append(f'<text x="{base_x + 46:.1f}" y="{height - 43}" text-anchor="middle" font-size="12" fill="#334155">{svg_escape(summary["label"])}</text>')

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <rect width="100%" height="100%" fill="#ffffff" />
  <text x="{margin_left}" y="29" font-family="Arial, sans-serif" font-size="20" font-weight="700" fill="#111827">JVM DaCapo on OpenFaaS p50/p95</text>
  <text x="{margin_left}" y="48" font-family="Arial, sans-serif" font-size="12" fill="#475569">Gateway request latency in milliseconds.</text>
  <g font-family="Arial, sans-serif">
    {''.join(grid)}
    <line x1="{margin_left}" y1="{margin_top + plot_h}" x2="{width - margin_right}" y2="{margin_top + plot_h}" stroke="#94a3b8" />
    <line x1="{margin_left}" y1="{margin_top}" x2="{margin_left}" y2="{margin_top + plot_h}" stroke="#94a3b8" />
    {''.join(bars)}
    <rect x="{width - 184}" y="23" width="12" height="12" fill="#2563eb" />
    <text x="{width - 166}" y="34" font-size="12" fill="#334155">p50</text>
    <rect x="{width - 122}" y="23" width="12" height="12" fill="#dc8618" />
    <text x="{width - 104}" y="34" font-size="12" fill="#334155">p95</text>
    <text x="18" y="{margin_top + plot_h / 2:.1f}" text-anchor="middle" font-size="13" fill="#334155" transform="rotate(-90 18 {margin_top + plot_h / 2:.1f})">Latency (ms)</text>
  </g>
</svg>
"""
    path.write_text(svg, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--prefix", default="jvm-openfaas-dacapo")
    args = parser.parse_args()

    summaries = []
    series = {}
    for summary_path in sorted(args.results.glob("*.json")):
        label = summary_path.stem
        csv_path = args.results / f"{label}.csv"
        if not csv_path.exists():
            continue
        summary = load_summary(summary_path)
        summary["label"] = label
        summaries.append(summary)
        series[label] = load_rows(csv_path)

    if not summaries:
        raise SystemExit(f"no matching CSV/JSON result pairs found in {args.results}")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    render_curves(args.out_dir / f"{args.prefix}-latency-curves.svg", series, summaries)
    render_bars(args.out_dir / f"{args.prefix}-p50-p95.svg", summaries)
    (args.out_dir / f"{args.prefix}-summary.json").write_text(json.dumps(summaries, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
