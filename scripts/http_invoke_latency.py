#!/usr/bin/env python3
"""Benchmark serverless HTTP invocation latency and render a small SVG graph."""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = int((p / 100.0) * len(ordered))
    return ordered[min(index, len(ordered) - 1)]


def request_once(
    url: str,
    method: str,
    body: bytes | None,
    headers: dict[str, str],
    timeout_s: float,
    invocation: int,
) -> dict[str, Any]:
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    started = time.perf_counter()
    status = 0
    response_bytes = 0
    error = ""

    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as response:
            payload = response.read()
            status = response.status
            response_bytes = len(payload)
    except urllib.error.HTTPError as exc:
        status = exc.code
        payload = exc.read()
        response_bytes = len(payload)
        error = str(exc)
    except Exception as exc:  # noqa: BLE001 - benchmark records failures as data.
        error = str(exc)

    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return {
        "invocation": invocation,
        "latency_ms": elapsed_ms,
        "status": status,
        "response_bytes": response_bytes,
        "error": error,
    }


def run_http(args: argparse.Namespace) -> list[dict[str, Any]]:
    body = args.body.encode("utf-8") if args.body is not None else None
    headers = dict(header.split(":", 1) for header in args.header)
    headers = {key.strip(): value.strip() for key, value in headers.items()}

    for i in range(args.warmup):
        request_once(args.url, args.method, body, headers, args.timeout, -(i + 1))

    rows: list[dict[str, Any]] = []
    if args.concurrency == 1:
        for invocation in range(1, args.requests + 1):
            rows.append(request_once(args.url, args.method, body, headers, args.timeout, invocation))
            if args.sleep_ms > 0:
                time.sleep(args.sleep_ms / 1000.0)
        return rows

    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = [
            executor.submit(request_once, args.url, args.method, body, headers, args.timeout, invocation)
            for invocation in range(1, args.requests + 1)
        ]
        for future in as_completed(futures):
            rows.append(future.result())

    return sorted(rows, key=lambda row: int(row["invocation"]))


def load_csv(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["invocation", "latency_ms", "status", "response_bytes", "error"]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def summarize(rows: list[dict[str, Any]], label: str) -> dict[str, Any]:
    latencies = [float(row["latency_ms"]) for row in rows if row.get("latency_ms") not in ("", None)]
    statuses: dict[str, int] = {}
    for row in rows:
        status = str(row.get("status", ""))
        statuses[status] = statuses.get(status, 0) + 1

    return {
        "label": label,
        "requests": len(rows),
        "mean_ms": statistics.fmean(latencies) if latencies else 0.0,
        "min_ms": min(latencies) if latencies else 0.0,
        "p50_ms": percentile(latencies, 50),
        "p95_ms": percentile(latencies, 95),
        "p99_ms": percentile(latencies, 99),
        "max_ms": max(latencies) if latencies else 0.0,
        "statuses": statuses,
    }


def svg_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def render_svg(path: Path, rows: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    width = 980
    height = 420
    margin_left = 72
    margin_right = 24
    margin_top = 48
    margin_bottom = 64
    plot_w = width - margin_left - margin_right
    plot_h = height - margin_top - margin_bottom

    points = [
        (int(row["invocation"]), float(row["latency_ms"]), int(float(row.get("status") or 0)))
        for row in rows
        if row.get("latency_ms") not in ("", None)
    ]
    if not points:
        path.write_text("<svg xmlns=\"http://www.w3.org/2000/svg\"></svg>\n", encoding="utf-8")
        return

    max_invocation = max(inv for inv, _, _ in points)
    max_latency = max(lat for _, lat, _ in points)
    y_max = max(1.0, math.ceil(max_latency / 10.0) * 10.0)

    def x_scale(invocation: int) -> float:
        if max_invocation <= 1:
            return margin_left
        return margin_left + ((invocation - 1) / (max_invocation - 1)) * plot_w

    def y_scale(latency: float) -> float:
        return margin_top + plot_h - (latency / y_max) * plot_h

    polyline = " ".join(f"{x_scale(inv):.1f},{y_scale(lat):.1f}" for inv, lat, _ in points)
    dots = []
    for inv, lat, status in points:
        color = "#2563eb" if 200 <= status < 400 else "#dc2626"
        dots.append(f'<circle cx="{x_scale(inv):.1f}" cy="{y_scale(lat):.1f}" r="3" fill="{color}" />')

    grid = []
    for i in range(6):
        value = y_max * i / 5
        y = y_scale(value)
        grid.append(f'<line x1="{margin_left}" y1="{y:.1f}" x2="{width - margin_right}" y2="{y:.1f}" stroke="#e5e7eb" />')
        grid.append(f'<text x="{margin_left - 10}" y="{y + 4:.1f}" text-anchor="end" font-size="12" fill="#4b5563">{value:.0f}</text>')

    for label, color in [("p50_ms", "#059669"), ("p95_ms", "#d97706"), ("p99_ms", "#7c3aed")]:
        value = float(summary[label])
        y = y_scale(value)
        grid.append(f'<line x1="{margin_left}" y1="{y:.1f}" x2="{width - margin_right}" y2="{y:.1f}" stroke="{color}" stroke-dasharray="6 5" />')
        grid.append(f'<text x="{width - margin_right - 4}" y="{y - 6:.1f}" text-anchor="end" font-size="12" fill="{color}">{label.replace("_ms", "")}: {value:.1f} ms</text>')

    title = svg_escape(str(summary["label"]))
    subtitle = (
        f"requests={summary['requests']} mean={summary['mean_ms']:.1f}ms "
        f"p50={summary['p50_ms']:.1f}ms p95={summary['p95_ms']:.1f}ms "
        f"p99={summary['p99_ms']:.1f}ms max={summary['max_ms']:.1f}ms"
    )

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <rect width="100%" height="100%" fill="#ffffff" />
  <text x="{margin_left}" y="24" font-family="Arial, sans-serif" font-size="18" font-weight="700" fill="#111827">{title}</text>
  <text x="{margin_left}" y="42" font-family="Arial, sans-serif" font-size="12" fill="#4b5563">{svg_escape(subtitle)}</text>
  <g font-family="Arial, sans-serif">
    {''.join(grid)}
    <line x1="{margin_left}" y1="{margin_top + plot_h}" x2="{width - margin_right}" y2="{margin_top + plot_h}" stroke="#9ca3af" />
    <line x1="{margin_left}" y1="{margin_top}" x2="{margin_left}" y2="{margin_top + plot_h}" stroke="#9ca3af" />
    <polyline points="{polyline}" fill="none" stroke="#1d4ed8" stroke-width="2" opacity="0.7" />
    {''.join(dots)}
    <text x="{margin_left + plot_w / 2:.1f}" y="{height - 18}" text-anchor="middle" font-size="13" fill="#374151">HTTP invocation number</text>
    <text x="18" y="{margin_top + plot_h / 2:.1f}" text-anchor="middle" font-size="13" fill="#374151" transform="rotate(-90 18 {margin_top + plot_h / 2:.1f})">Latency (ms)</text>
    <text x="{width - margin_right}" y="{height - 18}" text-anchor="end" font-size="12" fill="#2563eb">blue: 2xx/3xx</text>
    <text x="{width - margin_right}" y="{height - 34}" text-anchor="end" font-size="12" fill="#dc2626">red: error status</text>
  </g>
</svg>
"""
    path.write_text(svg, encoding="utf-8")


def write_summary(path: Path, summary: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", help="HTTP endpoint to invoke")
    parser.add_argument("--from-csv", help="existing latency CSV to graph instead of invoking HTTP")
    parser.add_argument("--label", default="serverless HTTP latency", help="label used in summaries and graph title")
    parser.add_argument("--requests", type=int, default=50)
    parser.add_argument("--warmup", type=int, default=0)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--sleep-ms", type=float, default=0)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--method", default="GET")
    parser.add_argument("--body")
    parser.add_argument("--header", action="append", default=[], help="HTTP header as 'Name: value'")
    parser.add_argument("--csv", default="generated/serverless-http/latency.csv")
    parser.add_argument("--summary", default="generated/serverless-http/summary.json")
    parser.add_argument("--svg", default="generated/serverless-http/latency.svg")
    args = parser.parse_args()

    if args.from_csv:
        rows = load_csv(Path(args.from_csv))
    else:
        if not args.url:
            raise SystemExit("either --url or --from-csv is required")
        rows = run_http(args)
        write_csv(Path(args.csv), rows)

    summary = summarize(rows, args.label)
    write_summary(Path(args.summary), summary)
    render_svg(Path(args.svg), rows, summary)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
