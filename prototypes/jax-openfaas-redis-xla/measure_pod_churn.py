#!/usr/bin/env python3
"""Measure JAX/XLA OpenFaaS invocations with repeated pod churn."""

from __future__ import annotations

import argparse
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
    "label",
    "signature",
    "invocation",
    "segment",
    "invocation_in_segment",
    "churn",
    "pod",
    "pod_uid",
    "restart_ms",
    "http_latency_ms",
    "status",
    "response_bytes",
    "error",
    "mode",
    "build",
    "handler_ms",
    "process_uptime_ms",
    "request_in_pod",
    "compile_or_load_ms",
    "execute_ms_median",
    "cache_enabled",
    "cache_files",
    "cache_bytes",
    "import_ms",
    "cache_imported",
    "artifact_found",
    "artifact_bytes",
    "redis_key",
    "checksum",
]


def run(cmd: list[str], timeout: float = 120.0, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout, check=False)
    if check and result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(cmd)}\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )
    return result


def kubectl_json(args: argparse.Namespace, extra: list[str], timeout: float = 30.0) -> dict[str, Any]:
    result = run(["kubectl", *extra, "-o", "json"], timeout=timeout)
    return json.loads(result.stdout)


def list_function_pods(args: argparse.Namespace) -> list[dict[str, Any]]:
    data = kubectl_json(args, ["get", "pod", "-n", args.namespace, "-l", f"faas_function={args.function}"])
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


def invoke(args: argparse.Namespace, invocation: int) -> tuple[int, float, bytes, str]:
    payload = json.dumps(
        {
            "signature": args.signature,
            "executions": args.executions,
            "compile_variants": args.compile_variants,
            "variant_schedule": args.variant_schedule,
            "iteration": 1 if args.fixed_iteration else invocation,
            "label": args.label,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{args.gateway.rstrip('/')}/function/{args.function}/work",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=args.invoke_timeout) as response:
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


def flatten_row(
    args: argparse.Namespace,
    invocation: int,
    segment: int,
    segment_start: int,
    churn: bool,
    pod: str,
    pod_uid: str,
    restart_ms: float,
    status: int,
    http_latency_ms: float,
    body: bytes,
    error: str,
) -> dict[str, Any]:
    payload = parse_body(body)
    result = payload.get("result") or {}
    config = payload.get("config") or {}
    import_meta = payload.get("import") or config.get("import") or {}
    return {
        "label": args.label,
        "signature": args.signature,
        "invocation": invocation,
        "segment": segment,
        "invocation_in_segment": invocation - segment_start + 1,
        "churn": "1" if churn else "0",
        "pod": pod,
        "pod_uid": payload.get("pod_uid") or pod_uid,
        "restart_ms": f"{restart_ms:.6f}" if churn else "",
        "http_latency_ms": f"{http_latency_ms:.6f}",
        "status": status,
        "response_bytes": len(body),
        "error": error or payload.get("error", ""),
        "mode": payload.get("mode", ""),
        "build": payload.get("build", ""),
        "handler_ms": f"{as_float(payload.get('handler_ms')):.6f}",
        "process_uptime_ms": f"{as_float(payload.get('process_uptime_ms')):.6f}",
        "request_in_pod": as_int(payload.get("request_in_pod")),
        "compile_or_load_ms": f"{as_float(result.get('compile_or_load_ms')):.6f}",
        "execute_ms_median": f"{as_float(result.get('execute_ms_median')):.6f}",
        "cache_enabled": str(bool(result.get("cache_enabled"))).lower(),
        "cache_files": int(payload.get("cache_files") or result.get("cache_files") or 0),
        "cache_bytes": int(payload.get("cache_bytes") or result.get("cache_bytes") or 0),
        "import_ms": f"{as_float(import_meta.get('import_ms')):.6f}",
        "cache_imported": str(bool(import_meta.get("imported"))).lower(),
        "artifact_found": str(bool(import_meta.get("artifact_found"))).lower(),
        "artifact_bytes": int(import_meta.get("artifact_bytes") or 0),
        "redis_key": payload.get("redis_key") or import_meta.get("redis_key") or "",
        "checksum": f"{as_float(result.get('checksum')):.6f}",
    }


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


def stats(values: list[float]) -> dict[str, float]:
    if not values:
        return {"mean": 0.0, "min": 0.0, "p50": 0.0, "p95": 0.0, "max": 0.0}
    return {
        "mean": statistics.fmean(values),
        "min": min(values),
        "p50": percentile(values, 50),
        "p95": percentile(values, 95),
        "max": max(values),
    }


def summarize(rows: list[dict[str, Any]], args: argparse.Namespace) -> dict[str, Any]:
    ok_rows = [row for row in rows if 200 <= int(row["status"]) < 400 and not row.get("error")]
    latencies = [float(row["http_latency_ms"]) for row in ok_rows]
    compile_load = [float(row["compile_or_load_ms"]) for row in ok_rows]
    return {
        "schema": "jax-openfaas-pod-churn.v1",
        "label": args.label,
        "signature": args.signature,
        "invocations": len(rows),
        "ok": len(ok_rows),
        "churn_invocations": [int(row["invocation"]) for row in rows if row["churn"] == "1"],
        "http_latency_ms": stats(latencies),
        "compile_or_load_ms": stats(compile_load),
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
    parser.add_argument("--function", default="jax-xla-redis")
    parser.add_argument("--namespace", default="openfaas-fn")
    parser.add_argument("--gateway", default="http://127.0.0.1:8080")
    parser.add_argument("--signature", default="dacapo-lusearch")
    parser.add_argument("--label", required=True)
    parser.add_argument("--invocations", type=int, default=64)
    parser.add_argument("--segment-length", type=int, default=8)
    parser.add_argument("--churn-at", default="")
    parser.add_argument("--executions", type=int, default=3)
    parser.add_argument("--compile-variants", type=int, default=1)
    parser.add_argument("--variant-schedule", default="")
    parser.add_argument("--fixed-iteration", action="store_true")
    parser.add_argument("--grace-period", type=int, default=5)
    parser.add_argument("--delete-timeout", type=int, default=120)
    parser.add_argument("--ready-timeout", type=int, default=240)
    parser.add_argument("--post-ready-delay", type=float, default=0.0)
    parser.add_argument("--poll-interval", type=float, default=1.0)
    parser.add_argument("--invoke-timeout", type=float, default=180.0)
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

        status, http_latency_ms, body, error = invoke(args, invocation)
        row = flatten_row(
            args,
            invocation,
            segment,
            segment_start,
            churn,
            pod,
            pod_uid,
            restart_ms,
            status,
            http_latency_ms,
            body,
            error,
        )
        rows.append(row)
        print(
            json.dumps(
                {
                    "label": args.label,
                    "signature": args.signature,
                    "invocation": invocation,
                    "position": row["invocation_in_segment"],
                    "churn": row["churn"],
                    "status": status,
                    "http_latency_ms": row["http_latency_ms"],
                    "compile_or_load_ms": row["compile_or_load_ms"],
                    "cache_imported": row["cache_imported"],
                    "error": row["error"],
                },
                sort_keys=True,
            ),
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
