#!/usr/bin/env python3
"""Summarize JAX/OpenFaaS Redis cold-start CSVs and render a compact SVG."""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from pathlib import Path
from typing import Any


def read_rows(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        for field in ("restart_ms", "http_latency_ms", "compile_or_load_ms", "import_ms"):
            row[field] = float(row.get(field) or 0.0)
        row["trial"] = int(row.get("trial") or 0)
    return rows


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = int((p / 100.0) * len(ordered))
    return ordered[min(index, len(ordered) - 1)]


def stats(values: list[float]) -> dict[str, float]:
    if not values:
        return {"mean": 0.0, "min": 0.0, "p50": 0.0, "p95": 0.0, "p99": 0.0, "max": 0.0}
    return {
        "mean": statistics.fmean(values),
        "min": min(values),
        "p50": percentile(values, 50),
        "p95": percentile(values, 95),
        "p99": percentile(values, 99),
        "max": max(values),
    }


def load_inputs(inputs: list[str]) -> dict[str, list[dict[str, Any]]]:
    datasets = {}
    for item in inputs:
        if "=" not in item:
            raise ValueError("--input must use label=path")
        label, path = item.split("=", 1)
        datasets[label] = read_rows(Path(path))
    return datasets


def summarize(datasets: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    labels = {}
    for label, rows in datasets.items():
        statuses: dict[str, int] = {}
        for row in rows:
            status = str(row.get("status", ""))
            statuses[status] = statuses.get(status, 0) + 1
        labels[label] = {
            "trials": len(rows),
            "signature": rows[0].get("signature", "") if rows else "",
            "statuses": statuses,
            "httpLatencyMs": stats([float(row["http_latency_ms"]) for row in rows]),
            "compileOrLoadMs": stats([float(row["compile_or_load_ms"]) for row in rows]),
            "restartMs": stats([float(row["restart_ms"]) for row in rows]),
            "importMs": stats([float(row["import_ms"]) for row in rows]),
        }

    comparisons = {}
    baseline = labels.get("baseline")
    cached = labels.get("redis-cache")
    if baseline and cached:
        for metric, key in (
            ("httpLatencyMs", "httpLatency"),
            ("compileOrLoadMs", "compileOrLoad"),
        ):
            base_p50 = baseline[metric]["p50"]
            cached_p50 = cached[metric]["p50"]
            base_p95 = baseline[metric]["p95"]
            cached_p95 = cached[metric]["p95"]
            comparisons[key] = {
                "p50SavedMs": base_p50 - cached_p50,
                "p95SavedMs": base_p95 - cached_p95,
                "p50Speedup": base_p50 / max(cached_p50, 0.000001),
                "p95Speedup": base_p95 / max(cached_p95, 0.000001),
            }

    return {
        "schema": "jax-openfaas-redis-xla-summary.v1",
        "labels": labels,
        "comparisons": comparisons,
    }


def svg_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def render_svg(summary: dict[str, Any], out: Path) -> None:
    labels = list(summary["labels"].keys())
    if not labels:
        return

    metrics = [
        ("httpLatencyMs", "HTTP first request p50"),
        ("compileOrLoadMs", "JAX compile/load p50"),
        ("restartMs", "Pod restart-to-ready p50"),
    ]
    width = 980
    row_h = 34
    section_gap = 28
    height = 118 + len(metrics) * (len(labels) * row_h + section_gap)
    left = 220
    chart_w = 560
    max_value = max(
        summary["labels"][label][metric]["p50"]
        for metric, _ in metrics
        for label in labels
        if metric in summary["labels"][label]
    )
    max_value = max(max_value, 1.0)
    colors = {"baseline": "#b9413c", "redis-cache": "#2f7d59", "populate": "#526d94"}

    y = 88
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<text x="24" y="36" font-family="Helvetica, Arial, sans-serif" font-size="22" font-weight="700" fill="#202124">JAX/OpenFaaS Redis cold starts</text>',
        '<text x="24" y="60" font-family="Helvetica, Arial, sans-serif" font-size="13" fill="#5f6368">Lower is better. Each trial deletes the OpenFaaS pod before the first request.</text>',
    ]

    for metric, title in metrics:
        lines.append(
            f'<text x="24" y="{y + 20}" font-family="Helvetica, Arial, sans-serif" '
            f'font-size="15" font-weight="700" fill="#202124">{svg_escape(title)}</text>'
        )
        y += 34
        for label in labels:
            value = summary["labels"][label][metric]["p50"]
            p95 = summary["labels"][label][metric]["p95"]
            bar_w = max(2.0, (value / max_value) * chart_w)
            color = colors.get(label, "#6f6f6f")
            lines.append(
                f'<text x="{left - 10}" y="{y + 20}" text-anchor="end" '
                f'font-family="Helvetica, Arial, sans-serif" font-size="12" fill="#3c4043">{svg_escape(label)}</text>'
            )
            lines.append(f'<rect x="{left}" y="{y}" width="{bar_w:.2f}" height="24" rx="3" fill="{color}"/>')
            lines.append(
                f'<text x="{left + bar_w + 8:.2f}" y="{y + 17}" '
                f'font-family="Helvetica, Arial, sans-serif" font-size="12" fill="#202124">'
                f'p50 {value:.1f} ms, p95 {p95:.1f} ms</text>'
            )
            y += row_h
        y += section_gap

    lines.append("</svg>")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", action="append", required=True, help="label=csv")
    parser.add_argument("--summary", required=True, type=Path)
    parser.add_argument("--svg", type=Path)
    args = parser.parse_args()

    summary = summarize(load_inputs(args.input))
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    if args.svg:
        render_svg(summary, args.svg)
    print(json.dumps(summary, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
