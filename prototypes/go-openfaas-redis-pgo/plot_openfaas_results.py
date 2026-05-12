#!/usr/bin/env python3
"""Render combined comparison graphs for the Go OpenFaaS Redis PGO run."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from pathlib import Path
from typing import Any


LABELS = {
    "go-openfaas-nopgo": "No PGO",
    "go-openfaas-pgo-5": "PGO, 5 warm profiles",
    "go-openfaas-pgo-10": "PGO, 10 warm profiles",
}

COLORS = {
    "go-openfaas-nopgo": "#334155",
    "go-openfaas-pgo-5": "#0f766e",
    "go-openfaas-pgo-10": "#b45309",
}

PGO_COLORS = ["#0f766e", "#b45309", "#7c3aed", "#dc2626", "#2563eb", "#0891b2"]


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
    margin_left = 72
    margin_right = 32
    margin_top = 54
    margin_bottom = 72
    plot_w = width - margin_left - margin_right
    plot_h = height - margin_top - margin_bottom

    all_rows = [row for rows in series.values() for row in rows]
    max_invocation = max(row["invocation"] for row in all_rows)
    max_latency = max(row["latency_ms"] for row in all_rows)
    y_max = max(1.0, math.ceil(max_latency / 25.0) * 25.0)

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
    for label, rows in series.items():
        points = " ".join(f'{x_scale(row["invocation"]):.1f},{y_scale(row["latency_ms"]):.1f}' for row in rows)
        paths.append(f'<polyline points="{points}" fill="none" stroke="{color_for(label)}" stroke-width="2.4" opacity="0.86" />')

    legend = []
    x = margin_left
    for summary in summaries:
        label = summary["label"]
        text = f"{display(label)} p50={summary['p50_ms']:.1f}ms p95={summary['p95_ms']:.1f}ms"
        legend.append(f'<rect x="{x}" y="{height - 43}" width="13" height="13" fill="{color_for(label)}" />')
        legend.append(f'<text x="{x + 18}" y="{height - 32}" font-size="12" fill="#334155">{svg_escape(text)}</text>')
        x += 300

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <rect width="100%" height="100%" fill="#ffffff" />
  <text x="{margin_left}" y="28" font-family="Arial, sans-serif" font-size="20" font-weight="700" fill="#111827">Go OpenFaaS Redis PGO latency</text>
  <text x="{margin_left}" y="46" font-family="Arial, sans-serif" font-size="12" fill="#475569">Gateway request latency, 80 measured requests per build</text>
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
    margin_left = 80
    margin_right = 40
    margin_top = 52
    margin_bottom = 72
    plot_w = width - margin_left - margin_right
    plot_h = height - margin_top - margin_bottom

    max_value = max(max(summary["p50_ms"], summary["p95_ms"]) for summary in summaries)
    y_max = max(1.0, math.ceil(max_value / 25.0) * 25.0)

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
        base_x = margin_left + i * group_w + 38
        for j, metric in enumerate(("p50_ms", "p95_ms")):
            value = float(summary[metric])
            bar_w = 42
            gap = 12
            x = base_x + j * (bar_w + gap)
            y = y_scale(value)
            color = "#2563eb" if metric == "p50_ms" else "#dc8618"
            bars.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w}" height="{margin_top + plot_h - y:.1f}" fill="{color}" />')
            bars.append(f'<text x="{x + bar_w / 2:.1f}" y="{y - 7:.1f}" text-anchor="middle" font-size="12" fill="#334155">{value:.1f}</text>')
        bars.append(f'<text x="{base_x + 48:.1f}" y="{height - 42}" text-anchor="middle" font-size="12" fill="#334155">{svg_escape(display(summary["label"]))}</text>')

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <rect width="100%" height="100%" fill="#ffffff" />
  <text x="{margin_left}" y="28" font-family="Arial, sans-serif" font-size="20" font-weight="700" fill="#111827">Go OpenFaaS Redis PGO p50/p95</text>
  <text x="{margin_left}" y="46" font-family="Arial, sans-serif" font-size="12" fill="#475569">Lower is better; gateway request latency in milliseconds</text>
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


def label_order(labels: Any) -> list[str]:
    def key(label: str) -> tuple[int, int, str]:
        if label == "go-openfaas-nopgo":
            return (0, 0, label)
        match = re.fullmatch(r"go-openfaas-pgo-(\d+)", label)
        if match:
            return (1, int(match.group(1)), label)
        return (2, 0, label)

    return sorted(set(labels), key=key)


def color_for(label: str) -> str:
    if label in COLORS:
        return COLORS[label]
    match = re.fullmatch(r"go-openfaas-pgo-(\d+)", label)
    if match:
        index = max(int(match.group(1)) - 1, 0) % len(PGO_COLORS)
        return PGO_COLORS[index]
    return "#475569"


def display(label: str) -> str:
    if label in LABELS:
        return LABELS[label]
    match = re.fullmatch(r"go-openfaas-pgo-(\d+)", label)
    if match:
        return f"PGO, {match.group(1)} warm profiles"
    return label


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--prefix", default="go-openfaas-redis-pgo")
    args = parser.parse_args()

    labels = label_order(path.stem for path in args.results.glob("go-openfaas-*.csv"))
    if not labels:
        raise SystemExit(f"no go-openfaas-*.csv files found in {args.results}")
    series = {label: load_rows(args.results / f"{label}.csv") for label in labels}
    summaries = [load_summary(args.results / f"{label}.json") for label in labels]

    args.out_dir.mkdir(parents=True, exist_ok=True)
    render_curves(args.out_dir / f"{args.prefix}-latency-curves.svg", series, summaries)
    render_bars(args.out_dir / f"{args.prefix}-p50-p95.svg", summaries)
    (args.out_dir / f"{args.prefix}-summary.json").write_text(json.dumps(summaries, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
