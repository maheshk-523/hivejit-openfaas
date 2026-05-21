#!/usr/bin/env python3
"""Collect and plot first-iteration warmup data for JAX-native XLA kernels."""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUN_ROOT = ROOT / "prototypes/jax-native-xla-first-requests/results"
DEFAULT_FIGURE = ROOT / "docs/figures/jax-native-xla-first-requests-medians.png"
DEFAULT_SUMMARY = ROOT / "docs/figures/jax-native-xla-first-requests-medians-summary.json"
DEFAULT_COMPILE_FIGURE = ROOT / "docs/figures/jax-native-xla-first-compile-medians.png"
DEFAULT_COMPILE_SUMMARY = ROOT / "docs/figures/jax-native-xla-first-compile-medians-summary.json"

BENCHMARKS = [
    ("dense-matmul", "Dense matmul"),
    ("mlp-forward", "MLP forward"),
    ("attention", "Scaled dot-product attention"),
    ("conv2d", "Conv2D feature map"),
    ("scan-rnn", "lax.scan recurrent loop"),
]
MODES = [
    ("no-cache", "JIT, no persistent cache", "#d64b4b"),
    ("saved-xla-cache", "JIT + saved XLA cache", "#4c9a42"),
]


def jax_python() -> Path:
    configured = os.environ.get("JAX_PYTHON")
    if configured:
        return Path(configured)
    bundled = ROOT / "prototypes/jax-xla-runtime-specialization/.venv/bin/python"
    if bundled.exists():
        return bundled
    return Path(sys.executable)


def worker_env() -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault("JAX_PLATFORMS", "cpu")
    return env


def csv_path(run_dir: Path, mode: str) -> Path:
    return run_dir / f"{mode}.csv"


def run_worker(
    *,
    mode: str,
    run_id: int,
    run_dir: Path,
    cache_dir: Path,
    iterations: int,
) -> None:
    cmd = [
        str(jax_python()),
        "-B",
        str(Path(__file__).resolve()),
        "--worker",
        "--mode",
        mode,
        "--run-id",
        str(run_id),
        "--run-dir",
        str(run_dir),
        "--cache-dir",
        str(cache_dir),
        "--iterations",
        str(iterations),
    ]
    subprocess.run(cmd, check=True, env=worker_env())


def collect(run_dir: Path, repeats: int, iterations: int) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = run_dir / "jax-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    for mode, _label, _color in MODES:
        path = csv_path(run_dir, mode)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "mode,run_id,benchmark,iteration,total_ms,compile_or_load_ms,execute_ms,checksum\n",
            encoding="utf-8",
        )

    print("== Populate saved XLA persistent cache ==")
    run_worker(mode="populate-cache", run_id=0, run_dir=run_dir, cache_dir=cache_dir, iterations=1)

    for run_id in range(1, repeats + 1):
        print(f"== Repeat {run_id}/{repeats}: no persistent cache ==")
        run_worker(mode="no-cache", run_id=run_id, run_dir=run_dir, cache_dir=cache_dir, iterations=iterations)
        print(f"== Repeat {run_id}/{repeats}: saved XLA persistent cache ==")
        run_worker(
            mode="saved-xla-cache",
            run_id=run_id,
            run_dir=run_dir,
            cache_dir=cache_dir,
            iterations=iterations,
        )


def configure_jax(cache_dir: Path | None) -> Any:
    os.environ.setdefault("JAX_PLATFORMS", "cpu")

    import jax

    jax.config.update("jax_enable_x64", False)
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        jax.config.update("jax_compilation_cache_dir", str(cache_dir))
        jax.config.update("jax_persistent_cache_min_compile_time_secs", 0)
        jax.config.update("jax_persistent_cache_min_entry_size_bytes", -1)
    return jax


def make_inputs(name: str, seed: int) -> tuple[Any, ...]:
    import jax.numpy as jnp
    import numpy as np

    rng = np.random.default_rng(seed)

    def normal(shape: tuple[int, ...], scale: float = 1.0) -> Any:
        return jnp.asarray((rng.normal(size=shape) * scale).astype(np.float32))

    if name == "dense-matmul":
        return normal((192, 256)), normal((256, 192), 0.05), normal((192,), 0.01)
    if name == "mlp-forward":
        return (
            normal((128, 256)),
            normal((256, 384), 0.04),
            normal((384,), 0.01),
            normal((384, 256), 0.04),
            normal((256,), 0.01),
            normal((256, 128), 0.04),
            normal((128,), 0.01),
        )
    if name == "attention":
        return normal((4, 64, 64)), normal((4, 64, 64)), normal((4, 64, 64))
    if name == "conv2d":
        return normal((1, 64, 64, 16)), normal((3, 3, 16, 32), 0.05), normal((32,), 0.01)
    if name == "scan-rnn":
        return normal((64, 64)), normal((64, 96), 0.05), normal((96, 96), 0.04), normal((96,), 0.01)
    raise ValueError(f"unknown benchmark {name}")


def kernel_for(name: str) -> Any:
    import jax
    import jax.numpy as jnp
    from jax import lax

    if name == "dense-matmul":

        def dense_matmul(x: Any, w: Any, bias: Any) -> Any:
            y = jnp.tanh((x @ w) + bias)
            return jnp.sum(y * y)

        return jax.jit(dense_matmul)

    if name == "mlp-forward":

        def mlp(x: Any, w1: Any, b1: Any, w2: Any, b2: Any, w3: Any, b3: Any) -> Any:
            h1 = jax.nn.gelu((x @ w1) + b1)
            h2 = jax.nn.relu((h1 @ w2) + b2)
            out = jnp.tanh((h2 @ w3) + b3)
            return jnp.sum(out)

        return jax.jit(mlp)

    if name == "attention":

        def attention(q: Any, k: Any, v: Any) -> Any:
            scale = q.shape[-1] ** -0.5
            scores = jnp.einsum("bqd,bkd->bqk", q, k) * scale
            weights = jax.nn.softmax(scores, axis=-1)
            out = jnp.einsum("bqk,bkd->bqd", weights, v)
            return jnp.sum(out)

        return jax.jit(attention)

    if name == "conv2d":

        def conv2d(x: Any, w: Any, bias: Any) -> Any:
            y = lax.conv_general_dilated(
                x,
                w,
                window_strides=(1, 1),
                padding="SAME",
                dimension_numbers=("NHWC", "HWIO", "NHWC"),
            )
            return jnp.mean(jax.nn.relu(y + bias))

        return jax.jit(conv2d)

    if name == "scan-rnn":

        def scan_rnn(xs: Any, wx: Any, wh: Any, bias: Any) -> Any:
            init = jnp.zeros((wh.shape[0],), dtype=xs.dtype)

            def step(carry: Any, x_t: Any) -> tuple[Any, Any]:
                nxt = jnp.tanh((x_t @ wx) + (carry @ wh) + bias)
                return nxt, nxt

            carry, ys = lax.scan(step, init, xs)
            return jnp.sum(carry) + jnp.sum(ys)

        return jax.jit(scan_rnn)

    raise ValueError(f"unknown benchmark {name}")


def benchmark_run(name: str, jitted: Any, seed: int) -> dict[str, float]:
    import jax

    inputs = make_inputs(name, seed)
    jax.block_until_ready(inputs)

    compile_start = time.perf_counter()
    lowered = jitted.lower(*inputs)
    compiled = lowered.compile()
    compile_or_load_ms = (time.perf_counter() - compile_start) * 1000.0

    execute_start = time.perf_counter()
    out = compiled(*inputs)
    out.block_until_ready()
    execute_ms = (time.perf_counter() - execute_start) * 1000.0
    checksum = float(jax.device_get(out))
    return {
        "total_ms": compile_or_load_ms + execute_ms,
        "compile_or_load_ms": compile_or_load_ms,
        "execute_ms": execute_ms,
        "checksum": checksum,
    }


def append_row(path: Path, row: dict[str, Any]) -> None:
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "mode",
                "run_id",
                "benchmark",
                "iteration",
                "total_ms",
                "compile_or_load_ms",
                "execute_ms",
                "checksum",
            ],
        )
        writer.writerow(row)


def worker_main(args: argparse.Namespace) -> int:
    cache_dir = Path(args.cache_dir)
    cache_enabled = args.mode in {"populate-cache", "saved-xla-cache"}
    configure_jax(cache_dir if cache_enabled else None)

    for benchmark_index, (benchmark, _display) in enumerate(BENCHMARKS):
        jitted = kernel_for(benchmark)
        for iteration in range(1, args.iterations + 1):
            seed = 1000 + benchmark_index * 100 + iteration
            result = benchmark_run(benchmark, jitted, seed)
            if args.mode == "populate-cache":
                continue
            append_row(
                csv_path(Path(args.run_dir), args.mode),
                {
                    "mode": args.mode,
                    "run_id": args.run_id,
                    "benchmark": benchmark,
                    "iteration": iteration,
                    **{key: f"{value:.6f}" for key, value in result.items()},
                },
            )
    return 0


def read_rows(path: Path, metric: str) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = []
        for row in csv.DictReader(f):
            rows.append(
                {
                    "mode": row["mode"],
                    "run_id": int(row["run_id"]),
                    "benchmark": row["benchmark"],
                    "iteration": int(row["iteration"]),
                    "value": float(row[metric]),
                    "total_ms": float(row["total_ms"]),
                    "compile_or_load_ms": float(row["compile_or_load_ms"]),
                    "execute_ms": float(row["execute_ms"]),
                }
            )
        return rows


def medians_by_iteration(rows: list[dict[str, Any]]) -> dict[int, float]:
    grouped: dict[int, list[float]] = {}
    for row in rows:
        grouped.setdefault(row["iteration"], []).append(row["value"])
    return {iteration: statistics.median(values) for iteration, values in sorted(grouped.items())}


def steady_value(medians: dict[int, float]) -> float:
    positions = sorted(medians)
    if not positions:
        return 0.0
    start_index = max(0, int(len(positions) * 0.8) - 1)
    start = positions[start_index]
    return statistics.median([value for iteration, value in medians.items() if iteration >= start])


def pct_saved(before: float, after: float) -> float:
    return ((before - after) / before * 100.0) if before > 0 else 0.0


def load_datasets(run_dir: Path, metric: str) -> dict[str, dict[str, Any]]:
    all_rows = {mode: read_rows(csv_path(run_dir, mode), metric) for mode, _label, _color in MODES}
    datasets: dict[str, dict[str, Any]] = {}
    for benchmark, _display in BENCHMARKS:
        datasets[benchmark] = {}
        for mode, _label, _color in MODES:
            rows = [row for row in all_rows[mode] if row["benchmark"] == benchmark]
            medians = medians_by_iteration(rows)
            datasets[benchmark][mode] = {
                "rows": rows,
                "medians": medians,
                "iteration_counts": {
                    iteration: sum(1 for row in rows if row["iteration"] == iteration)
                    for iteration in sorted(medians)
                },
                "first_median_ms": medians.get(1, 0.0),
                "steady_median_ms": steady_value(medians),
            }
    return datasets


def render(run_dir: Path, metric: str, out: Path, summary_path: Path, title_metric: str) -> None:
    import matplotlib.pyplot as plt

    datasets = load_datasets(run_dir, metric)
    fig, axes = plt.subplots(2, 3, figsize=(18, 10.4))
    flat_axes = list(axes.ravel())
    summary: dict[str, Any] = {
        "schema": "jax-native-xla-first-requests.v1",
        "source": str(run_dir),
        "metric": metric,
        "panels": {},
    }

    for panel_index, (benchmark, display) in enumerate(BENCHMARKS):
        ax = flat_axes[panel_index]
        max_y = 0.0
        for mode, label, color in MODES:
            medians = datasets[benchmark][mode]["medians"]
            xs = sorted(medians)
            ys = [medians[x] for x in xs]
            max_y = max(max_y, max(ys, default=0.0))
            ax.plot(xs, ys, marker="o", linewidth=2.4, markersize=5.5, color=color, label=label)

        saved_steady = datasets[benchmark]["saved-xla-cache"]["steady_median_ms"]
        if saved_steady > 0:
            ax.axhline(
                saved_steady,
                color="#777777",
                linestyle=":",
                linewidth=1.6,
                label=f"steady saved-cache = {saved_steady:.2f} ms",
            )

        first_no_cache = datasets[benchmark]["no-cache"]["first_median_ms"]
        first_saved = datasets[benchmark]["saved-xla-cache"]["first_median_ms"]
        annotation = (
            "iter-1 medians (ms):\n"
            f"no cache       {first_no_cache:8.2f}\n"
            f"saved XLA      {first_saved:8.2f}\n"
            f"steady         {saved_steady:8.2f}"
        )
        ax.text(
            0.65,
            0.40,
            annotation,
            transform=ax.transAxes,
            ha="left",
            va="top",
            family="monospace",
            fontsize=8.5,
            bbox={"facecolor": "white", "edgecolor": "#b8b8b8", "alpha": 0.88},
        )

        ax.set_title(display, fontsize=12.5)
        ax.set_xlabel("iteration in fresh process")
        ax.set_ylabel(f"{title_metric} (ms)")
        ax.set_xticks(range(1, max(datasets[benchmark]["no-cache"]["medians"] or {1: 0}) + 1))
        ax.set_ylim(bottom=0, top=max_y * 1.12 if max_y > 0 else 1)
        ax.grid(True, alpha=0.22)
        ax.legend(loc="upper right", fontsize=8.6, frameon=True)

        summary["panels"][benchmark] = {
            mode: {
                "iteration_counts": datasets[benchmark][mode]["iteration_counts"],
                "iteration_medians_ms": datasets[benchmark][mode]["medians"],
                "iteration_1_median_ms": datasets[benchmark][mode]["first_median_ms"],
                "steady_median_ms": datasets[benchmark][mode]["steady_median_ms"],
            }
            for mode, _label, _color in MODES
        }
        summary["panels"][benchmark]["comparison"] = {
            "iteration_1_saved_percent": pct_saved(first_no_cache, first_saved),
            "steady_saved_percent": pct_saved(
                datasets[benchmark]["no-cache"]["steady_median_ms"],
                datasets[benchmark]["saved-xla-cache"]["steady_median_ms"],
            ),
        }

    flat_axes[-1].axis("off")
    fig.suptitle(
        "JAX/XLA first 10 iterations - no persistent cache vs saved XLA cache",
        fontsize=17,
        fontweight="bold",
        y=0.985,
    )
    fig.text(
        0.5,
        0.952,
        "Per-iteration median across fresh Python processes using JAX-native kernels.",
        ha="center",
        fontsize=11,
    )
    fig.tight_layout(rect=(0.025, 0.045, 0.995, 0.935))

    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=180, facecolor="white")
    plt.close(fig)

    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def latest_run_dir() -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return DEFAULT_RUN_ROOT / f"{stamp}-jax-native-first-requests"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--mode", choices=["populate-cache", "no-cache", "saved-xla-cache"])
    parser.add_argument("--run-id", type=int, default=0)
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--iterations", type=int, default=10)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--skip-collect", action="store_true")
    parser.add_argument("--figure", type=Path, default=DEFAULT_FIGURE)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--compile-figure", type=Path, default=DEFAULT_COMPILE_FIGURE)
    parser.add_argument("--compile-summary", type=Path, default=DEFAULT_COMPILE_SUMMARY)
    args = parser.parse_args()

    if args.worker:
        if not args.mode or args.run_dir is None or args.cache_dir is None:
            raise SystemExit("--worker requires --mode, --run-dir, and --cache-dir")
        return worker_main(args)

    run_dir = args.run_dir or latest_run_dir()
    if not args.skip_collect:
        if run_dir.exists() and any(run_dir.iterdir()):
            raise SystemExit(f"refusing to overwrite non-empty run dir: {run_dir}")
        collect(run_dir, repeats=args.repeats, iterations=args.iterations)

    render(run_dir, "total_ms", args.figure, args.summary, "compile/load + execute")
    render(run_dir, "compile_or_load_ms", args.compile_figure, args.compile_summary, "compile/load")

    print(f"wrote {args.figure}")
    print(f"wrote {args.summary}")
    print(f"wrote {args.compile_figure}")
    print(f"wrote {args.compile_summary}")
    print(f"source run: {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
