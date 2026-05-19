#!/usr/bin/env python3
"""Measure a cache-learning chain across fresh OpenFaaS pods."""

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
    "compile_or_load_ms",
    "execute_ms_median",
    "cache_files",
    "cache_bytes",
    "import_ms",
    "cache_imported",
    "artifact_found",
    "artifact_bytes",
    "push_http_ms",
    "export_ms",
    "export_artifact_bytes",
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
            name = pod.get("metadata", {}).get("name", "")
            uid = pod.get("metadata", {}).get("uid", "")
            if ready_condition(pod):
                return name, uid, (time.perf_counter() - started) * 1000.0
            last_state = f"{name}:{pod.get('status', {}).get('phase', '')}"
        time.sleep(1.0)
    raise TimeoutError(f"timed out waiting for ready pod; last state={last_state}")


def restart_function_pod(args: argparse.Namespace) -> tuple[str, str, float]:
    started = time.perf_counter()
    pods = list_function_pods(args)
    for pod in pods:
        name = pod.get("metadata", {}).get("name")
        if not name:
            continue
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
        if not name:
            continue
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


def request_json(url: str, payload: dict[str, Any] | None, timeout: float) -> tuple[int, float, bytes, str]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=body,
        method="POST" if payload is not None else "GET",
        headers={"Content-Type": "application/json"} if payload is not None else {},
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_body = response.read()
            return response.status, (time.perf_counter() - started) * 1000.0, response_body, ""
    except urllib.error.HTTPError as exc:
        return exc.code, (time.perf_counter() - started) * 1000.0, exc.read(), str(exc)
    except Exception as exc:  # noqa: BLE001 - measurements record transport failures.
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


def flatten_row(
    args: argparse.Namespace,
    invocation: int,
    pod: str,
    pod_uid: str,
    restart_ms: float,
    status: int,
    http_latency_ms: float,
    body: bytes,
    error: str,
    push_status: int,
    push_http_ms: float,
    push_body: bytes,
    push_error: str,
) -> dict[str, Any]:
    payload = parse_body(body)
    push_payload = parse_body(push_body)
    result = payload.get("result") or {}
    config = payload.get("config") or {}
    import_meta = payload.get("import") or config.get("import") or {}
    export_meta = push_payload.get("export") or {}
    merged_error = error or payload.get("error", "")
    if push_status < 200 or push_status >= 400:
        merged_error = merged_error or push_error or push_payload.get("error", "")

    return {
        "label": args.label,
        "signature": args.signature,
        "invocation": invocation,
        "pod": pod,
        "pod_uid": payload.get("pod_uid") or pod_uid,
        "restart_ms": f"{restart_ms:.6f}",
        "http_latency_ms": f"{http_latency_ms:.6f}",
        "status": status,
        "response_bytes": len(body),
        "error": merged_error,
        "mode": payload.get("mode", ""),
        "build": payload.get("build", ""),
        "handler_ms": f"{as_float(payload.get('handler_ms')):.6f}",
        "process_uptime_ms": f"{as_float(payload.get('process_uptime_ms')):.6f}",
        "compile_or_load_ms": f"{as_float(result.get('compile_or_load_ms')):.6f}",
        "execute_ms_median": f"{as_float(result.get('execute_ms_median')):.6f}",
        "cache_files": int(payload.get("cache_files") or result.get("cache_files") or 0),
        "cache_bytes": int(payload.get("cache_bytes") or result.get("cache_bytes") or 0),
        "import_ms": f"{as_float(import_meta.get('import_ms')):.6f}",
        "cache_imported": str(bool(import_meta.get("imported"))).lower(),
        "artifact_found": str(bool(import_meta.get("artifact_found"))).lower(),
        "artifact_bytes": int(import_meta.get("artifact_bytes") or 0),
        "push_http_ms": f"{push_http_ms:.6f}",
        "export_ms": f"{as_float(export_meta.get('export_ms')):.6f}",
        "export_artifact_bytes": int(export_meta.get("artifact_bytes") or 0),
        "redis_key": payload.get("redis_key") or import_meta.get("redis_key") or "",
        "checksum": f"{as_float(result.get('checksum')):.6f}",
    }


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = int((p / 100.0) * len(ordered))
    return ordered[min(index, len(ordered) - 1)]


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
    parser.add_argument("--label", default="progressive-cache")
    parser.add_argument("--invocations", type=int, default=10)
    parser.add_argument("--executions", type=int, default=3)
    parser.add_argument("--grace-period", type=int, default=5)
    parser.add_argument("--delete-timeout", type=int, default=120)
    parser.add_argument("--ready-timeout", type=int, default=240)
    parser.add_argument("--invoke-timeout", type=float, default=180.0)
    parser.add_argument("--csv", required=True, type=Path)
    parser.add_argument("--summary", required=True, type=Path)
    args = parser.parse_args()

    base_url = f"{args.gateway.rstrip('/')}/function/{args.function}"
    rows = []
    for invocation in range(1, args.invocations + 1):
        pod, pod_uid, restart_ms = restart_function_pod(args)
        status, http_latency_ms, body, error = request_json(
            f"{base_url}/work",
            {
                "signature": args.signature,
                "executions": args.executions,
                "iteration": invocation,
                "label": args.label,
            },
            args.invoke_timeout,
        )
        push_status, push_http_ms, push_body, push_error = request_json(
            f"{base_url}/cache/push",
            None,
            args.invoke_timeout,
        )
        row = flatten_row(
            args,
            invocation,
            pod,
            pod_uid,
            restart_ms,
            status,
            http_latency_ms,
            body,
            error,
            push_status,
            push_http_ms,
            push_body,
            push_error,
        )
        rows.append(row)
        print(
            json.dumps(
                {
                    "invocation": invocation,
                    "status": status,
                    "cache_imported": row["cache_imported"],
                    "artifact_found": row["artifact_found"],
                    "http_latency_ms": row["http_latency_ms"],
                    "compile_or_load_ms": row["compile_or_load_ms"],
                    "import_ms": row["import_ms"],
                    "export_artifact_bytes": row["export_artifact_bytes"],
                    "error": row["error"],
                },
                sort_keys=True,
            ),
            flush=True,
        )

    write_csv(args.csv, rows)
    summary = {
        "label": args.label,
        "signature": args.signature,
        "invocations": len(rows),
        "http_latency_ms": stats([float(row["http_latency_ms"]) for row in rows]),
        "compile_or_load_ms": stats([float(row["compile_or_load_ms"]) for row in rows]),
        "import_ms": stats([float(row["import_ms"]) for row in rows]),
        "artifact_found_by_invocation": [row["artifact_found"] == "true" for row in rows],
    }
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
