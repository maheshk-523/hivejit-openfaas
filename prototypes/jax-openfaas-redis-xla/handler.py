#!/usr/bin/env python3
"""OpenFaaS HTTP handler for JAX/XLA Redis cache cold-start experiments."""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import cachectl
import jax_workload


STARTED_AT = time.time()
CONFIG_LOCK = threading.Lock()
CONFIGURED = False
INVOCATION = 0
CACHE_DIR_FOR_JAX: Path | None = None


def env_default(name: str, default: str) -> str:
    return os.getenv(name, default)


def cache_mode() -> str:
    return env_default("JAX_CACHE_MODE", "baseline").lower()


def build_label() -> str:
    return env_default("BUILD_LABEL", cache_mode())


def pod_uid() -> str:
    return env_default("POD_UID", "unknown")


def hostname() -> str:
    return socket.gethostname()


def cache_enabled_for_mode(mode: str) -> bool:
    return mode in {"populate", "redis"}


def ensure_jax_configured() -> dict[str, Any]:
    global CACHE_DIR_FOR_JAX, CONFIGURED
    with CONFIG_LOCK:
        if CONFIGURED:
            return {
                "configured": True,
                "cache_enabled": CACHE_DIR_FOR_JAX is not None,
                "cache_dir": str(CACHE_DIR_FOR_JAX) if CACHE_DIR_FOR_JAX else "",
                "import": cachectl.read_json(cachectl.import_meta_path()),
            }

        mode = cache_mode()
        import_meta: dict[str, Any] = cachectl.read_json(cachectl.import_meta_path())
        if mode == "redis" and not import_meta:
            import_meta = cachectl.pull_cache_from_redis()

        CACHE_DIR_FOR_JAX = cachectl.cache_dir() if cache_enabled_for_mode(mode) else None
        jax_workload.configure_jax(CACHE_DIR_FOR_JAX)
        CONFIGURED = True
        return {
            "configured": True,
            "cache_enabled": CACHE_DIR_FOR_JAX is not None,
            "cache_dir": str(CACHE_DIR_FOR_JAX) if CACHE_DIR_FOR_JAX else "",
            "import": import_meta,
        }


def request_payload(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    parsed = urlparse(handler.path)
    params = {key: values[-1] for key, values in parse_qs(parsed.query).items()}
    if handler.command == "POST":
        length = int(handler.headers.get("Content-Length") or "0")
        if length > 0:
            raw = handler.rfile.read(min(length, 1 << 20))
            if raw:
                try:
                    body = json.loads(raw.decode("utf-8"))
                    if isinstance(body, dict):
                        params.update(body)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"invalid JSON request body: {exc}") from exc
    return params


def int_param(params: dict[str, Any], name: str, default: int) -> int:
    value = params.get(name, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def schedule_param(params: dict[str, Any]) -> list[int]:
    raw = params.get("variant_schedule") or params.get("schedule") or os.getenv("VARIANT_SCHEDULE", "")
    if isinstance(raw, list):
        values = raw
    else:
        values = str(raw).replace(";", ",").replace(" ", ",").split(",")
    schedule: list[int] = []
    for value in values:
        if value == "":
            continue
        try:
            schedule.append(max(0, int(value)))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid variant schedule item: {value!r}") from exc
    return schedule


def signature_list(params: dict[str, Any]) -> list[str]:
    raw = params.get("signatures") or params.get("signature") or os.getenv("SIGNATURES") or os.getenv("SIGNATURE")
    if not raw:
        raw = "dacapo-lusearch"
    if isinstance(raw, list):
        values = raw
    else:
        values = str(raw).replace(",", " ").split()
    signatures = [value.strip() for value in values if value.strip()]
    for signature in signatures:
        if signature not in jax_workload.SIGNATURES:
            raise ValueError(f"unknown signature: {signature}")
    return signatures


def compile_one(signature_name: str, label: str, iteration: int, executions: int) -> dict[str, Any]:
    spec = dict(jax_workload.SIGNATURES[signature_name])
    spec["name"] = signature_name
    return jax_workload.compile_signature(
        label=label,
        signature=spec,
        iteration=iteration,
        cache_dir=CACHE_DIR_FOR_JAX,
        executions=executions,
    )


def variant_spec(signature_name: str, variant_index: int) -> dict[str, Any]:
    spec = dict(jax_workload.SIGNATURES[signature_name])
    spec["xShape"] = list(spec["xShape"])
    spec["wShape"] = list(spec["wShape"])
    spec["biasShape"] = list(spec["biasShape"])
    spec["staticArgs"] = dict(spec["staticArgs"])
    if variant_index > 0:
        spec["name"] = f"{signature_name}-variant-{variant_index + 1}"
        spec["xShape"][0] += variant_index * 8
        spec["staticArgs"]["depth"] = int(spec["staticArgs"]["depth"]) + (variant_index % 3)
    else:
        spec["name"] = signature_name
    return spec


def aggregate_rows(signature_name: str, label: str, iteration: int, rows: list[dict[str, Any]]) -> dict[str, Any]:
    if len(rows) == 1:
        row = dict(rows[0])
        row["variant_rows"] = rows
        row["compile_variants"] = 1
        return row

    return {
        "label": label,
        "signature": signature_name,
        "signature_hash": "aggregate",
        "iteration": iteration,
        "compile_or_load_ms": sum(float(row["compile_or_load_ms"]) for row in rows),
        "execute_ms_median": sum(float(row["execute_ms_median"]) for row in rows),
        "execute_ms_min": sum(float(row["execute_ms_min"]) for row in rows),
        "execute_ms_max": sum(float(row["execute_ms_max"]) for row in rows),
        "checksum": sum(float(row["checksum"]) for row in rows),
        "cache_enabled": any(bool(row["cache_enabled"]) for row in rows),
        "cache_files": max(int(row["cache_files"]) for row in rows),
        "cache_bytes": max(int(row["cache_bytes"]) for row in rows),
        "compile_variants": len(rows),
        "variant_rows": rows,
    }


def compile_variant_indices(
    signature_name: str,
    label: str,
    iteration: int,
    executions: int,
    variant_indices: list[int],
) -> dict[str, Any]:
    rows = []
    for variant_index in variant_indices:
        rows.append(
            jax_workload.compile_signature(
                label=label,
                signature=variant_spec(signature_name, variant_index),
                iteration=iteration,
                cache_dir=CACHE_DIR_FOR_JAX,
                executions=executions,
            )
        )
    return aggregate_rows(signature_name, label, iteration, rows)


def scheduled_variant_indices(schedule: list[int], request_in_pod: int) -> list[int]:
    if not schedule:
        return []
    position_index = max(0, request_in_pod - 1)
    if position_index >= len(schedule):
        return [0]
    count = schedule[position_index]
    if count <= 0:
        return [0]
    start = sum(schedule[:position_index])
    return list(range(start, start + count))


def compile_variants(
    signature_name: str,
    label: str,
    iteration: int,
    executions: int,
    variants: int,
) -> dict[str, Any]:
    rows = []
    for variant_index in range(max(1, variants)):
        rows.append(
            jax_workload.compile_signature(
                label=label,
                signature=variant_spec(signature_name, variant_index),
                iteration=iteration,
                cache_dir=CACHE_DIR_FOR_JAX,
                executions=executions,
            )
        )
    return aggregate_rows(signature_name, label, iteration, rows)


def cache_snapshot() -> dict[str, Any]:
    files, bytes_on_disk = cachectl.directory_stats(cachectl.cache_dir())
    return {
        "cache_dir": str(cachectl.cache_dir()),
        "cache_files": files,
        "cache_bytes": bytes_on_disk,
        "import": cachectl.read_json(cachectl.import_meta_path()),
        "export": cachectl.read_json(cachectl.export_meta_path()),
    }


def common_metadata() -> dict[str, Any]:
    return {
        "mode": cache_mode(),
        "build": build_label(),
        "redis_key": cachectl.cache_key(),
        "pod_uid": pod_uid(),
        "hostname": hostname(),
        "process_uptime_ms": (time.time() - STARTED_AT) * 1000.0,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "jax-openfaas-redis-xla/1.0"

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API.
        self.dispatch()

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API.
        self.dispatch()

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} - {fmt % args}", flush=True)

    def dispatch(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path in {"/healthz", "/_/health"}:
                self.write_json(HTTPStatus.OK, {"ok": True, **common_metadata()})
            elif parsed.path in {"/", "/work"}:
                self.handle_work()
            elif parsed.path == "/profile":
                self.handle_profile()
            elif parsed.path == "/cache/ping":
                self.handle_cache_ping()
            elif parsed.path == "/cache/metadata":
                self.write_json(HTTPStatus.OK, {"ok": True, **common_metadata(), **cache_snapshot()})
            elif parsed.path == "/cache/populate":
                self.handle_cache_populate()
            elif parsed.path == "/cache/push":
                self.handle_cache_push()
            else:
                self.write_json(HTTPStatus.NOT_FOUND, {"error": "not found", "path": parsed.path})
        except ValueError as exc:
            self.write_json(HTTPStatus.BAD_REQUEST, {"error": str(exc), **common_metadata()})
        except Exception as exc:  # noqa: BLE001 - benchmark surface records failures as JSON.
            self.write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc), **common_metadata()})

    def handle_profile(self) -> None:
        params = request_payload(self)
        signatures = signature_list(params)
        self.write_json(HTTPStatus.OK, jax_workload.profile_for_signatures(signatures))

    def handle_cache_ping(self) -> None:
        reply = cachectl.RedisClient().ping()
        self.write_json(HTTPStatus.OK, {"ok": True, "reply": reply, **common_metadata()})

    def handle_cache_push(self) -> None:
        export_meta = cachectl.push_cache_to_redis()
        self.write_json(HTTPStatus.OK, {"ok": True, **common_metadata(), "export": export_meta})

    def handle_cache_populate(self) -> None:
        global INVOCATION
        params = request_payload(self)
        signatures = signature_list(params)
        executions = int_param(params, "executions", int(env_default("EXECUTIONS", "3")))
        iterations = max(1, int_param(params, "iterations", 1))
        variants = max(1, int_param(params, "compile_variants", int(env_default("COMPILE_VARIANTS", "1"))))
        schedule = schedule_param(params)
        scheduled_variants = sum(schedule)

        started = time.perf_counter()
        config_meta = ensure_jax_configured()
        rows = []
        for iteration in range(1, iterations + 1):
            for signature in signatures:
                INVOCATION += 1
                if scheduled_variants > 0:
                    rows.append(
                        compile_variant_indices(
                            signature_name=signature,
                            label=params.get("label", "jax-openfaas-populate"),
                            iteration=iteration,
                            executions=executions,
                            variant_indices=list(range(scheduled_variants)),
                        )
                    )
                else:
                    rows.append(
                        compile_variants(
                            signature_name=signature,
                            label=params.get("label", "jax-openfaas-populate"),
                            iteration=iteration,
                            executions=executions,
                            variants=variants,
                        )
                    )
        export_meta = cachectl.push_cache_to_redis()
        self.write_json(
            HTTPStatus.OK,
            {
                "ok": True,
                **common_metadata(),
                "handler_ms": (time.perf_counter() - started) * 1000.0,
                "config": config_meta,
                "rows": rows,
                "export": export_meta,
            },
        )

    def handle_work(self) -> None:
        global INVOCATION
        params = request_payload(self)
        signatures = signature_list(params)
        if len(signatures) != 1:
            raise ValueError("/work accepts exactly one signature")
        executions = int_param(params, "executions", int(env_default("EXECUTIONS", "3")))
        variants = max(1, int_param(params, "compile_variants", int(env_default("COMPILE_VARIANTS", "1"))))
        schedule = schedule_param(params)

        INVOCATION += 1
        started = time.perf_counter()
        first_work = INVOCATION == 1
        config_meta = ensure_jax_configured()
        if schedule:
            variant_indices = scheduled_variant_indices(schedule, INVOCATION)
            row = compile_variant_indices(
                signature_name=signatures[0],
                label=params.get("label", f"jax-openfaas-{cache_mode()}"),
                iteration=int_param(params, "iteration", INVOCATION),
                executions=executions,
                variant_indices=variant_indices,
            )
            row["variant_schedule"] = schedule
            row["scheduled_variant_indices"] = variant_indices
        else:
            row = compile_variants(
                signature_name=signatures[0],
                label=params.get("label", f"jax-openfaas-{cache_mode()}"),
                iteration=int_param(params, "iteration", INVOCATION),
                executions=executions,
                variants=variants,
            )
        export_meta: dict[str, Any] = {}
        if cache_mode() == "populate" and str(params.get("export", "0")) == "1":
            export_meta = cachectl.push_cache_to_redis()

        self.write_json(
            HTTPStatus.OK,
            {
                "ok": True,
                **common_metadata(),
                "first_work": first_work,
                "request_in_pod": INVOCATION,
                "handler_ms": (time.perf_counter() - started) * 1000.0,
                "config": config_meta,
                "result": row,
                "export": export_meta,
                **cache_snapshot(),
            },
        )

    def write_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def listen_address() -> tuple[str, int]:
    upstream = os.getenv("http_upstream_url") or os.getenv("upstream_url") or ""
    if upstream.startswith("http://"):
        parsed = urlparse(upstream)
        return parsed.hostname or "127.0.0.1", parsed.port or 8082
    raw = os.getenv("HANDLER_ADDR", ":8082")
    if raw.startswith(":"):
        return "", int(raw[1:])
    host, sep, port = raw.rpartition(":")
    if not sep:
        return "", int(raw)
    return host, int(port)


def main() -> int:
    host, port = listen_address()
    server = ThreadingHTTPServer((host, port), Handler)
    print(
        json.dumps(
            {
                "event": "handler_started",
                "addr": f"{host or '0.0.0.0'}:{port}",
                **common_metadata(),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
