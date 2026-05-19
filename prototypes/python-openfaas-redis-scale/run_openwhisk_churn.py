#!/usr/bin/env python3
"""Run sequential OpenWhisk-style Python/OpenFaaS invocations with pod churn."""

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


OPENWHISK_POINTS = [1, 112, 478, 800, 1044, 1283, 1679, 1790]

CSV_FIELDS = [
    "benchmark",
    "treatment",
    "function",
    "requests",
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
    "mode",
    "build",
    "used_artifact",
    "cache_imported",
    "artifact_found",
    "import_ms",
    "redis_key",
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
                [
                    "kubectl",
                    "delete",
                    "pod",
                    "-n",
                    args.namespace,
                    name,
                    f"--grace-period={args.grace_period}",
                ],
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


def churn_points(raw: str, invocations: int) -> set[int]:
    if raw == "openwhisk":
        scale = invocations / 2000.0
        points = {max(1, round(point * scale)) for point in OPENWHISK_POINTS}
        points.add(1)
        return {point for point in points if 1 <= point <= invocations}
    points = {1}
    for item in raw.split(","):
        item = item.strip()
        if item:
            points.add(int(item))
    return {point for point in points if 1 <= point <= invocations}


def request_gateway(
    args: argparse.Namespace,
    invocation: int,
) -> tuple[int, float, bytes, str]:
    headers = {"Content-Type": "application/json"}
    if args.username and args.password:
        token = base64.b64encode(f"{args.username}:{args.password}".encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {token}"
    payload = {
        "benchmark": args.benchmark,
        "requests": args.requests,
        "iteration": invocation,
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
    return {
        "benchmark": args.benchmark,
        "treatment": args.treatment,
        "function": args.function,
        "requests": args.requests,
        "invocations": len(rows),
        "ok": len(ok_rows),
        "churn_invocations": [int(row["invocation"]) for row in rows if row["churn"] == "1"],
        "http_latency_ms": {
            "mean": statistics.fmean(latencies) if latencies else 0.0,
            "min": min(latencies) if latencies else 0.0,
            "p50": percentile(latencies, 50),
            "p95": percentile(latencies, 95),
            "p99": percentile(latencies, 99),
            "max": max(latencies) if latencies else 0.0,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--function", required=True)
    parser.add_argument("--namespace", default="openfaas-fn")
    parser.add_argument("--gateway", default="http://127.0.0.1:8080")
    parser.add_argument("--benchmark", default="dacapo-lusearch")
    parser.add_argument("--treatment", required=True)
    parser.add_argument("--requests", type=int, default=12000)
    parser.add_argument("--invocations", type=int, default=240)
    parser.add_argument("--churn-at", default="openwhisk")
    parser.add_argument("--username", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--grace-period", type=int, default=5)
    parser.add_argument("--delete-timeout", type=int, default=120)
    parser.add_argument("--ready-timeout", type=int, default=300)
    parser.add_argument("--post-ready-delay", type=float, default=0.0)
    parser.add_argument("--poll-interval", type=float, default=1.0)
    parser.add_argument("--invoke-timeout", type=float, default=180.0)
    parser.add_argument("--csv", required=True, type=Path)
    parser.add_argument("--summary", required=True, type=Path)
    args = parser.parse_args()

    churns = churn_points(args.churn_at, args.invocations)
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
        result = payload.get("result") or {}
        import_meta = payload.get("import") or {}
        row = {
            "benchmark": args.benchmark,
            "treatment": args.treatment,
            "function": args.function,
            "requests": args.requests,
            "invocation": invocation,
            "segment": segment,
            "invocation_in_segment": invocation - segment_start + 1,
            "churn": "1" if churn else "0",
            "pod": pod,
            "pod_uid": payload.get("pod_uid") or pod_uid,
            "restart_ms": f"{restart_ms:.6f}" if churn else "",
            "http_latency_ms": f"{http_latency_ms:.6f}",
            "handler_elapsed_ms": f"{as_float(payload.get('handler_ms') or result.get('work_ms')):.6f}",
            "process_uptime_ms": f"{as_float(payload.get('process_uptime_ms')):.6f}",
            "request_in_pod": as_int(payload.get("invocation")),
            "mode": payload.get("mode") or args.treatment,
            "build": payload.get("build", ""),
            "used_artifact": str(bool(result.get("used_artifact"))).lower(),
            "cache_imported": str(bool(import_meta.get("imported"))).lower(),
            "artifact_found": str(bool(import_meta.get("artifact_found"))).lower(),
            "import_ms": f"{as_float(import_meta.get('import_ms')):.6f}",
            "redis_key": payload.get("redis_key") or import_meta.get("redis_key") or "",
            "checksum": result.get("checksum", ""),
            "status": status,
            "response_bytes": len(body),
            "error": error or payload.get("error", ""),
        }
        rows.append(row)
        print(
            json.dumps(
                {
                    "benchmark": args.benchmark,
                    "treatment": args.treatment,
                    "invocation": invocation,
                    "segment": segment,
                    "churn": churn,
                    "status": status,
                    "http_latency_ms": row["http_latency_ms"],
                    "handler_elapsed_ms": row["handler_elapsed_ms"],
                    "request_in_pod": row["request_in_pod"],
                    "used_artifact": row["used_artifact"],
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
