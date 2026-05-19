#!/usr/bin/env python3
"""Redis import/export helpers for the JAX persistent compilation cache."""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import socket
import tarfile
import time
from pathlib import Path
from typing import Any


class RedisError(RuntimeError):
    pass


class RedisClient:
    def __init__(self) -> None:
        host, port = redis_host_port()
        self.host = host
        self.port = port
        self.password = os.getenv("REDIS_PASSWORD") or os.getenv("redis_password") or ""
        self.db = int(os.getenv("REDIS_DB") or os.getenv("redis_db") or "0")
        self.timeout = parse_seconds(os.getenv("REDIS_TIMEOUT") or "5s")

    def command(self, *args: bytes | str | int) -> Any:
        encoded = [encode_arg(arg) for arg in args]
        with socket.create_connection((self.host, self.port), timeout=self.timeout) as sock:
            sock.settimeout(self.timeout)
            reader = sock.makefile("rb")
            if self.password:
                self._send(sock, "AUTH", self.password)
                read_resp(reader)
            if self.db > 0:
                self._send(sock, "SELECT", str(self.db))
                read_resp(reader)
            self._send(sock, *encoded)
            return read_resp(reader)

    def ping(self) -> str:
        reply = self.command("PING")
        if isinstance(reply, bytes):
            return reply.decode("utf-8", errors="replace")
        return str(reply)

    def get(self, key: str) -> bytes | None:
        reply = self.command("GET", key)
        if reply is None:
            return None
        if not isinstance(reply, bytes):
            raise RedisError(f"unexpected GET reply type {type(reply).__name__}")
        return reply

    def set(self, key: str, value: bytes) -> None:
        reply = self.command("SET", key, value)
        if reply not in ("OK", b"OK"):
            raise RedisError(f"unexpected SET reply: {reply!r}")

    @staticmethod
    def _send(sock: socket.socket, *args: bytes | str | int) -> None:
        encoded = [encode_arg(arg) for arg in args]
        parts = [f"*{len(encoded)}\r\n".encode("ascii")]
        for arg in encoded:
            parts.append(f"${len(arg)}\r\n".encode("ascii"))
            parts.append(arg)
            parts.append(b"\r\n")
        sock.sendall(b"".join(parts))


def encode_arg(arg: bytes | str | int) -> bytes:
    if isinstance(arg, bytes):
        return arg
    return str(arg).encode("utf-8")


def read_line(reader: Any) -> bytes:
    line = reader.readline()
    if not line:
        raise RedisError("unexpected EOF from Redis")
    if not line.endswith(b"\r\n"):
        raise RedisError(f"malformed Redis line: {line!r}")
    return line[:-2]


def read_resp(reader: Any) -> Any:
    prefix = reader.read(1)
    if not prefix:
        raise RedisError("unexpected EOF from Redis")
    if prefix == b"+":
        return read_line(reader).decode("utf-8", errors="replace")
    if prefix == b"-":
        raise RedisError(read_line(reader).decode("utf-8", errors="replace"))
    if prefix == b":":
        return int(read_line(reader))
    if prefix == b"$":
        length = int(read_line(reader))
        if length < 0:
            return None
        data = reader.read(length)
        trailer = reader.read(2)
        if len(data) != length or trailer != b"\r\n":
            raise RedisError("malformed Redis bulk string")
        return data
    if prefix == b"*":
        count = int(read_line(reader))
        if count < 0:
            return None
        return [read_resp(reader) for _ in range(count)]
    raise RedisError(f"unknown Redis response prefix: {prefix!r}")


def redis_host_port() -> tuple[str, int]:
    raw_addr = os.getenv("REDIS_ADDR") or ""
    if not raw_addr:
        host = os.getenv("REDIS_HOST") or os.getenv("redis_host") or "redis.openfaas.svc.cluster.local"
        port = int(os.getenv("REDIS_PORT") or os.getenv("redis_port") or "6379")
        return host, port
    if raw_addr.startswith("redis://"):
        raw_addr = raw_addr.removeprefix("redis://").split("/", 1)[0]
    host, sep, raw_port = raw_addr.rpartition(":")
    if not sep:
        return raw_addr, 6379
    return host, int(raw_port)


def parse_seconds(raw: str) -> float:
    value = raw.strip().lower()
    if value.endswith("ms"):
        return float(value[:-2]) / 1000.0
    if value.endswith("s"):
        return float(value[:-1])
    return float(value)


def cache_dir() -> Path:
    return Path(os.getenv("JAX_CACHE_DIR", "/profiles/jax-cache"))


def cache_key() -> str:
    return os.getenv("JAX_CACHE_KEY", "jax-xla-cache:default")


def mode() -> str:
    return os.getenv("JAX_CACHE_MODE", "baseline").lower()


def import_meta_path() -> Path:
    return Path(os.getenv("JAX_CACHE_IMPORT_META", "/profiles/jax-cache-import.json"))


def export_meta_path() -> Path:
    return Path(os.getenv("JAX_CACHE_EXPORT_META", "/profiles/jax-cache-export.json"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def directory_stats(path: Path) -> tuple[int, int]:
    if not path.exists():
        return 0, 0
    files = [entry for entry in path.rglob("*") if entry.is_file()]
    return len(files), sum(entry.stat().st_size for entry in files)


def archive_cache(path: Path) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        if path.exists():
            for entry in sorted(path.rglob("*")):
                if entry.is_file():
                    tar.add(entry, arcname=str(entry.relative_to(path)))
    return buf.getvalue()


def safe_extract_cache(archive: bytes, path: Path) -> None:
    root = path.resolve()
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)

    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        for member in tar.getmembers():
            target = (path / member.name).resolve()
            if target != root and root not in target.parents:
                raise RedisError(f"refusing unsafe tar member: {member.name}")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            source = tar.extractfile(member)
            if source is None:
                continue
            with source, target.open("wb") as out:
                shutil.copyfileobj(source, out)
            try:
                target.chmod(member.mode)
            except OSError:
                pass


def pull_cache_from_redis(require_artifact: bool | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    key = cache_key()
    path = cache_dir()
    current_mode = mode()
    if require_artifact is None:
        require_artifact = os.getenv("JAX_CACHE_REQUIRE_ARTIFACT", "0") == "1"

    if current_mode != "redis":
        path.mkdir(parents=True, exist_ok=True)
        files, bytes_on_disk = directory_stats(path)
        meta = {
            "mode": current_mode,
            "redis_key": key,
            "artifact_found": False,
            "imported": False,
            "import_ms": 0.0,
            "cache_files": files,
            "cache_bytes": bytes_on_disk,
            "status": "skipped",
        }
        write_json(import_meta_path(), meta)
        return meta

    client = RedisClient()
    payload = client.get(key)
    if payload is None:
        meta = {
            "mode": current_mode,
            "redis_key": key,
            "artifact_found": False,
            "imported": False,
            "import_ms": (time.perf_counter() - started) * 1000.0,
            "cache_files": 0,
            "cache_bytes": 0,
            "artifact_bytes": 0,
            "status": "missing",
        }
        write_json(import_meta_path(), meta)
        if require_artifact:
            raise RedisError(f"missing required Redis artifact: {key}")
        path.mkdir(parents=True, exist_ok=True)
        return meta

    safe_extract_cache(payload, path)
    files, bytes_on_disk = directory_stats(path)
    meta = {
        "mode": current_mode,
        "redis_key": key,
        "artifact_found": True,
        "imported": True,
        "import_ms": (time.perf_counter() - started) * 1000.0,
        "cache_files": files,
        "cache_bytes": bytes_on_disk,
        "artifact_bytes": len(payload),
        "status": "ok",
    }
    write_json(import_meta_path(), meta)
    return meta


def push_cache_to_redis() -> dict[str, Any]:
    started = time.perf_counter()
    path = cache_dir()
    key = cache_key()
    files, bytes_on_disk = directory_stats(path)
    payload = archive_cache(path)
    RedisClient().set(key, payload)
    meta = {
        "mode": mode(),
        "redis_key": key,
        "exported": True,
        "export_ms": (time.perf_counter() - started) * 1000.0,
        "cache_files": files,
        "cache_bytes": bytes_on_disk,
        "artifact_bytes": len(payload),
        "status": "ok",
    }
    write_json(export_meta_path(), meta)
    return meta


def command_pull(_args: argparse.Namespace) -> int:
    try:
        meta = pull_cache_from_redis()
    except Exception as exc:  # noqa: BLE001 - entrypoint records startup failures.
        meta = {
            "mode": mode(),
            "redis_key": cache_key(),
            "artifact_found": False,
            "imported": False,
            "status": "error",
            "error": str(exc),
        }
        write_json(import_meta_path(), meta)
        if os.getenv("JAX_CACHE_REQUIRE_ARTIFACT", "0") == "1":
            print(json.dumps(meta, indent=2), flush=True)
            return 2
    print(json.dumps(meta, indent=2), flush=True)
    return 0


def command_push(_args: argparse.Namespace) -> int:
    meta = push_cache_to_redis()
    print(json.dumps(meta, indent=2), flush=True)
    return 0


def command_ping(_args: argparse.Namespace) -> int:
    print(json.dumps({"ok": True, "reply": RedisClient().ping()}), flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("pull").set_defaults(func=command_pull)
    subparsers.add_parser("push").set_defaults(func=command_push)
    subparsers.add_parser("ping").set_defaults(func=command_ping)
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
