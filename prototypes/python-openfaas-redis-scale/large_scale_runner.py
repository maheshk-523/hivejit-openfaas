#!/usr/bin/env python3
"""Large-scale OpenFaaS/Redis verifier for Python specialization artifacts."""

from __future__ import annotations

import argparse
import base64
import csv
import json
import re
import statistics
import subprocess
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_LABEL = "python-openfaas-redis-scale"
TREATMENTS = ("baseline", "saved")
BENCH_SHORT = {
    "dacapo-lusearch": "lusearch",
    "dacapo-h2": "h2",
    "dacapo-eclipse": "eclipse",
    "dacapo-jython": "jython",
    "dacapo-fop": "fop",
}

CSV_FIELDS = [
    "run_id",
    "benchmark",
    "treatment",
    "wave",
    "shard",
    "function",
    "pod_name",
    "pod_uid",
    "request_in_pod",
    "phase",
    "seed",
    "status",
    "ok",
    "latency_ms",
    "work_ms",
    "restart_ms",
    "response_bytes",
    "error",
    "mode",
    "build",
    "used_artifact",
    "cache_imported",
    "artifact_found",
    "import_ms",
    "artifact_bytes",
    "artifact_hash",
    "redis_key",
    "checksum",
]


@dataclass(frozen=True)
class FunctionTarget:
    name: str
    benchmark: str
    treatment: str
    shard: int


def run(cmd: list[str], timeout: float = 120.0, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout, check=False)
    if check and result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(cmd)}\nstdout={result.stdout}\nstderr={result.stderr}"
        )
    return result


def kubectl(args: list[str], timeout: float = 120.0, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run(["kubectl", *args], timeout=timeout, check=check)


def kubectl_json(args: list[str], timeout: float = 60.0) -> dict[str, Any]:
    return json.loads(kubectl([*args, "-o", "json"], timeout=timeout).stdout)


def slugify(raw: str) -> str:
    slug = re.sub(r"[^a-z0-9.-]+", "-", raw.lower()).strip("-.")
    return slug or "x"


def dns_name(raw: str) -> str:
    name = re.sub(r"[^a-z0-9-]+", "-", raw.lower()).strip("-")
    name = re.sub(r"-+", "-", name)
    if not name:
        name = "x"
    if len(name) > 63:
        name = name[:63].rstrip("-")
    return name


def label_value(raw: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "-", raw).strip("_.-")
    if len(value) > 63:
        value = value[:63].strip("_.-")
    if not value:
        value = "x"
    if not value[0].isalnum():
        value = f"x{value}"
    if not value[-1].isalnum():
        value = f"{value}x"
    return value


def bench_short(benchmark: str) -> str:
    return BENCH_SHORT.get(benchmark, slugify(benchmark.removeprefix("dacapo-")))


def function_name(args: argparse.Namespace, benchmark: str, treatment: str, shard: int | None = None) -> str:
    short = bench_short(benchmark)
    suffix = f"{short}-{treatment}" if shard is None else f"{short}-{treatment}-{shard:03d}"
    prefix = dns_name(args.function_prefix)
    name = dns_name(f"{prefix}-{suffix}")
    if len(name) <= 63:
        return name
    keep = max(1, 63 - len(suffix) - 1)
    return dns_name(f"{prefix[:keep].rstrip('-')}-{suffix}")


def artifact_key(args: argparse.Namespace, benchmark: str) -> str:
    return f"{args.artifact_prefix}:{args.run_id}:{bench_short(benchmark)}:artifact:v1"


def common_labels(args: argparse.Namespace, benchmark: str, treatment: str, shard: int | None, name: str) -> dict[str, str]:
    return {
        "faas_function": name,
        "profile-scale-project": PROJECT_LABEL,
        "profile-scale-run": args.run_label,
        "profile-scale-benchmark": label_value(bench_short(benchmark)),
        "profile-scale-treatment": label_value(treatment),
        "profile-scale-shard": label_value("populate" if shard is None else f"{shard:03d}"),
    }


def env(name: str, value: str | int) -> dict[str, str]:
    return {"name": name, "value": str(value)}


def function_items(
    args: argparse.Namespace,
    benchmark: str,
    treatment: str,
    shard: int | None,
    mode: str,
    require_artifact: bool,
) -> list[dict[str, Any]]:
    name = function_name(args, benchmark, treatment, shard)
    labels = common_labels(args, benchmark, treatment, shard, name)
    build = f"{args.run_id}-{bench_short(benchmark)}-{treatment}"
    container: dict[str, Any] = {
        "name": name,
        "image": args.image,
        "imagePullPolicy": args.image_pull_policy,
        "ports": [{"name": "http", "containerPort": 8080}],
        "env": [
            {"name": "POD_UID", "valueFrom": {"fieldRef": {"fieldPath": "metadata.uid"}}},
            env("BUILD_LABEL", build),
            env("BENCHMARK", benchmark),
            env("REQUESTS", args.work_requests),
            env("PROFILE_REQUESTS", args.profile_requests),
            env("PROFILE_ITERS", args.profile_iters),
            env("PY_SPEC_MODE", mode),
            env("PY_SPEC_ARTIFACT_KEY", artifact_key(args, benchmark)),
            env("PY_SPEC_ARTIFACT_PATH", "/profiles/specialized.py"),
            env("PY_SPEC_IMPORT_META", "/profiles/python-specialization-import.json"),
            env("PY_SPEC_REQUIRE_ARTIFACT", "1" if require_artifact else "0"),
            env("REDIS_ADDR", args.redis_addr),
            env("REDIS_PASSWORD", args.redis_password),
            env("REDIS_DB", args.redis_db),
            env("REDIS_TIMEOUT", args.redis_timeout),
            env("read_timeout", args.watchdog_timeout),
            env("write_timeout", args.watchdog_timeout),
            env("exec_timeout", args.watchdog_timeout),
        ],
        "readinessProbe": {
            "httpGet": {"path": "/_/health", "port": 8080},
            "initialDelaySeconds": 2,
            "periodSeconds": 3,
            "timeoutSeconds": 2,
        },
        "livenessProbe": {
            "httpGet": {"path": "/_/health", "port": 8080},
            "initialDelaySeconds": 2,
            "periodSeconds": 5,
            "timeoutSeconds": 2,
        },
        "volumeMounts": [{"name": "python-profile-artifact", "mountPath": "/profiles"}],
    }
    resources: dict[str, Any] = {}
    requests: dict[str, str] = {}
    if args.cpu_request:
        requests["cpu"] = args.cpu_request
    if args.memory_request:
        requests["memory"] = args.memory_request
    if requests:
        resources["requests"] = requests
    if resources:
        container["resources"] = resources

    deployment = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": name,
            "namespace": args.namespace,
            "labels": labels,
        },
        "spec": {
            "replicas": 1,
            "selector": {"matchLabels": {"faas_function": name}},
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "terminationGracePeriodSeconds": 20,
                    "volumes": [{"name": "python-profile-artifact", "emptyDir": {}}],
                    "containers": [container],
                },
            },
        },
    }
    service = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {
            "name": name,
            "namespace": args.namespace,
            "labels": labels,
        },
        "spec": {
            "selector": {"faas_function": name},
            "ports": [{"name": "http", "port": 8080, "targetPort": "http"}],
        },
    }
    return [deployment, service]


def write_manifest(path: Path, items: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"apiVersion": "v1", "kind": "List", "items": items}, indent=2) + "\n", encoding="utf-8")


def ready_condition(pod: dict[str, Any]) -> bool:
    if pod.get("metadata", {}).get("deletionTimestamp"):
        return False
    return any(
        item.get("type") == "Ready" and item.get("status") == "True"
        for item in pod.get("status", {}).get("conditions", [])
    )


def selector(args: argparse.Namespace, benchmark: str, treatment: str) -> str:
    return ",".join(
        [
            f"profile-scale-project={PROJECT_LABEL}",
            f"profile-scale-run={args.run_label}",
            f"profile-scale-benchmark={label_value(bench_short(benchmark))}",
            f"profile-scale-treatment={label_value(treatment)}",
        ]
    )


def wait_ready_functions(
    args: argparse.Namespace,
    selector_value: str,
    expected_names: set[str],
    timeout: float,
) -> dict[str, tuple[str, str]]:
    deadline = time.time() + timeout
    last = "no pods"
    while time.time() < deadline:
        data = kubectl_json(["get", "pod", "-n", args.namespace, "-l", selector_value], timeout=30)
        ready: dict[str, tuple[str, str]] = {}
        states = []
        for pod in data.get("items", []):
            metadata = pod.get("metadata", {})
            labels = metadata.get("labels", {})
            function = labels.get("faas_function", "")
            name = metadata.get("name", "")
            phase = pod.get("status", {}).get("phase", "")
            states.append(f"{function}:{phase}")
            if function in expected_names and ready_condition(pod):
                ready[function] = (name, metadata.get("uid", ""))
        if expected_names.issubset(ready):
            return ready
        if states:
            last = ", ".join(states[:8])
            if len(states) > 8:
                last += f", +{len(states) - 8} more"
        time.sleep(1.0)
    missing = ", ".join(sorted(expected_names - set(ready)))
    raise TimeoutError(f"timed out waiting for ready functions; missing={missing}; last={last}")


def apply_manifest(path: Path, timeout: float) -> None:
    print(f"applying {path}", flush=True)
    kubectl(["apply", "-f", str(path)], timeout=timeout)


def delete_manifest(path: Path, timeout: float) -> None:
    if path.exists():
        print(f"deleting {path}", flush=True)
        kubectl(["delete", "-f", str(path), "--ignore-not-found=true"], timeout=timeout, check=False)


def restart_pods(
    args: argparse.Namespace,
    benchmark: str,
    treatment: str,
    expected_names: set[str],
) -> tuple[dict[str, tuple[str, str]], float]:
    selector_value = selector(args, benchmark, treatment)
    started = time.perf_counter()
    kubectl(
        [
            "delete",
            "pod",
            "-n",
            args.namespace,
            "-l",
            selector_value,
            f"--grace-period={args.grace_period}",
            "--ignore-not-found=true",
        ],
        timeout=args.delete_timeout,
        check=False,
    )
    ready = wait_ready_functions(args, selector_value, expected_names, args.ready_timeout)
    return ready, (time.perf_counter() - started) * 1000.0


def gateway_url(args: argparse.Namespace, function: str, path: str) -> str:
    return f"{args.gateway.rstrip('/')}/function/{function}{path}"


def request_gateway(
    args: argparse.Namespace,
    function: str,
    path: str,
    method: str,
    payload: dict[str, Any] | None,
    timeout: float,
) -> tuple[int, float, bytes, str]:
    headers = {"Content-Type": "application/json"}
    if args.username and args.password:
        token = base64.b64encode(f"{args.username}:{args.password}".encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {token}"
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(gateway_url(args, function, path), data=data, method=method, headers=headers)
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


def write_response(path: Path, status: int, latency_ms: float, body: bytes, error: str) -> None:
    parsed = parse_body(body)
    payload = {
        "status": status,
        "latency_ms": latency_ms,
        "error": error,
        "response_bytes": len(body),
        "body": parsed if parsed else body.decode("utf-8", errors="replace"),
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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


def targets_for(args: argparse.Namespace, benchmark: str, treatment: str) -> list[FunctionTarget]:
    return [
        FunctionTarget(function_name(args, benchmark, treatment, shard), benchmark, treatment, shard)
        for shard in range(1, args.shards + 1)
    ]


def deploy_populate_functions(args: argparse.Namespace) -> Path:
    items: list[dict[str, Any]] = []
    expected: dict[str, set[str]] = {}
    for benchmark in args.benchmarks:
        items.extend(function_items(args, benchmark, "populate", None, "populate", False))
        expected[benchmark] = {function_name(args, benchmark, "populate")}
    manifest = args.manifest_dir / "python-scale-populate.json"
    write_manifest(manifest, items)
    apply_manifest(manifest, args.ready_timeout)
    for benchmark in args.benchmarks:
        wait_ready_functions(args, selector(args, benchmark, "populate"), expected[benchmark], args.ready_timeout)
    return manifest


def populate_artifacts(args: argparse.Namespace) -> None:
    for benchmark in args.benchmarks:
        function = function_name(args, benchmark, "populate")
        print(f"populating Redis artifact benchmark={benchmark} function={function}", flush=True)
        status, latency_ms, body, error = request_gateway(args, function, "/profile/ping", "GET", None, args.invoke_timeout)
        write_response(args.out_dir / f"redis-ping-{bench_short(benchmark)}.json", status, latency_ms, body, error)
        if status != 200:
            raise RuntimeError(f"Redis ping failed for {benchmark}: status={status} error={error} body={body[:500]!r}")

        payload = {
            "benchmark": benchmark,
            "profile_iters": args.profile_iters,
            "profile_requests": args.profile_requests,
        }
        status, latency_ms, body, error = request_gateway(
            args, function, "/profile/populate", "POST", payload, args.invoke_timeout
        )
        write_response(args.out_dir / f"populate-{bench_short(benchmark)}.json", status, latency_ms, body, error)
        if status != 200:
            raise RuntimeError(f"populate failed for {benchmark}: status={status} error={error} body={body[:500]!r}")


def deploy_matrix(args: argparse.Namespace) -> Path:
    items: list[dict[str, Any]] = []
    for benchmark in args.benchmarks:
        for treatment in TREATMENTS:
            mode = "saved" if treatment == "saved" else "baseline"
            for shard in range(1, args.shards + 1):
                items.extend(function_items(args, benchmark, treatment, shard, mode, treatment == "saved"))
    manifest = args.manifest_dir / "python-scale-matrix.json"
    write_manifest(manifest, items)
    apply_manifest(manifest, max(args.ready_timeout, 300))
    for benchmark in args.benchmarks:
        for treatment in TREATMENTS:
            expected = {target.name for target in targets_for(args, benchmark, treatment)}
            wait_ready_functions(args, selector(args, benchmark, treatment), expected, args.ready_timeout)
    return manifest


def row_from_response(
    args: argparse.Namespace,
    target: FunctionTarget,
    wave: int,
    request_in_pod: int,
    seed: int,
    pod_info: tuple[str, str],
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
        "run_id": args.run_id,
        "benchmark": target.benchmark,
        "treatment": target.treatment,
        "wave": wave,
        "shard": target.shard,
        "function": target.name,
        "pod_name": pod_info[0],
        "pod_uid": payload.get("pod_uid") or pod_info[1],
        "request_in_pod": request_in_pod,
        "phase": phase_for(request_in_pod, args.warmup_requests),
        "seed": seed,
        "status": status,
        "ok": str(bool(payload.get("ok"))).lower(),
        "latency_ms": f"{latency_ms:.6f}",
        "work_ms": f"{as_float(payload.get('handler_ms') or result.get('work_ms')):.6f}",
        "restart_ms": f"{restart_ms:.6f}",
        "response_bytes": len(body),
        "error": error or payload.get("error", ""),
        "mode": payload.get("mode", ""),
        "build": payload.get("build", ""),
        "used_artifact": str(bool(result.get("used_artifact"))).lower(),
        "cache_imported": str(bool(import_meta.get("imported"))).lower(),
        "artifact_found": str(bool(import_meta.get("artifact_found"))).lower(),
        "import_ms": f"{as_float(import_meta.get('import_ms')):.6f}",
        "artifact_bytes": import_meta.get("artifact_bytes", ""),
        "artifact_hash": import_meta.get("artifact_hash", ""),
        "redis_key": payload.get("redis_key") or import_meta.get("redis_key") or "",
        "checksum": result.get("checksum", ""),
    }


def invoke_target(
    args: argparse.Namespace,
    target: FunctionTarget,
    wave: int,
    request_in_pod: int,
    seed: int,
    pod_info: tuple[str, str],
    restart_ms: float,
) -> dict[str, Any]:
    payload = {
        "benchmark": target.benchmark,
        "requests": args.work_requests,
        "iteration": seed,
    }
    status, latency_ms, body, error = request_gateway(args, target.name, "/work", "POST", payload, args.invoke_timeout)
    return row_from_response(
        args,
        target,
        wave,
        request_in_pod,
        seed,
        pod_info,
        restart_ms,
        status,
        latency_ms,
        body,
        error,
    )


def measure_matrix(args: argparse.Namespace) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    csv_path = args.out_dir / "large-scale.csv"
    args.out_dir.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for benchmark in args.benchmarks:
            for treatment in TREATMENTS:
                targets = targets_for(args, benchmark, treatment)
                expected = {target.name for target in targets}
                print(
                    json.dumps(
                        {
                            "event": "measure-treatment",
                            "benchmark": benchmark,
                            "treatment": treatment,
                            "functions": len(targets),
                            "waves": args.waves,
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
                for wave in range(1, args.waves + 1):
                    ready, restart_ms = restart_pods(args, benchmark, treatment, expected)
                    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
                        for request_in_pod in range(1, args.requests_per_pod + 1):
                            futures = []
                            for target in targets:
                                seed = wave * 1_000_000 + target.shard * 1_000 + request_in_pod
                                futures.append(
                                    executor.submit(
                                        invoke_target,
                                        args,
                                        target,
                                        wave,
                                        request_in_pod,
                                        seed,
                                        ready.get(target.name, ("", "")),
                                        restart_ms,
                                    )
                                )
                            position_rows = []
                            for future in as_completed(futures):
                                row = future.result()
                                writer.writerow({field: row.get(field, "") for field in CSV_FIELDS})
                                rows.append(row)
                                position_rows.append(row)
                            f.flush()
                            ok_latencies = [
                                float(row["latency_ms"])
                                for row in position_rows
                                if int(row["status"]) == 200 and row["error"] == ""
                            ]
                            print(
                                json.dumps(
                                    {
                                        "benchmark": benchmark,
                                        "event": "request-position",
                                        "median_latency_ms": statistics.median(ok_latencies) if ok_latencies else None,
                                        "ok": len(ok_latencies),
                                        "request_in_pod": request_in_pod,
                                        "treatment": treatment,
                                        "wave": wave,
                                    },
                                    sort_keys=True,
                                ),
                                flush=True,
                            )
    return rows


def percent_saved(baseline: float, saved: float) -> float:
    if baseline <= 0:
        return 0.0
    return ((baseline - saved) / baseline) * 100.0


def summarize(rows: list[dict[str, Any]], args: argparse.Namespace) -> dict[str, Any]:
    values: dict[str, dict[str, dict[int, list[float]]]] = {}
    status_counts: dict[str, int] = {}
    validation: dict[str, Any] = {
        "saved_ok_rows": 0,
        "saved_rows_without_used_artifact": 0,
        "saved_rows_without_cache_import": 0,
        "saved_rows_without_artifact_found": 0,
        "baseline_ok_rows": 0,
        "baseline_rows_with_used_artifact": 0,
        "checksum_pairs": 0,
        "checksum_mismatches": 0,
        "checksum_mismatch_examples": [],
    }
    checksums: dict[tuple[str, int, int, int], dict[str, str]] = {}
    for row in rows:
        key = f"{row['benchmark']}:{row['treatment']}:{row['status']}"
        status_counts[key] = status_counts.get(key, 0) + 1
        if int(row["status"]) != 200 or row["error"]:
            continue
        benchmark = str(row["benchmark"])
        treatment = str(row["treatment"])
        request = int(row["request_in_pod"])
        values.setdefault(benchmark, {}).setdefault(treatment, {}).setdefault(request, []).append(float(row["latency_ms"]))
        if treatment == "saved":
            validation["saved_ok_rows"] += 1
            if str(row.get("used_artifact", "")).lower() != "true":
                validation["saved_rows_without_used_artifact"] += 1
            if str(row.get("cache_imported", "")).lower() != "true":
                validation["saved_rows_without_cache_import"] += 1
            if str(row.get("artifact_found", "")).lower() != "true":
                validation["saved_rows_without_artifact_found"] += 1
        elif treatment == "baseline":
            validation["baseline_ok_rows"] += 1
            if str(row.get("used_artifact", "")).lower() == "true":
                validation["baseline_rows_with_used_artifact"] += 1
        checksum = str(row.get("checksum", ""))
        if checksum:
            checksum_key = (benchmark, int(row["wave"]), int(row["shard"]), request)
            checksums.setdefault(checksum_key, {})[treatment] = checksum

    for checksum_key, by_treatment in checksums.items():
        if "baseline" not in by_treatment or "saved" not in by_treatment:
            continue
        validation["checksum_pairs"] += 1
        if by_treatment["baseline"] != by_treatment["saved"]:
            validation["checksum_mismatches"] += 1
            examples = validation["checksum_mismatch_examples"]
            if len(examples) < 10:
                benchmark, wave, shard, request = checksum_key
                examples.append(
                    {
                        "benchmark": benchmark,
                        "wave": wave,
                        "shard": shard,
                        "request_in_pod": request,
                        "baseline_checksum": by_treatment["baseline"],
                        "saved_checksum": by_treatment["saved"],
                    }
                )

    benchmarks: dict[str, Any] = {}
    for benchmark in args.benchmarks:
        by_request = []
        baseline = {
            request: statistics.median(samples)
            for request, samples in values.get(benchmark, {}).get("baseline", {}).items()
            if samples
        }
        saved = {
            request: statistics.median(samples)
            for request, samples in values.get(benchmark, {}).get("saved", {}).items()
            if samples
        }
        positions = sorted(set(baseline) & set(saved))
        wins = 0
        for request in positions:
            saved_pct = percent_saved(baseline[request], saved[request])
            if saved[request] < baseline[request]:
                wins += 1
            by_request.append(
                {
                    "request_in_pod": request,
                    "baseline_median_ms": baseline[request],
                    "saved_median_ms": saved[request],
                    "saved_pct": saved_pct,
                    "baseline_samples": len(values[benchmark]["baseline"][request]),
                    "saved_samples": len(values[benchmark]["saved"][request]),
                }
            )
        cold_saved_pct = percent_saved(baseline.get(1, 0.0), saved.get(1, 0.0)) if 1 in positions else 0.0
        hot_positions = [request for request in positions if request > args.warmup_requests]
        if hot_positions:
            base_hot = statistics.median(baseline[request] for request in hot_positions)
            saved_hot = statistics.median(saved[request] for request in hot_positions)
            hot_saved_pct = percent_saved(base_hot, saved_hot)
        else:
            hot_saved_pct = 0.0
        benchmarks[benchmark] = {
            "median_position_wins": wins,
            "positions": len(positions),
            "cold_saved_pct": cold_saved_pct,
            "hot_saved_pct": hot_saved_pct,
            "samples_per_request": args.shards * args.waves,
            "by_request": by_request,
        }

    return {
        "schema": "python-openfaas-redis-scale-summary.v1",
        "run_id": args.run_id,
        "function_prefix": args.function_prefix,
        "benchmarks": benchmarks,
        "settings": {
            "shards": args.shards,
            "waves": args.waves,
            "requests_per_pod": args.requests_per_pod,
            "warmup_requests": args.warmup_requests,
            "work_requests": args.work_requests,
            "profile_requests": args.profile_requests,
            "profile_iters": args.profile_iters,
        },
        "status_counts": status_counts,
        "validation": validation,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--image-pull-policy", default="IfNotPresent")
    parser.add_argument("--function-prefix", default="py-spec-scale")
    parser.add_argument("--namespace", default="openfaas-fn")
    parser.add_argument("--gateway", default="http://127.0.0.1:8080")
    parser.add_argument(
        "--benchmarks",
        nargs="+",
        default=["dacapo-lusearch", "dacapo-h2", "dacapo-eclipse", "dacapo-jython", "dacapo-fop"],
    )
    parser.add_argument("--shards", type=int, default=24)
    parser.add_argument("--waves", type=int, default=4)
    parser.add_argument("--requests-per-pod", type=int, default=8)
    parser.add_argument("--warmup-requests", type=int, default=3)
    parser.add_argument("--work-requests", type=int, default=12000)
    parser.add_argument("--profile-requests", type=int, default=36000)
    parser.add_argument("--profile-iters", type=int, default=3)
    parser.add_argument("--concurrency", type=int, default=24)
    parser.add_argument("--manifest-dir", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--artifact-prefix", default="python-profile-scale")
    parser.add_argument("--redis-addr", default="profile-cache-redis.openfaas-fn.svc.cluster.local:6379")
    parser.add_argument("--redis-password", default="")
    parser.add_argument("--redis-db", default="0")
    parser.add_argument("--redis-timeout", default="10s")
    parser.add_argument("--watchdog-timeout", default="300s")
    parser.add_argument("--invoke-timeout", type=float, default=180.0)
    parser.add_argument("--ready-timeout", type=float, default=300.0)
    parser.add_argument("--delete-timeout", type=float, default=180.0)
    parser.add_argument("--grace-period", type=int, default=5)
    parser.add_argument("--username", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--cpu-request", default="")
    parser.add_argument("--memory-request", default="")
    parser.add_argument("--skip-populate", action="store_true")
    parser.add_argument("--skip-deploy", action="store_true")
    parser.add_argument("--cleanup-at-end", action="store_true")
    args = parser.parse_args()
    args.run_label = label_value(args.run_id)
    if args.shards <= 0:
        raise SystemExit("--shards must be positive")
    if args.waves <= 0:
        raise SystemExit("--waves must be positive")
    if args.requests_per_pod <= 0:
        raise SystemExit("--requests-per-pod must be positive")
    if args.concurrency <= 0:
        raise SystemExit("--concurrency must be positive")
    args.manifest_dir.mkdir(parents=True, exist_ok=True)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    return args


def main() -> int:
    args = parse_args()
    populate_manifest = args.manifest_dir / "python-scale-populate.json"
    matrix_manifest = args.manifest_dir / "python-scale-matrix.json"

    if not args.skip_populate:
        populate_manifest = deploy_populate_functions(args)
        populate_artifacts(args)
    else:
        print("skipping Redis artifact population", flush=True)

    if not args.skip_deploy:
        matrix_manifest = deploy_matrix(args)
    else:
        print("skipping matrix deployment", flush=True)

    rows = measure_matrix(args)
    summary = summarize(rows, args)
    summary_path = args.out_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"event": "summary", "path": str(summary_path)}, sort_keys=True), flush=True)

    if args.cleanup_at_end:
        delete_manifest(matrix_manifest, args.delete_timeout)
        delete_manifest(populate_manifest, args.delete_timeout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
