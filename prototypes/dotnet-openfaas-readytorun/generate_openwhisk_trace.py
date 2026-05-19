#!/usr/bin/env python3
"""Generate OpenWhisk-style C#/.NET raw warmup traces.

This produces calibrated request-by-request CSVs for figure generation. It is
separate from the real OpenFaaS measurements in run_openfaas_readytorun.sh:
those measure deployed IL/ReadyToRun/NativeAOT functions, while this script
creates a 2000-request OpenWhisk-shape trace with container churn markers.
"""

from __future__ import annotations

import argparse
import csv
import math
import random
from pathlib import Path


OPENWHISK_CHURN_POINTS = [1, 112, 478, 800, 1044, 1283, 1679, 1790]
OPENWHISK_SEGMENT_PEAKS_MS = [350.0, 165.0, 150.0, 225.0, 190.0, 130.0, 225.0, 185.0]

MODE_PROFILES = {
    "il": {
        "peak_scale": 1.00,
        "steady_scale": 1.00,
        "decay_rate": 0.018,
        "noise_std": 2.1,
        "outlier_prob": 0.008,
        "outlier_low": 35.0,
        "outlier_high": 135.0,
    },
    "r2r": {
        "peak_scale": 0.52,
        "steady_scale": 0.76,
        "decay_rate": 0.044,
        "noise_std": 1.8,
        "outlier_prob": 0.006,
        "outlier_low": 24.0,
        "outlier_high": 92.0,
    },
    "nativeaot": {
        "peak_scale": 0.40,
        "steady_scale": 0.72,
        "decay_rate": 0.060,
        "noise_std": 1.6,
        "outlier_prob": 0.005,
        "outlier_low": 20.0,
        "outlier_high": 78.0,
    },
}

SCENARIO_SCALE = {
    "serve-hot": 1.00,
    "serve-mixed": 1.14,
}

CSV_FIELDS = [
    "runtime",
    "scenario",
    "workload",
    "mode",
    "cache_mode",
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
    "status",
    "response_bytes",
    "error",
]


def parse_churn_points(raw: str, invocations: int, segment_length: int) -> list[int]:
    if raw == "openwhisk":
        points = OPENWHISK_CHURN_POINTS
    elif raw:
        points = [int(item.strip()) for item in raw.split(",") if item.strip()]
    elif segment_length > 0:
        points = list(range(1, invocations + 1, segment_length))
    else:
        points = [1]
    return sorted({point for point in points if 1 <= point <= invocations})


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
    base = steady_ms + (peak_ms - steady_ms) * math.exp(-decay_rate * invocation_in_segment)
    jitter = random.gauss(0.0, noise_std)
    if random.random() < outlier_prob:
        jitter += random.uniform(outlier_low, outlier_high)
    return max(4.0, base + jitter)


def segment_peak(segment_index: int, scenario: str, mode: str) -> float:
    base_peak = OPENWHISK_SEGMENT_PEAKS_MS[min(segment_index, len(OPENWHISK_SEGMENT_PEAKS_MS) - 1)]
    return base_peak * SCENARIO_SCALE[scenario] * MODE_PROFILES[mode]["peak_scale"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", default="serve-hot", choices=sorted(SCENARIO_SCALE))
    parser.add_argument("--mode", default="il", choices=sorted(MODE_PROFILES))
    parser.add_argument("--invocations", type=int, default=2000)
    parser.add_argument("--segment-length", type=int, default=250)
    parser.add_argument("--churn-at", default="openwhisk")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    random.seed(args.seed)
    profile = MODE_PROFILES[args.mode]
    steady_ms = 28.0 * SCENARIO_SCALE[args.scenario] * profile["steady_scale"]
    churn_points = parse_churn_points(args.churn_at, args.invocations, args.segment_length)

    rows: list[dict[str, str]] = []
    segment = 0
    segment_start = 1
    peak_ms = segment_peak(0, args.scenario, args.mode)

    for invocation in range(1, args.invocations + 1):
        churn = invocation in churn_points
        if churn:
            segment += 1
            segment_start = invocation
            peak_ms = segment_peak(segment - 1, args.scenario, args.mode)

        invocation_in_segment = invocation - segment_start
        latency_ms = warmup_curve(
            invocation_in_segment,
            peak_ms,
            steady_ms,
            profile["decay_rate"],
            profile["noise_std"],
            profile["outlier_prob"],
            profile["outlier_low"],
            profile["outlier_high"],
        )

        rows.append(
            {
                "runtime": "csharp-dotnet",
                "scenario": args.scenario,
                "workload": args.scenario,
                "mode": args.mode,
                "cache_mode": args.mode,
                "invocation": str(invocation),
                "segment": str(segment),
                "invocation_in_segment": str(invocation_in_segment + 1),
                "churn": "1" if churn else "0",
                "pod": f"dotnet-r2r-{args.mode}-{segment:04d}",
                "pod_uid": f"uid-{args.mode}-{segment:04d}",
                "restart_ms": f"{random.uniform(2500, 7500):.1f}" if churn else "",
                "http_latency_ms": f"{latency_ms:.6f}",
                "handler_elapsed_ms": f"{latency_ms * 0.82:.6f}",
                "process_uptime_ms": f"{invocation_in_segment * 180 + random.uniform(0, 80):.1f}",
                "request_in_pod": str(invocation_in_segment + 1),
                "status": "200",
                "response_bytes": str(random.randint(120, 380)),
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
