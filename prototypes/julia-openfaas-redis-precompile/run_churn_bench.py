#!/usr/bin/env python3
"""Run sequential Julia/OpenFaaS invocations with scripted pod churn.

This is the Julia-flavoured version of the JVM DaCapo churn harness.
The key difference is the workload parameter (workload=lusearch|h2|eclipse)
instead of DaCapo-specific parameters, and an optional export step that pushes
the --trace-compile output to Redis before the pod is killed.

Usage (see run_openfaas_redis_julia_precompile.sh for the full experiment):

  python3 run_churn_bench.py \\
    --function julia-precompile \\
    --namespace openfaas-fn \\
    --gateway http://127.0.0.1:8080 \\
    --workload lusearch \\
    --size 1 \\
    --invocations 60 \\
    --segment-length 20 \\
    --csv results/lusearch.csv \\
    --summary results/lusearch.json
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


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


def run(cmd: list[str], timeout: float = 120.0, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout, check=False)
    if check and result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(cmd)}\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )
    return result


def kubectl_json(extra: list[str], timeout: float = 30.0) -> dict[str, Any]:
    result = run(["kubectl", *extra, "-o", "json"], timeout=timeout)
    return json.loads(result.stdout)


def list_function_pods(args: argparse.Namespace) -> list[dict[str, Any]]:
    data = kubectl_json(["get", "pod", "-n", args.namespace, "-l", f"faas_function={args.function}"])
    return list(data.get("items", []))


def ready_condition(pod: dict[str, Any]) -> bool:
    if pod.get("metadata", {}).get("deletionTimestamp"):
        return False
    return any(
        item.get("type") == "Ready" and item.get("status") == "True"
        for item in pod.get("status", {}).get("conditions", [])
    )


def wait_ready_pod(args: argparse.Namespace, started: float) -> tuple[str, str, float]:
    deadline = time.time() + args.ready_timeout
    last_state = "no pod"
    while time.time() < deadline:
        for pod in list_function_pods(args):
            name = pod.get("metadata", {}).get("name", "")
            uid  = pod.get("metadata", {}).get("uid", "")
            phase = pod.get("status", {}).get("phase", "")
            last_state = f"{name}:{phase}"
            if ready_condition(pod):
                return name, uid, (time.perf_counter() - started) * 1000.0
        time.sleep(args.poll_interval)
    raise TimeoutError(f"timed out waiting for ready pod; last state={last_state}")


def restart_function_pod(args: argparse.Namespace) -> tuple[str, str, float]:
    started = time.perf_counter()
    pods = list_function_pods(args)
    for pod in pods:
        name = pod.get("metadata", {}).get("name")
        if not name:
            continue
        run(
            ["kubectl", "delete", "pod", "-n", args.namespace, name,
             f"--grace-period={args.grace_period}"],
            timeout=60.0, check=False,
        )
    for pod in pods:
        name = pod.get("metadata", {}).get("name")
        if not name:
            continue
        run(
            ["kubectl", "wait", "--for=delete", f"pod/{name}",
             "-n", args.namespace, f"--timeout={args.delete_timeout}s"],
            timeout=args.delete_timeout + 10, check=False,
        )
    return wait_ready_pod(args, started)


def push_trace(args: argparse.Namespace) -> None:
    """Hit the /_/cache/push endpoint on the running pod to export the trace."""
    url = f"{args.gateway.rstrip('/')}/function/{args.function}/_/cache/push"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=30.0) as resp:
            body = resp.read()
            print(json.dumps({"export": json.loads(body.decode("utf-8"))}), flush=True)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"export_error": str(exc)}), flush=True)


def request_url(args: argparse.Namespace) -> str:
    params = {"workload": args.workload, "size": str(args.size)}
    return f"{args.gateway.rstrip('/')}/function/{args.function}/run?{urllib.parse.urlencode(params)}"


def invoke(url: str, timeout: float) -> tuple[int, float, bytes, str]:
    req = urllib.request.Request(url, method="GET")
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, (time.perf_counter() - started) * 1000.0, resp.read(), ""
    except urllib.error.HTTPError as exc:
        return exc.code, (time.perf_counter() - started) * 1000.0, exc.read(), str(exc)
    except Exception as exc:  # noqa: BLE001
        return 0, (time.perf_counter() - started) * 1000.0, b"", str(exc)


def parse_body(body: bytes) -> dict[str, Any]:
    if not body:
        return {}
    try:
        parsed = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def churn_points(args: argparse.Namespace) -> set[int]:
    points = set() if getattr(args, "no_initial_churn", False) else {1}
    if args.churn_at:
        for item in args.churn_at.split(","):
            item = item.strip()
            if item:
                points.add(int(item))
    if args.segment_length > 0:
        points.update(range(1, args.invocations + 1, args.segment_length))
    return {p for p in points if 1 <= p <= args.invocations}


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = (len(ordered) - 1) * (p / 100.0)
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    if lower == upper:
        return ordered[lower]
    weight = index - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def summarize(rows: list[dict[str, Any]], args: argparse.Namespace) -> dict[str, Any]:
    ok_rows = [row for row in rows if 200 <= int(row["status"]) < 400]
    latencies = [float(row["http_latency_ms"]) for row in ok_rows]
    handler_latencies = [
        float(row["handler_elapsed_ms"]) for row in ok_rows if float(row["handler_elapsed_ms"]) > 0
    ]
    return {
        "workload": args.workload,
        "size": args.size,
        "invocations": len(rows),
        "ok": len(ok_rows),
        "churn_invocations": [int(row["invocation"]) for row in rows if row["churn"] == "1"],
        "http_latency_ms": {
            "mean": statistics.fmean(latencies) if latencies else 0.0,
            "min": min(latencies) if latencies else 0.0,
            "p50": percentile(latencies, 50),
            "p90": percentile(latencies, 90),
            "p95": percentile(latencies, 95),
            "p99": percentile(latencies, 99),
            "max": max(latencies) if latencies else 0.0,
        },
        "handler_elapsed_ms": {
            "mean": statistics.fmean(handler_latencies) if handler_latencies else 0.0,
            "p50": percentile(handler_latencies, 50),
            "p95": percentile(handler_latencies, 95),
        },
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in CSV_FIELDS})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--function",       default="julia-precompile")
    parser.add_argument("--namespace",      default="openfaas-fn")
    parser.add_argument("--gateway",        default="http://127.0.0.1:8080")
    parser.add_argument("--workload",       choices=["lusearch", "h2", "eclipse", "jython", "fop", "fopo", "matrix", "regex", "sort"], default="lusearch")
    parser.add_argument("--size",           type=int, default=1)
    parser.add_argument("--invocations",    type=int, default=60)
    parser.add_argument("--segment-length", type=int, default=20)
    parser.add_argument("--churn-at",       default="")
    parser.add_argument("--export-at",      type=int, default=0,
                        help="invocation index after which to push the trace to Redis (0 = disabled)")
    parser.add_argument("--no-initial-churn", action="store_true",
                        help="skip the default pod restart at invocation 1 (use when accumulating traces across workloads)")
    parser.add_argument("--grace-period",   type=int, default=5)
    parser.add_argument("--delete-timeout", type=int, default=120)
    parser.add_argument("--ready-timeout",  type=int, default=300)
    parser.add_argument("--post-ready-delay", type=float, default=0.0,
                        help="seconds to wait after Kubernetes marks a replacement pod ready")
    parser.add_argument("--poll-interval",  type=float, default=1.0)
    parser.add_argument("--invoke-timeout", type=float, default=300.0)
    parser.add_argument("--csv",            required=True, type=Path)
    parser.add_argument("--summary",        required=True, type=Path)
    args = parser.parse_args()

    url    = request_url(args)
    churns = churn_points(args)
    pod    = ""
    pod_uid = ""
    restart_ms = 0.0
    segment = 0
    segment_start = 1
    rows: list[dict[str, Any]] = []

    for invocation in range(1, args.invocations + 1):
        churn = invocation in churns
        if churn:
            segment += 1
            segment_start = invocation
            pod, pod_uid, restart_ms = restart_function_pod(args)
            if args.post_ready_delay > 0:
                time.sleep(args.post_ready_delay)

        if args.export_at > 0 and invocation == args.export_at:
            push_trace(args)

        status, http_latency_ms, body, error = invoke(url, args.invoke_timeout)
        payload = parse_body(body)
        row = {
            "workload":              args.workload,
            "size":                  args.size,
            "invocation":            invocation,
            "segment":               segment,
            "invocation_in_segment": invocation - segment_start + 1,
            "churn":                 "1" if churn else "0",
            "pod":                   pod,
            "pod_uid":               (payload.get("pod_uid") or pod_uid) if payload.get("pod_uid") not in (None, "", "unknown") else pod_uid,
            "restart_ms":            f"{restart_ms:.6f}" if churn else "",
            "http_latency_ms":       f"{http_latency_ms:.6f}",
            "handler_elapsed_ms":    f"{as_float(payload.get('elapsed_ms')):.6f}",
            "process_uptime_ms":     f"{as_float(payload.get('process_uptime_ms')):.6f}",
            "request_in_pod":        as_int(payload.get("request_in_pod")),
            "cache_mode":            payload.get("cache_mode", ""),
            "status":                status,
            "response_bytes":        len(body),
            "error":                 error or payload.get("error", ""),
        }
        rows.append(row)
        print(
            json.dumps({
                "workload":           args.workload,
                "invocation":         invocation,
                "segment":            segment,
                "churn":              churn,
                "status":             status,
                "http_latency_ms":    row["http_latency_ms"],
                "handler_elapsed_ms": row["handler_elapsed_ms"],
                "cache_mode":         row["cache_mode"],
                "pod":                pod,
                "error":              row["error"],
            }, sort_keys=True),
            flush=True,
        )

    write_csv(args.csv, rows)
    summary = summarize(rows, args)
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
