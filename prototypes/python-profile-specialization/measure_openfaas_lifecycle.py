#!/usr/bin/env python3
"""Measure cold/warm/hot OpenFaaS pod lifecycle requests."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


CSV_FIELDS = [
    "label",
    "benchmark",
    "global_invocation",
    "pod",
    "pod_uid",
    "request_in_pod",
    "phase",
    "latency_ms",
    "work_ms",
    "cold_start_ms",
    "restart_ms",
    "status",
    "response_bytes",
    "error",
    "mode",
    "build",
    "used_artifact",
    "cache_imported",
    "artifact_found",
    "import_ms",
    "redis_key",
    "checksum",
]


def run(cmd: list[str], timeout: float = 120.0, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout, check=False)
    if check and result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(cmd)}\nstdout={result.stdout}\nstderr={result.stderr}"
        )
    return result


def kubectl_json(args: argparse.Namespace, extra: list[str], timeout: float = 30.0) -> dict[str, Any]:
    return json.loads(run(["kubectl", *extra, "-o", "json"], timeout=timeout).stdout)


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
        if name:
            run(["kubectl", "delete", "pod", "-n", args.namespace, name, f"--grace-period={args.grace_period}"], check=False)
    for pod in pods:
        name = pod.get("metadata", {}).get("name")
        if name:
            run(
                ["kubectl", "wait", "--for=delete", f"pod/{name}", "-n", args.namespace, f"--timeout={args.delete_timeout}s"],
                timeout=args.delete_timeout + 10,
                check=False,
            )
    return wait_ready_pod(args, started)


def request_json(
    url: str,
    payload: dict[str, Any],
    timeout: float,
    username: str,
    password: str,
) -> tuple[int, float, bytes, str]:
    headers = {"Content-Type": "application/json"}
    if username and password:
        import base64

        token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {token}"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers=headers,
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
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


def phase_for(request_in_pod: int, warmup_requests: int) -> str:
    if request_in_pod == 1:
        return "cold"
    if request_in_pod <= warmup_requests:
        return "warmup"
    return "hot"


def row_from_response(
    args: argparse.Namespace,
    global_invocation: int,
    pod_number: int,
    pod: str,
    pod_uid: str,
    request_in_pod: int,
    restart_ms: float,
    status: int,
    latency_ms: float,
    body: bytes,
    error: str,
) -> dict[str, Any]:
    payload = parse_body(body)
    result = payload.get("result") or {}
    import_meta = payload.get("import") or {}
    return {
        "label": args.label,
        "benchmark": args.benchmark,
        "global_invocation": global_invocation,
        "pod": pod_number,
        "pod_uid": payload.get("pod_uid") or pod_uid,
        "request_in_pod": request_in_pod,
        "phase": phase_for(request_in_pod, args.warmup_requests),
        "latency_ms": f"{latency_ms:.6f}",
        "work_ms": f"{as_float(payload.get('handler_ms') or result.get('work_ms')):.6f}",
        "cold_start_ms": f"{restart_ms if request_in_pod == 1 else 0.0:.6f}",
        "restart_ms": f"{restart_ms:.6f}",
        "status": status,
        "response_bytes": len(body),
        "error": error or payload.get("error", ""),
        "mode": payload.get("mode", ""),
        "build": payload.get("build", ""),
        "used_artifact": str(bool(result.get("used_artifact"))).lower(),
        "cache_imported": str(bool(import_meta.get("imported"))).lower(),
        "artifact_found": str(bool(import_meta.get("artifact_found"))).lower(),
        "import_ms": f"{as_float(import_meta.get('import_ms')):.6f}",
        "redis_key": payload.get("redis_key") or import_meta.get("redis_key") or "",
        "checksum": result.get("checksum", ""),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--function", default="python-profile-specialization")
    parser.add_argument("--namespace", default="openfaas-fn")
    parser.add_argument("--gateway", default="http://127.0.0.1:8080")
    parser.add_argument("--benchmark", default="dacapo-lusearch")
    parser.add_argument("--requests", type=int, default=12000)
    parser.add_argument("--pods", type=int, default=3)
    parser.add_argument("--requests-per-pod", type=int, default=10)
    parser.add_argument("--warmup-requests", type=int, default=4)
    parser.add_argument("--label", required=True)
    parser.add_argument("--username", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--grace-period", type=int, default=5)
    parser.add_argument("--delete-timeout", type=int, default=120)
    parser.add_argument("--ready-timeout", type=int, default=240)
    parser.add_argument("--invoke-timeout", type=float, default=180.0)
    parser.add_argument("--csv", required=True, type=Path)
    args = parser.parse_args()

    rows = []
    global_invocation = 0
    url = f"{args.gateway.rstrip('/')}/function/{args.function}/work"
    for pod_number in range(1, args.pods + 1):
        pod, pod_uid, restart_ms = restart_function_pod(args)
        for request_in_pod in range(1, args.requests_per_pod + 1):
            global_invocation += 1
            status, latency_ms, body, error = request_json(
                url,
                {
                    "benchmark": args.benchmark,
                    "requests": args.requests,
                    "iteration": global_invocation,
                },
                args.invoke_timeout,
                args.username,
                args.password,
            )
            row = row_from_response(
                args,
                global_invocation,
                pod_number,
                pod,
                pod_uid,
                request_in_pod,
                restart_ms,
                status,
                latency_ms,
                body,
                error,
            )
            rows.append(row)
            print(
                json.dumps(
                    {
                        "label": args.label,
                        "benchmark": args.benchmark,
                        "global_invocation": global_invocation,
                        "pod": pod_number,
                        "request_in_pod": request_in_pod,
                        "phase": row["phase"],
                        "status": status,
                        "latency_ms": row["latency_ms"],
                        "work_ms": row["work_ms"],
                        "cache_imported": row["cache_imported"],
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
