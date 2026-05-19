#!/usr/bin/env python3
"""Import/export generated Python specialization artifacts through Redis."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path

from openfaas_redis import RedisClient


def artifact_key() -> str:
    return os.getenv("PY_SPEC_ARTIFACT_KEY", "python-profile-specialization:artifact:v1")


def artifact_path() -> Path:
    return Path(os.getenv("PY_SPEC_ARTIFACT_PATH", "/profiles/specialized.py"))


def import_meta_path() -> Path:
    return Path(os.getenv("PY_SPEC_IMPORT_META", "/profiles/python-specialization-import.json"))


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def pull(require: bool) -> dict[str, object]:
    started = time.perf_counter()
    key = artifact_key()
    path = artifact_path()
    payload = RedisClient().get(key)
    meta: dict[str, object]
    if payload is None:
        meta = {
            "imported": False,
            "artifact_found": False,
            "artifact_bytes": 0,
            "artifact_hash": "",
            "redis_key": key,
            "import_ms": (time.perf_counter() - started) * 1000.0,
        }
        write_json(import_meta_path(), meta)
        if require:
            raise SystemExit(f"missing required Redis artifact: {key}")
        return meta

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    meta = {
        "imported": True,
        "artifact_found": True,
        "artifact_bytes": len(payload),
        "artifact_hash": sha256(payload),
        "redis_key": key,
        "artifact_path": str(path),
        "import_ms": (time.perf_counter() - started) * 1000.0,
    }
    write_json(import_meta_path(), meta)
    return meta


def put(path: Path) -> dict[str, object]:
    started = time.perf_counter()
    payload = path.read_bytes()
    key = artifact_key()
    reply = RedisClient().set(key, payload)
    return {
        "ok": reply == "OK",
        "reply": reply,
        "artifact_bytes": len(payload),
        "artifact_hash": sha256(payload),
        "redis_key": key,
        "export_ms": (time.perf_counter() - started) * 1000.0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    pull_parser = sub.add_parser("pull")
    pull_parser.add_argument("--require", action="store_true")
    put_parser = sub.add_parser("put")
    put_parser.add_argument("--path", type=Path, default=artifact_path())
    sub.add_parser("ping")
    args = parser.parse_args()

    if args.command == "pull":
        print(json.dumps(pull(args.require), sort_keys=True), flush=True)
    elif args.command == "put":
        print(json.dumps(put(args.path), sort_keys=True), flush=True)
    elif args.command == "ping":
        print(json.dumps({"reply": RedisClient().ping()}, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
