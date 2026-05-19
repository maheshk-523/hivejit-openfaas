#!/usr/bin/env python3
"""OpenFaaS HTTP handler for Python profile-specialization experiments."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import tempfile
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import handler
import openfaas_artifact
import profile_codegen
from openfaas_redis import RedisClient


STARTED_AT = time.time()
INVOCATION = 0
ARTIFACT_MODULE: Any | None = None


def mode() -> str:
    return os.getenv("PY_SPEC_MODE", "baseline").lower()


def build_label() -> str:
    return os.getenv("BUILD_LABEL", mode())


def pod_uid() -> str:
    return os.getenv("POD_UID", "unknown")


def hostname() -> str:
    return socket.gethostname()


def default_benchmark() -> str:
    return os.getenv("BENCHMARK", "dacapo-lusearch")


def artifact_path() -> Path:
    return openfaas_artifact.artifact_path()


def read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def common_metadata() -> dict[str, Any]:
    return {
        "mode": mode(),
        "build": build_label(),
        "pod_uid": pod_uid(),
        "hostname": hostname(),
        "redis_key": openfaas_artifact.artifact_key(),
        "process_uptime_ms": (time.time() - STARTED_AT) * 1000.0,
        "import": read_json(openfaas_artifact.import_meta_path()),
    }


def request_payload(req: BaseHTTPRequestHandler) -> dict[str, Any]:
    parsed = urlparse(req.path)
    params: dict[str, Any] = {key: values[-1] for key, values in parse_qs(parsed.query).items()}
    if req.command == "POST":
        length = int(req.headers.get("Content-Length") or "0")
        if length > 0:
            raw = req.rfile.read(min(length, 1 << 20))
            if raw:
                body = json.loads(raw.decode("utf-8"))
                if isinstance(body, dict):
                    params.update(body)
    return params


def int_param(params: dict[str, Any], name: str, default: int) -> int:
    try:
        return int(params.get(name, default))
    except (TypeError, ValueError):
        return default


def benchmark_param(params: dict[str, Any]) -> str:
    benchmark = str(params.get("benchmark") or default_benchmark())
    if benchmark not in handler.ROUTES:
        raise ValueError(f"unknown benchmark: {benchmark}")
    return benchmark


def ensure_artifact_loaded(benchmark: str) -> Any:
    global ARTIFACT_MODULE
    if ARTIFACT_MODULE is not None:
        return ARTIFACT_MODULE
    path = artifact_path()
    ARTIFACT_MODULE = handler.load_artifact(path)
    artifact_benchmark = getattr(ARTIFACT_MODULE, "BENCHMARK", None)
    if artifact_benchmark != benchmark:
        raise ValueError(f"artifact benchmark {artifact_benchmark!r} does not match {benchmark!r}")
    return ARTIFACT_MODULE


def run_work(benchmark: str, requests: int, seed: int) -> tuple[int, bool]:
    if mode() == "saved":
        artifact = ensure_artifact_loaded(benchmark)
        return int(artifact.run(requests, seed)), True
    checksum, _counts = handler.run_generic(benchmark, requests, seed)
    return checksum, False


def artifact_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Handler(BaseHTTPRequestHandler):
    server_version = "python-profile-specialization-openfaas/1.0"

    def do_GET(self) -> None:  # noqa: N802
        self.dispatch()

    def do_POST(self) -> None:  # noqa: N802
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
            elif parsed.path == "/profile/ping":
                self.write_json(HTTPStatus.OK, {"ok": True, "reply": RedisClient().ping(), **common_metadata()})
            elif parsed.path == "/profile/populate":
                self.handle_populate()
            elif parsed.path == "/profile/metadata":
                self.write_json(HTTPStatus.OK, {"ok": True, **common_metadata()})
            else:
                self.write_json(HTTPStatus.NOT_FOUND, {"error": "not found", "path": parsed.path})
        except ValueError as exc:
            self.write_json(HTTPStatus.BAD_REQUEST, {"error": str(exc), **common_metadata()})
        except Exception as exc:  # noqa: BLE001 - benchmark wants JSON failures.
            self.write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc), **common_metadata()})

    def handle_work(self) -> None:
        global INVOCATION
        INVOCATION += 1
        params = request_payload(self)
        benchmark = benchmark_param(params)
        requests = int_param(params, "requests", int(os.getenv("REQUESTS", "12000")))
        seed = int_param(params, "seed", int_param(params, "iteration", INVOCATION))

        started = time.perf_counter()
        checksum, used_artifact = run_work(benchmark, requests, seed)
        handler_ms = (time.perf_counter() - started) * 1000.0
        self.write_json(
            HTTPStatus.OK,
            {
                "ok": True,
                **common_metadata(),
                "first_work": INVOCATION == 1,
                "invocation": INVOCATION,
                "benchmark": benchmark,
                "requests": requests,
                "seed": seed,
                "handler_ms": handler_ms,
                "result": {
                    "checksum": checksum,
                    "used_artifact": used_artifact,
                    "work_ms": handler_ms,
                },
            },
        )

    def handle_populate(self) -> None:
        params = request_payload(self)
        benchmark = benchmark_param(params)
        profile_iters = int_param(params, "profile_iters", int(os.getenv("PROFILE_ITERS", "3")))
        profile_requests = int_param(params, "profile_requests", int(os.getenv("PROFILE_REQUESTS", "36000")))

        started = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix="python-profiles-") as tmp:
            tmp_dir = Path(tmp)
            profile_paths = []
            for index in range(1, profile_iters + 1):
                checksum, route_counts = handler.run_generic(benchmark, profile_requests, index)
                profile_path = tmp_dir / f"invoke-{index}.json"
                handler.write_profile(profile_path, benchmark, profile_requests, index, checksum, route_counts)
                profile_paths.append(profile_path)

            artifact = artifact_path()
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text(
                profile_codegen.generate_artifact(*profile_codegen.load_profiles(profile_paths)),
                encoding="utf-8",
            )
            export_meta = openfaas_artifact.put(artifact)

        self.write_json(
            HTTPStatus.OK,
            {
                "ok": True,
                **common_metadata(),
                "benchmark": benchmark,
                "profile_iters": profile_iters,
                "profile_requests": profile_requests,
                "handler_ms": (time.perf_counter() - started) * 1000.0,
                "artifact_hash": artifact_hash(artifact),
                "export": export_meta,
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
    raw = os.getenv("HANDLER_ADDR", ":8082")
    if raw.startswith(":"):
        return "", int(raw[1:])
    host, sep, port = raw.rpartition(":")
    if not sep:
        return "", int(raw)
    return host, int(port)


def main() -> int:
    if mode() == "saved" and os.getenv("PY_SPEC_PRELOAD_ARTIFACT", "1") == "1":
        started = time.perf_counter()
        ensure_artifact_loaded(default_benchmark())
        print(
            json.dumps(
                {
                    "event": "artifact_preloaded",
                    "preload_ms": (time.perf_counter() - started) * 1000.0,
                    **common_metadata(),
                },
                sort_keys=True,
            ),
            flush=True,
        )
    host, port = listen_address()
    server = ThreadingHTTPServer((host, port), Handler)
    print(json.dumps({"event": "handler_started", "addr": f"{host or '0.0.0.0'}:{port}", **common_metadata()}), flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
