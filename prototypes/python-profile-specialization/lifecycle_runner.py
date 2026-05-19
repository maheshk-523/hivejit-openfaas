#!/usr/bin/env python3
"""Measure OpenFaaS-style pod lifecycle latency for profile specialization."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", type=Path, default=Path(__file__).with_name("pod_worker.py"))
    parser.add_argument("--benchmark", required=True)
    parser.add_argument("--requests", type=int, required=True)
    parser.add_argument("--pods", type=int, default=3)
    parser.add_argument("--requests-per-pod", type=int, default=12)
    parser.add_argument("--warmup-requests", type=int, default=4)
    parser.add_argument("--label", required=True)
    parser.add_argument("--csv", required=True, type=Path)
    parser.add_argument("--artifact", type=Path)
    parser.add_argument(
        "--platform-cold-ms",
        type=float,
        default=0.0,
        help="Optional external pod scheduling/readiness latency to add to first request in each pod.",
    )
    args = parser.parse_args()

    if args.requests <= 0 or args.pods <= 0 or args.requests_per_pod <= 0:
        raise SystemExit("requests, pods, and requests-per-pod must be positive")

    args.csv.parent.mkdir(parents=True, exist_ok=True)
    with args.csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "label",
                "benchmark",
                "global_invocation",
                "pod",
                "request_in_pod",
                "phase",
                "latency_ms",
                "work_ms",
                "cold_start_ms",
                "measured_startup_ms",
                "platform_cold_ms",
                "checksum",
                "used_artifact",
            ],
        )
        writer.writeheader()

        global_invocation = 0
        for pod in range(1, args.pods + 1):
            process, startup_ms = start_worker(args)
            try:
                for request_in_pod in range(1, args.requests_per_pod + 1):
                    global_invocation += 1
                    result = invoke(process, args.requests, seed=global_invocation)
                    cold_start_ms = startup_ms + args.platform_cold_ms if request_in_pod == 1 else 0.0
                    latency_ms = float(result["workMs"]) + cold_start_ms
                    phase = phase_for(request_in_pod, args.warmup_requests)
                    writer.writerow(
                        {
                            "label": args.label,
                            "benchmark": args.benchmark,
                            "global_invocation": global_invocation,
                            "pod": pod,
                            "request_in_pod": request_in_pod,
                            "phase": phase,
                            "latency_ms": f"{latency_ms:.6f}",
                            "work_ms": f"{float(result['workMs']):.6f}",
                            "cold_start_ms": f"{cold_start_ms:.6f}",
                            "measured_startup_ms": f"{startup_ms:.6f}",
                            "platform_cold_ms": f"{args.platform_cold_ms:.6f}",
                            "checksum": result["checksum"],
                            "used_artifact": str(bool(result["usedArtifact"])).lower(),
                        }
                    )
                    print(
                        f"{args.label} {args.benchmark} pod={pod} req={request_in_pod} "
                        f"global={global_invocation} phase={phase} latency_ms={latency_ms:.3f} "
                        f"work_ms={float(result['workMs']):.3f} cold_start_ms={cold_start_ms:.3f}"
                    )
            finally:
                stop_worker(process)
    return 0


def phase_for(request_in_pod: int, warmup_requests: int) -> str:
    if request_in_pod == 1:
        return "cold"
    if request_in_pod <= warmup_requests:
        return "warmup"
    return "hot"


def start_worker(args: argparse.Namespace) -> tuple[subprocess.Popen[str], float]:
    cmd = [
        sys.executable,
        str(args.worker),
        "--benchmark",
        args.benchmark,
    ]
    if args.artifact:
        cmd.extend(["--artifact", str(args.artifact)])

    start = time.perf_counter()
    process = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    if process.stdout is None:
        raise RuntimeError("worker stdout pipe was not created")
    ready_line = process.stdout.readline()
    startup_ms = (time.perf_counter() - start) * 1000.0
    if not ready_line:
        stderr = process.stderr.read() if process.stderr else ""
        raise RuntimeError(f"worker exited before ready: {stderr}")
    ready = json.loads(ready_line)
    if not ready.get("ready"):
        raise RuntimeError(f"worker did not report ready: {ready}")
    return process, startup_ms


def invoke(process: subprocess.Popen[str], requests: int, seed: int) -> dict[str, object]:
    if process.stdin is None or process.stdout is None:
        raise RuntimeError("worker pipes were not created")
    process.stdin.write(json.dumps({"command": "invoke", "requests": requests, "seed": seed}) + "\n")
    process.stdin.flush()
    line = process.stdout.readline()
    if not line:
        stderr = process.stderr.read() if process.stderr else ""
        raise RuntimeError(f"worker exited during invoke: {stderr}")
    result = json.loads(line)
    if "error" in result:
        raise RuntimeError(str(result["error"]))
    return result


def stop_worker(process: subprocess.Popen[str]) -> None:
    if process.stdin is not None:
        try:
            process.stdin.write(json.dumps({"command": "stop"}) + "\n")
            process.stdin.flush()
        except BrokenPipeError:
            pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
