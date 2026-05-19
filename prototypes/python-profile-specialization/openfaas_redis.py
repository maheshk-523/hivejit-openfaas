"""Small Redis client for OpenFaaS artifact import/export."""

from __future__ import annotations

import os
import socket
from typing import Any


class RedisError(RuntimeError):
    pass


def redis_addr() -> tuple[str, int]:
    raw = os.getenv("REDIS_ADDR", "profile-cache-redis.openfaas-fn.svc.cluster.local:6379")
    host, sep, port = raw.rpartition(":")
    if not sep:
        return raw, 6379
    return host, int(port)


def redis_db() -> int:
    return int(os.getenv("REDIS_DB", "0"))


def redis_password() -> str:
    return os.getenv("REDIS_PASSWORD", "")


def timeout_seconds() -> float:
    raw = os.getenv("REDIS_TIMEOUT", "10s")
    if raw.endswith("ms"):
        return float(raw[:-2]) / 1000.0
    if raw.endswith("s"):
        return float(raw[:-1])
    return float(raw)


class RedisClient:
    def __init__(self) -> None:
        self.host, self.port = redis_addr()
        self.timeout = timeout_seconds()

    def ping(self) -> str:
        reply = self.command("PING")
        return reply.decode("utf-8") if isinstance(reply, bytes) else str(reply)

    def get(self, key: str) -> bytes | None:
        reply = self.command("GET", key)
        if reply is None:
            return None
        if not isinstance(reply, bytes):
            raise RedisError(f"unexpected GET reply {reply!r}")
        return reply

    def set(self, key: str, value: bytes) -> str:
        reply = self.command("SET", key, value)
        return reply.decode("utf-8") if isinstance(reply, bytes) else str(reply)

    def command(self, *parts: str | bytes) -> Any:
        with socket.create_connection((self.host, self.port), timeout=self.timeout) as sock:
            reader = sock.makefile("rb")
            if redis_password():
                sock.sendall(encode_command("AUTH", redis_password()))
                read_response(reader)
            if redis_db() != 0:
                sock.sendall(encode_command("SELECT", str(redis_db())))
                read_response(reader)
            sock.sendall(encode_command(*parts))
            return read_response(reader)


def encode_command(*parts: str | bytes) -> bytes:
    encoded = []
    for part in parts:
        encoded.append(part if isinstance(part, bytes) else part.encode("utf-8"))
    out = [f"*{len(encoded)}\r\n".encode("ascii")]
    for part in encoded:
        out.append(f"${len(part)}\r\n".encode("ascii"))
        out.append(part)
        out.append(b"\r\n")
    return b"".join(out)


def read_line(reader: Any) -> bytes:
    line = reader.readline()
    if not line:
        raise RedisError("unexpected EOF from Redis")
    if not line.endswith(b"\r\n"):
        raise RedisError(f"malformed Redis line: {line!r}")
    return line[:-2]


def read_response(reader: Any) -> Any:
    prefix = reader.read(1)
    if not prefix:
        raise RedisError("unexpected EOF from Redis")
    if prefix == b"+":
        return read_line(reader)
    if prefix == b"-":
        raise RedisError(read_line(reader).decode("utf-8", errors="replace"))
    if prefix == b":":
        return int(read_line(reader))
    if prefix == b"$":
        size = int(read_line(reader))
        if size == -1:
            return None
        data = reader.read(size)
        if len(data) != size:
            raise RedisError("truncated bulk string")
        trailer = reader.read(2)
        if trailer != b"\r\n":
            raise RedisError("malformed bulk string trailer")
        return data
    raise RedisError(f"unknown Redis response prefix: {prefix!r}")
