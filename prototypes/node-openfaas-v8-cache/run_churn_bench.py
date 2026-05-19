#!/usr/bin/env python3
"""Run Node/V8 OpenFaaS cachedData invocations with real pod churn."""

from __future__ import annotations

import argparse
import base64
import csv
import json
import statistics
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


CSV_FIELDS = [
    "treatment",
    "function",
    "workload",
    "invocation",
    "segment",
    "invocation_in_segment",
    "churn",
    "pod",
    "pod_uid",
    "restart_ms",
    "http_latency_ms",
    "work_ms",
    "compile_ms",
    "init_ms",
    "execute_ms",
    "total_ms",
    "process_uptime_ms",
    "request_in_pod",
    "build",
    "hostname",
    "node",
    "v8",
    "function_count",
    "rounds",
    "request_invocations",
    "source_bytes",
    "artifact_bytes",
    "cache_imported",
    "artifact_found",
    "cached_data_rejected",
    "import_ms",
    "checksum",
    "status",
    "response_bytes",
    "error",
]


def run(cmd: list[str], timeout: float = 120.0, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout, check=False)
    if check and result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(cmd)}\nstdout={result.stdout}\nstderr={result.stderr}"
        )
    return result


def kubectl_json(extra: list[str], timeout: float = 30.0) -> dict[str, Any]:
    return json.loads(run(["kubectl", *extra, "-o", "json"], timeout=timeout).stdout)


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
            metadata = pod.get("metadata", {})
            name = metadata.get("name", "")
            uid = metadata.get("uid", "")
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
        if name:
            run(
                ["kubectl", "delete", "pod", "-n", args.namespace, name, f"--grace-period={args.grace_period}"],
                timeout=60.0,
                check=False,
            )
    for pod in pods:
        name = pod.get("metadata", {}).get("name")
        if name:
            run(
                [
                    "kubectl",
                    "wait",
                    "--for=delete",
                    f"pod/{name}",
                    "-n",
                    args.namespace,
                    f"--timeout={args.delete_timeout}s",
                ],
                timeout=args.delete_timeout + 10,
                check=False,
            )
    return wait_ready_pod(args, started)


def churn_points(args: argparse.Namespace) -> set[int]:
    points = {1}
    if args.churn_at:
        for item in args.churn_at.split(","):
            item = item.strip()
            if item:
                points.add(int(item))
    if args.segment_length > 0:
        points.update(range(1, args.invocations + 1, args.segment_length))
    return {point for point in points if 1 <= point <= args.invocations}


def request_gateway(args: argparse.Namespace, invocation: int) -> tuple[int, float, bytes, str]:
    headers = {"Content-Type": "application/json"}
    if args.username and args.password:
        token = base64.b64encode(f"{args.username}:{args.password}".encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {token}"
    payload = {
        "workload": args.workload,
        "functionCount": args.function_count,
        "rounds": args.rounds,
        "invocations": args.request_invocations,
        "seed": args.seed + invocation,
    }
    req = urllib.request.Request(
        f"{args.gateway.rstrip('/')}/function/{args.function}/work",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers=headers,
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=args.invoke_timeout) as response:
            return response.status, (time.perf_counter() - started) * 1000.0, response.read(), ""
    except urllib.error.HTTPError as exc:
        return exc.code, (time.perf_counter() - started) * 1000.0, exc.read(), str(exc)
    except Exception as exc:  # noqa: BLE001 - benchmark records transport failures.
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


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = (len(ordered) - 1) * p / 100.0
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    if lower == upper:
        return ordered[lower]
    weight = index - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def summarize(rows: list[dict[str, Any]], args: argparse.Namespace) -> dict[str, Any]:
    ok_rows = [row for row in rows if 200 <= int(row["status"]) < 400 and not row["error"]]
    latencies = [float(row["http_latency_ms"]) for row in ok_rows]
    work = [float(row["work_ms"]) for row in ok_rows]
    compile_ms = [float(row["compile_ms"]) for row in ok_rows]
    return {
        "treatment": args.treatment,
        "workload": args.workload,
        "function_count": args.function_count,
        "rounds": args.rounds,
        "request_invocations": args.request_invocations,
        "invocations": len(rows),
        "ok": len(ok_rows),
        "churn_invocations": [int(row["invocation"]) for row in rows if row["churn"] == "1"],
        "http_latency_ms": stats(latencies),
        "work_ms": stats(work),
        "compile_ms": stats(compile_ms),
        "cached_data_rejected": sum(1 for row in rows if str(row.get("cached_data_rejected")).lower() == "true"),
        "artifact_missing": sum(1 for row in rows if args.treatment != "baseline" and row.get("artifact_found") != "true"),
    }


def stats(values: list[float]) -> dict[str, float]:
    return {
        "mean": statistics.fmean(values) if values else 0.0,
        "min": min(values) if values else 0.0,
        "p50": percentile(values, 50),
        "p95": percentile(values, 95),
        "p99": percentile(values, 99),
        "max": max(values) if values else 0.0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--function", default="node-v8-cache")
    parser.add_argument("--namespace", default="openfaas-fn")
    parser.add_argument("--gateway", default="http://127.0.0.1:8080")
    parser.add_argument("--treatment", required=True)
    parser.add_argument("--workload", default="lusearch")
    parser.add_argument("--function-count", type=int, default=3000)
    parser.add_argument("--rounds", type=int, default=10000)
    parser.add_argument("--request-invocations", type=int, default=8)
    parser.add_argument("--invocations", type=int, default=40)
    parser.add_argument("--segment-length", type=int, default=8)
    parser.add_argument("--churn-at", default="")
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--username", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--grace-period", type=int, default=5)
    parser.add_argument("--delete-timeout", type=int, default=120)
    parser.add_argument("--ready-timeout", type=int, default=300)
    parser.add_argument("--post-ready-delay", type=float, default=0.0)
    parser.add_argument("--poll-interval", type=float, default=1.0)
    parser.add_argument("--invoke-timeout", type=float, default=120.0)
    parser.add_argument("--csv", required=True, type=Path)
    parser.add_argument("--summary", required=True, type=Path)
    args = parser.parse_args()

    churns = churn_points(args)
    pod = ""
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

        status, http_latency_ms, body, error = request_gateway(args, invocation)
        payload = parse_body(body)
        row = {
            "treatment": args.treatment,
            "function": args.function,
            "workload": payload.get("workload") or args.workload,
            "invocation": invocation,
            "segment": segment,
            "invocation_in_segment": invocation - segment_start + 1,
            "churn": "1" if churn else "0",
            "pod": pod,
            "pod_uid": payload.get("pod_uid") or pod_uid,
            "restart_ms": f"{restart_ms:.6f}" if churn else "",
            "http_latency_ms": f"{http_latency_ms:.6f}",
            "work_ms": f"{as_float(payload.get('work_ms')):.6f}",
            "compile_ms": f"{as_float(payload.get('compile_ms')):.6f}",
            "init_ms": f"{as_float(payload.get('init_ms')):.6f}",
            "execute_ms": f"{as_float(payload.get('execute_ms')):.6f}",
            "total_ms": f"{as_float(payload.get('total_ms')):.6f}",
            "process_uptime_ms": f"{as_float(payload.get('process_uptime_ms')):.6f}",
            "request_in_pod": as_int(payload.get("request_in_pod")),
            "build": payload.get("build", ""),
            "hostname": payload.get("hostname", ""),
            "node": payload.get("node", ""),
            "v8": payload.get("v8", ""),
            "function_count": as_int(payload.get("function_count")),
            "rounds": as_int(payload.get("rounds")),
            "request_invocations": as_int(payload.get("request_invocations")),
            "source_bytes": as_int(payload.get("source_bytes")),
            "artifact_bytes": as_int(payload.get("artifact_bytes")),
            "cache_imported": str(bool(payload.get("cache_imported"))).lower(),
            "artifact_found": str(bool(payload.get("artifact_found"))).lower(),
            "cached_data_rejected": str(bool(payload.get("cached_data_rejected"))).lower(),
            "import_ms": f"{as_float(payload.get('import_ms')):.6f}",
            "checksum": payload.get("checksum", ""),
            "status": status,
            "response_bytes": len(body),
            "error": error or payload.get("error", ""),
        }
        rows.append(row)
        print(
            json.dumps(
                {
                    "treatment": args.treatment,
                    "invocation": invocation,
                    "segment": segment,
                    "churn": churn,
                    "status": status,
                    "http_latency_ms": row["http_latency_ms"],
                    "work_ms": row["work_ms"],
                    "compile_ms": row["compile_ms"],
                    "request_in_pod": row["request_in_pod"],
                    "cache_imported": row["cache_imported"],
                    "cached_data_rejected": row["cached_data_rejected"],
                    "error": row["error"],
                },
                sort_keys=True,
            ),
            flush=True,
        )

    args.csv.parent.mkdir(parents=True, exist_ok=True)
    with args.csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in CSV_FIELDS})

    summary = summarize(rows, args)
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
