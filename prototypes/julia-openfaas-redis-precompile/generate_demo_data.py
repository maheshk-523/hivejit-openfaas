#!/usr/bin/env python3
"""Generate synthetic CSV data that mimics Julia/OpenFaaS warmup latency patterns.

Produces sawtooth warmup curves identical in shape to the OpenWhisk benchmark
graph. Useful for testing plot_churn.py without a live cluster and for making
an explicit "OpenWhisk-shape emulation" trace separate from real measurements.

Usage:
  python3 generate_demo_data.py --workload lusearch --mode baseline \
    --invocations 2000 --segment-length 250 --out demo-baseline.csv

  python3 generate_demo_data.py --workload lusearch --mode sysimage10 \
    --invocations 2000 --churn-at openwhisk --out demo-sysimage10.csv
"""

from __future__ import annotations

import argparse
import csv
import math
import random
from pathlib import Path


OPENWHISK_CHURN_POINTS = [1, 112, 478, 800, 1044, 1283, 1679, 1790]

MODE_PROFILES = {
    # Peak multipliers are applied to the OpenWhisk-like per-segment peak
    # schedule below. Decay is per request inside a fresh pod.
    "baseline": {
        "peak_scale": 1.00,
        "steady_scale": 1.00,
        "decay_rate": 0.018,
        "noise_std": 2.2,
        "outlier_prob": 0.008,
        "outlier_low": 35.0,
        "outlier_high": 140.0,
    },
    "redis": {
        "peak_scale": 0.45,
        "steady_scale": 0.95,
        "decay_rate": 0.052,
        "noise_std": 1.8,
        "outlier_prob": 0.006,
        "outlier_low": 25.0,
        "outlier_high": 90.0,
    },
    "sysimage5": {
        "peak_scale": 0.52,
        "steady_scale": 0.95,
        "decay_rate": 0.042,
        "noise_std": 1.8,
        "outlier_prob": 0.006,
        "outlier_low": 25.0,
        "outlier_high": 95.0,
    },
    "sysimage10": {
        "peak_scale": 0.34,
        "steady_scale": 0.93,
        "decay_rate": 0.064,
        "noise_std": 1.6,
        "outlier_prob": 0.005,
        "outlier_low": 20.0,
        "outlier_high": 80.0,
    },
}

WORKLOAD_SCALE = {
    "lusearch": 1.00,
    "h2": 0.88,
    "eclipse": 1.14,
}

# The reference image has a large initial warmup and then smaller, irregular
# post-churn peaks. Keep this schedule deterministic so generated artifacts are
# reproducible and visually comparable across modes.
OPENWHISK_SEGMENT_PEAKS_MS = [350.0, 165.0, 150.0, 225.0, 190.0, 130.0, 225.0, 185.0]

CSV_FIELDS = [
    "workload",
    "size",
    "invocation",
    "segment",
    "invocation_in_segment",
    "churn",
    "pod",
    "pod_uid",
    "restart_ms",
    "http_latency_ms",
    "handler_elapsed_ms",
    "process_uptime_ms",
    "request_in_pod",
    "cache_mode",
    "status",
    "response_bytes",
    "error",
]


def warmup_curve(
    invocation_in_segment: int,
    peak_ms: float,
    steady_ms: float,
    decay_rate: float,
    noise_std: float,
    outlier_prob: float,
    outlier_low: float,
    outlier_high: float,
) -> float:
    """Exponential decay from peak to steady-state with Gaussian noise."""
    base = steady_ms + (peak_ms - steady_ms) * math.exp(-decay_rate * invocation_in_segment)
    jitter = random.gauss(0, noise_std)
    # Occasional outlier spikes (simulates GC or scheduling hiccup).
    if random.random() < outlier_prob:
        jitter += random.uniform(outlier_low, outlier_high)
    return max(5.0, base + jitter)


def parse_churn_points(raw: str, invocations: int, segment_length: int) -> list[int]:
    if raw == "openwhisk":
        points = OPENWHISK_CHURN_POINTS
    elif raw:
        points = [int(item.strip()) for item in raw.split(",") if item.strip()]
    elif segment_length > 0:
        points = list(range(1, invocations + 1, segment_length))
    else:
        points = [1]
    return sorted({p for p in points if 1 <= p <= invocations})


def segment_peak(segment_index: int, workload: str, mode: str) -> float:
    profile = MODE_PROFILES[mode]
    base_peak = OPENWHISK_SEGMENT_PEAKS_MS[
        min(segment_index, len(OPENWHISK_SEGMENT_PEAKS_MS) - 1)
    ]
    return base_peak * WORKLOAD_SCALE[workload] * profile["peak_scale"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workload", default="lusearch", choices=["lusearch", "h2", "eclipse"])
    parser.add_argument("--mode", default="baseline", choices=sorted(MODE_PROFILES))
    parser.add_argument("--size", type=int, default=1)
    parser.add_argument("--invocations", type=int, default=2000)
    parser.add_argument("--segment-length", type=int, default=250)
    parser.add_argument(
        "--churn-at",
        default="",
        help='comma-separated churn request indices, or "openwhisk" for the reference schedule',
    )
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    random.seed(args.seed)

    profile = MODE_PROFILES[args.mode]
    steady_ms = 28.0 * WORKLOAD_SCALE[args.workload] * profile["steady_scale"]
    churn_points = parse_churn_points(args.churn_at, args.invocations, args.segment_length)

    rows: list[dict[str, str]] = []
    segment = 0
    segment_start = 1
    seg_peak = segment_peak(0, args.workload, args.mode)

    for inv in range(1, args.invocations + 1):
        churn = inv in churn_points
        if churn:
            segment += 1
            segment_start = inv
            seg_peak = segment_peak(segment - 1, args.workload, args.mode)
        inv_in_seg = inv - segment_start

        latency = warmup_curve(
            inv_in_seg,
            seg_peak,
            steady_ms,
            profile["decay_rate"],
            profile["noise_std"],
            profile["outlier_prob"],
            profile["outlier_low"],
            profile["outlier_high"],
        )

        rows.append(
            {
                "workload": args.workload,
                "size": str(args.size),
                "invocation": str(inv),
                "segment": str(segment),
                "invocation_in_segment": str(inv_in_seg + 1),
                "churn": "1" if churn else "0",
                "pod": f"julia-precompile-{segment:04d}",
                "pod_uid": f"uid-{segment:04d}",
                "restart_ms": f"{random.uniform(3000, 8000):.1f}" if churn else "",
                "http_latency_ms": f"{latency:.6f}",
                "handler_elapsed_ms": f"{latency * 0.85:.6f}",
                "process_uptime_ms": f"{(inv_in_seg) * 200 + random.uniform(0, 100):.1f}",
                "request_in_pod": str(inv_in_seg + 1),
                "cache_mode": args.mode,
                "status": "200",
                "response_bytes": str(random.randint(80, 300)),
                "error": "",
            }
        )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"wrote {args.out} ({len(rows)} rows, {segment} segments)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
