#!/usr/bin/env python3
"""JAX/XLA runtime-signature specialization and persistent cache probe."""

from __future__ import annotations

import argparse
import csv
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


def observed_profile(selected: list[str]) -> dict[str, Any]:
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
            "persistent compilation cache -> fresh-process reuse"
        ),
        "signatures": signatures,
    }


def configure_jax(cache_dir: Path | None) -> None:
    # Keep CPU measurements comparable across machines and avoid accidental GPU use.
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
        # Search/indexing shape: token scoring, phrase windows, and rank-like top values.
        for step in range(depth):
            token_scores = jnp.sin(z * (1.0 + step * 0.07))
            phrase_windows = jnp.cos(jnp.roll(z, shift=step + 1, axis=1) * 0.5)
            ranked = jnp.sort(token_scores + phrase_windows, axis=1)
            z = jnp.tanh((0.72 * token_scores) + (0.18 * phrase_windows) + (0.10 * ranked))
            z = jnp.where(z > 0.0, z, z * 0.25)
    elif variant == "h2":
        # Relational query shape: filters, prefix/range scans, and aggregate-style reductions.
        column_ids = jnp.arange(z.shape[1], dtype=z.dtype)
        region = jnp.mod(column_ids, 5.0)
        for step in range(depth):
            threshold = 0.02 + step * 0.01
            filtered = jnp.where(z + region * 0.001 > threshold, z, 0.0)
            range_scan = jnp.cumsum(filtered, axis=1)
            join_probe = jnp.roll(filtered, shift=step + 1, axis=1)
            z = jnp.tanh(filtered + 0.025 * range_scan + 0.35 * join_probe + bias)
    elif variant == "eclipse":
        # Compiler/IDE shape: parse scans, symbol-resolution mixing, and workspace indexing.
        for step in range(depth):
            parsed = jnp.cumsum(jnp.sin(z * (1.0 + step * 0.03)), axis=1)
            resolved = parsed + jnp.roll(parsed, shift=step + 1, axis=1)
            indexed = jnp.sort(resolved, axis=1)
            z = jnp.tanh((0.55 * resolved) + (0.45 * indexed) + bias)
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

    return jax.jit(model_kernel, static_argnames=("variant", "depth"))


def make_inputs(spec: dict[str, Any], seed: int) -> tuple[Any, Any, Any]:
    import jax.numpy as jnp
    import numpy as np

    dtype = np.float32
    rng = np.random.default_rng(seed)
    x = rng.normal(size=tuple(spec["xShape"])).astype(dtype)
    w = rng.normal(size=tuple(spec["wShape"])).astype(dtype) / max(1, int(spec["wShape"][0]))
    bias = rng.normal(size=tuple(spec["biasShape"])).astype(dtype) * 0.01
    return jnp.asarray(x), jnp.asarray(w), jnp.asarray(bias)


def stable_hash(spec: dict[str, Any]) -> str:
    payload = json.dumps(spec, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:12]


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
    hlo_dir: Path | None,
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

    hlo_path = ""
    if hlo_dir is not None:
        hlo_dir.mkdir(parents=True, exist_ok=True)
        hlo_file = hlo_dir / f"{signature['name']}-{stable_hash(signature)}.hlo.txt"
        hlo_file.write_text(compiled.as_text(), encoding="utf-8")
        hlo_path = str(hlo_file)

    execute_ms_values = []
    checksum = 0.0
    for exec_index in range(executions):
        start = time.perf_counter()
        out = compiled(x, w, bias)
        out.block_until_ready()
        execute_ms_values.append((time.perf_counter() - start) * 1000.0)
        checksum = float(jax.device_get(jnp.sum(out)))

        # Slightly perturb inputs between executions without changing the signature.
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
        "hlo_path": hlo_path,
    }


def stable_seed(name: str) -> int:
    return int(hashlib.sha256(name.encode("utf-8")).hexdigest()[:8], 16)


def load_profile(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != SCHEMA:
        raise ValueError(f"unsupported profile schema in {path}")
    return list(data["signatures"])


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "label",
        "signature",
        "signature_hash",
        "iteration",
        "compile_or_load_ms",
        "execute_ms_median",
        "execute_ms_min",
        "execute_ms_max",
        "checksum",
        "cache_enabled",
        "cache_files",
        "cache_bytes",
        "hlo_path",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            formatted = dict(row)
            for key in (
                "compile_or_load_ms",
                "execute_ms_median",
                "execute_ms_min",
                "execute_ms_max",
                "checksum",
            ):
                formatted[key] = f"{float(formatted[key]):.6f}"
            formatted["cache_enabled"] = str(bool(formatted["cache_enabled"])).lower()
            writer.writerow(formatted)


def command_profile(args: argparse.Namespace) -> int:
    profile = observed_profile(args.signature)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")
    print(f"wrote runtime signature profile: {args.out}")
    for signature in profile["signatures"]:
        static_args = signature["staticArgs"]
        print(
            f"observed {signature['name']}: x={signature['xShape']} w={signature['wShape']} "
            f"dtype={signature['dtype']} variant={static_args['variant']} depth={static_args['depth']}"
        )
    return 0


def command_compile(args: argparse.Namespace) -> int:
    cache_dir = args.cache_dir if args.cache_dir else None
    configure_jax(cache_dir)

    rows: list[dict[str, Any]] = []
    signatures = load_profile(args.profile)
    for iteration in range(1, args.iterations + 1):
        for signature in signatures:
            row = compile_signature(
                label=args.label,
                signature=signature,
                iteration=iteration,
                cache_dir=cache_dir,
                hlo_dir=args.hlo_dir,
                executions=args.executions,
            )
            rows.append(row)
            print(
                f"{args.label} {signature['name']} iteration={iteration} "
                f"compile_or_load_ms={row['compile_or_load_ms']:.3f} "
                f"execute_ms_median={row['execute_ms_median']:.3f} "
                f"cache_files={row['cache_files']}"
            )
    write_csv(args.csv, rows)
    print(f"wrote measurements: {args.csv}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    profile_parser = subparsers.add_parser("profile")
    profile_parser.add_argument("--out", required=True, type=Path)
    profile_parser.add_argument(
        "--signature",
        action="append",
        choices=sorted(SIGNATURES),
        default=[],
        help="runtime signature to include; repeatable",
    )
    profile_parser.set_defaults(func=command_profile)

    compile_parser = subparsers.add_parser("compile")
    compile_parser.add_argument("--profile", required=True, type=Path)
    compile_parser.add_argument("--csv", required=True, type=Path)
    compile_parser.add_argument("--label", required=True)
    compile_parser.add_argument("--cache-dir", type=Path)
    compile_parser.add_argument("--hlo-dir", type=Path)
    compile_parser.add_argument("--iterations", type=int, default=1)
    compile_parser.add_argument("--executions", type=int, default=3)
    compile_parser.set_defaults(func=command_compile)

    args = parser.parse_args()
    if args.command == "profile" and not args.signature:
        args.signature = ["dacapo-lusearch", "dacapo-h2", "dacapo-eclipse"]
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
