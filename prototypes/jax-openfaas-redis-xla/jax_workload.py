#!/usr/bin/env python3
"""DaCapo-shaped JAX/XLA workload used by the OpenFaaS Redis prototype."""

from __future__ import annotations

import hashlib
import json
import os
import statistics
import time
from pathlib import Path
from typing import Any


SCHEMA = "jax-xla-runtime-specialization.v1"

SIGNATURES: dict[str, dict[str, Any]] = {
    "dacapo-lusearch": {
        "xShape": [128, 96],
        "wShape": [96, 64],
        "biasShape": [64],
        "dtype": "float32",
        "staticArgs": {"variant": "lusearch", "depth": 4},
        "calls": 16,
    },
    "dacapo-h2": {
        "xShape": [160, 72],
        "wShape": [72, 64],
        "biasShape": [64],
        "dtype": "float32",
        "staticArgs": {"variant": "h2", "depth": 5},
        "calls": 16,
    },
    "dacapo-eclipse": {
        "xShape": [144, 80],
        "wShape": [80, 56],
        "biasShape": [56],
        "dtype": "float32",
        "staticArgs": {"variant": "eclipse", "depth": 5},
        "calls": 16,
    },
    "dacapo-fop": {
        "xShape": [120, 88],
        "wShape": [88, 60],
        "biasShape": [60],
        "dtype": "float32",
        "staticArgs": {"variant": "fop", "depth": 5},
        "calls": 16,
    },
    "dacapo-jython": {
        "xShape": [136, 76],
        "wShape": [76, 52],
        "biasShape": [52],
        "dtype": "float32",
        "staticArgs": {"variant": "jython", "depth": 6},
        "calls": 16,
    },
    "mlp-small": {
        "xShape": [96, 64],
        "wShape": [64, 48],
        "biasShape": [48],
        "dtype": "float32",
        "staticArgs": {"variant": "mlp", "depth": 3},
        "calls": 18,
    },
    "mlp-wide": {
        "xShape": [128, 96],
        "wShape": [96, 64],
        "biasShape": [64],
        "dtype": "float32",
        "staticArgs": {"variant": "mlp", "depth": 4},
        "calls": 10,
    },
    "attention-small": {
        "xShape": [64, 64],
        "wShape": [64, 48],
        "biasShape": [48],
        "dtype": "float32",
        "staticArgs": {"variant": "attention", "depth": 2},
        "calls": 6,
    },
}

_JITTED_KERNEL: Any | None = None


def profile_for_signatures(selected: list[str]) -> dict[str, Any]:
    signatures = []
    for name in selected:
        if name not in SIGNATURES:
            raise ValueError(f"unknown signature {name}")
        spec = dict(SIGNATURES[name])
        spec["name"] = name
        signatures.append(spec)
    return {
        "schema": SCHEMA,
        "domain": "JAX/XLA",
        "pattern": (
            "runtime tensor shapes/dtypes/static args -> XLA executable -> "
            "persistent compilation cache -> Redis artifact -> fresh OpenFaaS pod"
        ),
        "signatures": signatures,
    }


def configure_jax(cache_dir: Path | None) -> None:
    # Keep the function CPU-only so OpenFaaS pod placement does not affect runs.
    os.environ.setdefault("JAX_PLATFORMS", "cpu")

    import jax

    jax.config.update("jax_enable_x64", False)
    if cache_dir is None:
        return

    cache_dir.mkdir(parents=True, exist_ok=True)
    jax.config.update("jax_compilation_cache_dir", str(cache_dir))
    jax.config.update("jax_persistent_cache_min_compile_time_secs", 0)
    jax.config.update("jax_persistent_cache_min_entry_size_bytes", -1)


def model_kernel(x: Any, w: Any, bias: Any, *, variant: str, depth: int) -> Any:
    import jax
    import jax.numpy as jnp

    z = x @ w + bias
    if variant == "lusearch":
        for step in range(depth):
            token_scores = jnp.sin(z * (1.0 + step * 0.07))
            phrase_windows = jnp.cos(jnp.roll(z, shift=step + 1, axis=1) * 0.5)
            ranked = jnp.sort(token_scores + phrase_windows, axis=1)
            z = jnp.tanh((0.72 * token_scores) + (0.18 * phrase_windows) + (0.10 * ranked))
            z = jnp.where(z > 0.0, z, z * 0.25)
    elif variant == "h2":
        column_ids = jnp.arange(z.shape[1], dtype=z.dtype)
        region = jnp.mod(column_ids, 5.0)
        for step in range(depth):
            threshold = 0.02 + step * 0.01
            filtered = jnp.where(z + region * 0.001 > threshold, z, 0.0)
            range_scan = jnp.cumsum(filtered, axis=1)
            join_probe = jnp.roll(filtered, shift=step + 1, axis=1)
            z = jnp.tanh(filtered + 0.025 * range_scan + 0.35 * join_probe + bias)
    elif variant == "eclipse":
        for step in range(depth):
            parsed = jnp.cumsum(jnp.sin(z * (1.0 + step * 0.03)), axis=1)
            resolved = parsed + jnp.roll(parsed, shift=step + 1, axis=1)
            indexed = jnp.sort(resolved, axis=1)
            z = jnp.tanh((0.55 * resolved) + (0.45 * indexed) + bias)
    elif variant == "fop":
        columns = jnp.arange(z.shape[1], dtype=z.dtype)
        line_boxes = jnp.sin(z + columns * 0.013)
        for step in range(depth):
            block_edges = jnp.where(line_boxes > 0.0, line_boxes, line_boxes * 0.35)
            page_cursor = jnp.cumsum(jnp.abs(block_edges), axis=1)
            page_breaks = jnp.mod(page_cursor + step * 0.17, 1.0)
            rendered = jnp.cos(jnp.roll(block_edges, shift=step + 1, axis=1) + page_breaks)
            z = jnp.tanh(0.52 * block_edges + 0.31 * rendered + 0.17 * page_breaks + bias)
            line_boxes = z + jnp.roll(z, shift=2, axis=1) * 0.07
    elif variant == "jython":
        op_ids = jnp.mod(jnp.arange(z.shape[1], dtype=z.dtype), 7.0)
        for step in range(depth):
            stack_top = jnp.roll(z, shift=step + 1, axis=1)
            locals_view = jnp.roll(z, shift=-(step + 2), axis=1)
            binary = jnp.where(op_ids < 3.0, z + stack_top, z * 0.35 + locals_view)
            call_path = jnp.sin(binary + op_ids * 0.031)
            exception_path = jnp.where(binary > 0.05, binary, -binary * 0.5)
            z = jnp.tanh(0.46 * binary + 0.32 * call_path + 0.22 * exception_path + bias)
    elif variant == "mlp":
        for step in range(depth):
            z = jnp.tanh(z * (1.0 + step * 0.05))
            z = z + 0.05 * jnp.sin(z * 1.7)
            z = jnp.where(z > 0.0, z, z * 0.2)
    elif variant == "attention":
        scale = jnp.asarray(w.shape[1], dtype=z.dtype) ** -0.5
        for step in range(depth):
            scores = (z @ jnp.swapaxes(z, 0, 1)) * scale
            weights = jax.nn.softmax(scores, axis=-1)
            z = jnp.tanh((weights @ z) + bias + step * 0.01)
    else:
        raise ValueError(f"unknown variant {variant}")
    return jnp.sum(z, axis=1)


def jitted_kernel() -> Any:
    import jax

    global _JITTED_KERNEL
    if _JITTED_KERNEL is None:
        _JITTED_KERNEL = jax.jit(model_kernel, static_argnames=("variant", "depth"))
    return _JITTED_KERNEL


def stable_seed(name: str) -> int:
    return int(hashlib.sha256(name.encode("utf-8")).hexdigest()[:8], 16)


def stable_hash(spec: dict[str, Any]) -> str:
    payload = json.dumps(spec, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:12]


def make_inputs(spec: dict[str, Any], seed: int) -> tuple[Any, Any, Any]:
    import jax.numpy as jnp
    import numpy as np

    dtype = np.float32
    rng = np.random.default_rng(seed)
    x = rng.normal(size=tuple(spec["xShape"])).astype(dtype)
    w = rng.normal(size=tuple(spec["wShape"])).astype(dtype) / max(1, int(spec["wShape"][0]))
    bias = rng.normal(size=tuple(spec["biasShape"])).astype(dtype) * 0.01
    return jnp.asarray(x), jnp.asarray(w), jnp.asarray(bias)


def cache_stats(path: Path | None) -> tuple[int, int]:
    if path is None or not path.exists():
        return 0, 0
    files = [entry for entry in path.rglob("*") if entry.is_file()]
    return len(files), sum(entry.stat().st_size for entry in files)


def compile_signature(
    label: str,
    signature: dict[str, Any],
    iteration: int,
    cache_dir: Path | None,
    executions: int,
) -> dict[str, Any]:
    import jax
    import jax.numpy as jnp

    kernel = jitted_kernel()
    static_args = signature["staticArgs"]
    x, w, bias = make_inputs(signature, seed=iteration + stable_seed(signature["name"]))

    compile_start = time.perf_counter()
    lowered = kernel.lower(
        x,
        w,
        bias,
        variant=static_args["variant"],
        depth=int(static_args["depth"]),
    )
    compiled = lowered.compile()
    compile_or_load_ms = (time.perf_counter() - compile_start) * 1000.0

    execute_ms_values = []
    checksum = 0.0
    for exec_index in range(executions):
        start = time.perf_counter()
        out = compiled(x, w, bias)
        out.block_until_ready()
        execute_ms_values.append((time.perf_counter() - start) * 1000.0)
        checksum = float(jax.device_get(jnp.sum(out)))

        if exec_index + 1 < executions:
            x = x + jnp.asarray(0.000001, dtype=x.dtype)

    file_count, byte_count = cache_stats(cache_dir)
    return {
        "label": label,
        "signature": signature["name"],
        "signature_hash": stable_hash(signature),
        "iteration": iteration,
        "compile_or_load_ms": compile_or_load_ms,
        "execute_ms_median": statistics.median(execute_ms_values),
        "execute_ms_min": min(execute_ms_values),
        "execute_ms_max": max(execute_ms_values),
        "checksum": checksum,
        "cache_enabled": cache_dir is not None,
        "cache_files": file_count,
        "cache_bytes": byte_count,
    }
