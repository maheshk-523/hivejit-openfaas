#!/usr/bin/env python3
"""Collect simple OpenFaaS HTTP latency samples for the JVM DaCapo wrapper."""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import time
import urllib.error
import urllib.request
from pathlib import Path


def percentile(values: list[float], percent: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = (len(ordered) - 1) * (percent / 100.0)
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    if lower == upper:
        return ordered[lower]
    weight = index - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def invoke(url: str, timeout: float) -> tuple[int, float, int]:
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read()
            status = response.getcode()
    except urllib.error.HTTPError as exc:
        body = exc.read()
        status = exc.code
    latency_ms = (time.perf_counter() - started) * 1000.0
    return status, latency_ms, len(body)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--requests", type=int, required=True)
    parser.add_argument("--warmup", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--csv", required=True, type=Path)
    parser.add_argument("--summary", required=True, type=Path)
    args = parser.parse_args()

    for warmup in range(1, args.warmup + 1):
        status, latency_ms, body_bytes = invoke(args.url, args.timeout)
        print(f"warmup {warmup}/{args.warmup}: status={status} latency_ms={latency_ms:.1f} bytes={body_bytes}", flush=True)
        if status != 200:
            raise SystemExit(f"warmup request failed with HTTP {status}")

    rows = []
    for invocation in range(1, args.requests + 1):
        status, latency_ms, body_bytes = invoke(args.url, args.timeout)
        print(f"sample {invocation}/{args.requests}: status={status} latency_ms={latency_ms:.1f} bytes={body_bytes}", flush=True)
        if status != 200:
            raise SystemExit(f"sample request failed with HTTP {status}")
        rows.append(
            {
                "invocation": invocation,
                "status": status,
                "latency_ms": f"{latency_ms:.3f}",
                "body_bytes": body_bytes,
            }
        )

    latencies = [float(row["latency_ms"]) for row in rows]
    summary = {
        "label": args.label,
        "url": args.url,
        "requests": args.requests,
        "warmup": args.warmup,
        "ok": len(rows),
        "mean_ms": statistics.fmean(latencies),
        "min_ms": min(latencies),
        "p50_ms": percentile(latencies, 50),
        "p90_ms": percentile(latencies, 90),
        "p95_ms": percentile(latencies, 95),
        "p99_ms": percentile(latencies, 99),
        "max_ms": max(latencies),
    }

    args.csv.parent.mkdir(parents=True, exist_ok=True)
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    with args.csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["invocation", "status", "latency_ms", "body_bytes"])
        writer.writeheader()
        writer.writerows(rows)
    args.summary.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
