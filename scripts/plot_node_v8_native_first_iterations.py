#!/usr/bin/env python3
"""Generate C#-style first-iteration plots for native Node/V8 workloads."""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import subprocess
import time
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[1]
PROTO = ROOT / "prototypes/node-v8-native-first-iterations"
RUNNER = PROTO / "run_once.js"
DEFAULT_RESULTS = PROTO / "results/node-v8-native-first-iterations"
DEFAULT_FIGURE = ROOT / "docs/figures/node-v8-native-first-10-iterations.png"
DEFAULT_SUMMARY = ROOT / "docs/figures/node-v8-native-first-10-iterations-summary.json"

WORKLOADS = [
    ("router-dispatch", "Router dispatch"),
    ("json-codec", "JSON codec"),
    ("regex-tokenizer", "Regex tokenizer"),
    ("template-render", "Template render"),
    ("query-aggregate", "Query aggregate"),
]

VARIANTS = [
    ("source", "source JIT/load", "#222222", "o", "-"),
    ("cached-cold", "V8 cachedData", "#E69F00", "s", "-"),
    ("cached-trained", "V8 cachedData + trained", "#0072B2", "^", "-"),
]


def run_json(cmd: list[str], timeout: float = 120.0) -> dict[str, Any]:
    result = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(cmd)}\nstdout={result.stdout}\nstderr={result.stderr}"
        )
    return json.loads(result.stdout)


def write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "workload",
        "variant",
        "repeat",
        "iteration",
        "total_ms",
        "compile_ms",
        "execute_ms",
        "cached_data_rejected",
        "cache_bytes",
        "checksum",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def read_rows(path: Path) -> list[dict[str, Any]]:
    parsed: list[dict[str, Any]] = []
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            parsed.append(
                {
                    "workload": row["workload"],
                    "variant": row["variant"],
                    "repeat": int(row["repeat"]),
                    "iteration": int(row["iteration"]),
                    "total_ms": float(row["total_ms"]),
                    "compile_ms": float(row["compile_ms"]),
                    "execute_ms": float(row["execute_ms"]),
                    "cached_data_rejected": row["cached_data_rejected"].lower() == "true",
                    "cache_bytes": int(float(row["cache_bytes"] or 0)),
                    "checksum": int(float(row["checksum"] or 0)),
                }
            )
    return parsed


def median(values: list[float]) -> float:
    return statistics.median(values) if values else 0.0


def saved_pct(before: float, after: float) -> float:
    return ((before - after) * 100.0 / before) if before else 0.0


def cache_path(results: Path, workload: str, kind: str) -> Path:
    return results / "cache" / f"{workload}-{kind}.blob"


def build_caches(args: argparse.Namespace, workload: str) -> None:
    for kind in ("cold", "trained"):
        out = cache_path(args.results, workload, kind)
        run_json(
            [
                "node",
                str(RUNNER),
                "make-cache",
                "--workload",
                workload,
                "--cache-kind",
                kind,
                "--function-count",
                str(args.function_count),
                "--rounds",
                str(args.rounds),
                "--warmup-iterations",
                str(args.warmup_iterations),
                "--out",
                str(out),
            ],
            timeout=180.0,
        )


def run_variant(args: argparse.Namespace, workload: str, variant: str, repeat: int) -> dict[str, Any]:
    cmd = [
        "node",
        str(RUNNER),
        "run",
        "--workload",
        workload,
        "--variant",
        variant,
        "--function-count",
        str(args.function_count),
        "--rounds",
        str(args.rounds),
        "--iterations",
        str(args.iterations),
        "--seed",
        str(args.seed + repeat * 7919),
    ]
    if variant == "cached-cold":
        cmd.extend(["--cache", str(cache_path(args.results, workload, "cold"))])
    elif variant == "cached-trained":
        cmd.extend(["--cache", str(cache_path(args.results, workload, "trained"))])
    return run_json(cmd, timeout=180.0)


def run_matrix(args: argparse.Namespace) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    args.results.mkdir(parents=True, exist_ok=True)
    for workload, _display in WORKLOADS:
        build_caches(args, workload)
        for variant, _label, _color, _marker, _style in VARIANTS:
            for repeat in range(1, args.repeats + 1):
                data = run_variant(args, workload, variant, repeat)
                for item in data["rows"]:
                    rows.append(
                        {
                            "workload": workload,
                            "variant": variant,
                            "repeat": repeat,
                            "iteration": item["iteration"],
                            "total_ms": item["total_ms"],
                            "compile_ms": item["compile_ms"],
                            "execute_ms": item["execute_ms"],
                            "cached_data_rejected": item["cached_data_rejected"],
                            "cache_bytes": item["cache_bytes"],
                            "checksum": item["checksum"],
                        }
                    )
    write_rows(args.results / "measurements.csv", rows)
    return rows


def series_for(rows: list[dict[str, Any]], workload: str, variant: str, iterations: int) -> list[float]:
    return [
        median(
            [
                row["total_ms"]
                for row in rows
                if row["workload"] == workload and row["variant"] == variant and row["iteration"] == iteration
            ]
        )
        for iteration in range(1, iterations + 1)
    ]


def repeat_steady(rows: list[dict[str, Any]], workload: str, variant: str, iterations: int) -> float:
    start = max(1, int(iterations * 0.8) + 1)
    by_repeat: dict[int, list[float]] = {}
    for row in rows:
        if row["workload"] != workload or row["variant"] != variant or row["iteration"] < start:
            continue
        by_repeat.setdefault(row["repeat"], []).append(row["total_ms"])
    return median([median(values) for values in by_repeat.values()])


def build_summary(rows: list[dict[str, Any]], iterations: int, first_n: int) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "schema": "node-v8-native-first-iterations.v1",
        "description": (
            "Fresh Node/V8 process runs over native JavaScript workloads. Iteration 1 includes vm.Script "
            "source compile or cachedData load plus execution. Steady is the median of each repeat's last "
            "20% iterations, then the median across repeats."
        ),
        "workloads": {},
    }
    for workload, display in WORKLOADS:
        item: dict[str, Any] = {"display": display, "variants": {}}
        for variant, label, _color, _marker, _style in VARIANTS:
            first_values = series_for(rows, workload, variant, first_n)
            all_values = series_for(rows, workload, variant, iterations)
            rejected = sum(
                1 for row in rows if row["workload"] == workload and row["variant"] == variant and row["cached_data_rejected"]
            )
            item["variants"][variant] = {
                "label": label,
                "first_iteration_median_ms": first_values[0],
                "first_10_median_ms": first_values,
                "steady_median_ms": repeat_steady(rows, workload, variant, iterations),
                "cache_bytes": max(
                    [
                        row["cache_bytes"]
                        for row in rows
                        if row["workload"] == workload and row["variant"] == variant
                    ]
                    or [0]
                ),
                "cached_data_rejected_rows": rejected,
                "all_iteration_medians_ms": all_values,
            }
        source = item["variants"]["source"]
        trained = item["variants"]["cached-trained"]
        item["trained_vs_source"] = {
            "iteration_1_saved_pct": saved_pct(
                source["first_iteration_median_ms"], trained["first_iteration_median_ms"]
            ),
            "steady_saved_pct": saved_pct(source["steady_median_ms"], trained["steady_median_ms"]),
        }
        summary["workloads"][workload] = item
    return summary


def plot(rows: list[dict[str, Any]], summary: dict[str, Any], out: Path, repeats: int, iterations: int, first_n: int) -> None:
    fig, axes = plt.subplots(2, 3, figsize=(17.4, 10.0))
    axes_flat = list(axes.flat)
    x = list(range(1, first_n + 1))

    handles = []
    labels = []
    for index, (workload, display) in enumerate(WORKLOADS):
        ax = axes_flat[index]
        ymax = 0.0
        variant_values: dict[str, dict[str, float]] = {}
        for variant, label, color, marker, style in VARIANTS:
            values = series_for(rows, workload, variant, first_n)
            steady = repeat_steady(rows, workload, variant, iterations)
            line = ax.plot(
                x,
                values,
                color=color,
                marker=marker,
                linestyle=style,
                linewidth=2.0,
                markersize=4.5,
                label=label,
            )[0]
            ax.axhline(steady, color=color, linestyle="--", linewidth=1.15, alpha=0.72)
            ymax = max(ymax, max(values), steady)
            variant_values[variant] = {"iter1": values[0], "steady": steady}
            if index == 0:
                handles.append(line)
                labels.append(label)

        trained_change = summary["workloads"][workload]["trained_vs_source"]
        box = (
            "iter-1 medians (ms):\n"
            f"  source   {variant_values['source']['iter1']:6.2f}\n"
            f"  cache    {variant_values['cached-cold']['iter1']:6.2f}\n"
            f"  trained  {variant_values['cached-trained']['iter1']:6.2f}\n\n"
            "steady medians (ms):\n"
            f"  source   {variant_values['source']['steady']:6.2f}\n"
            f"  cache    {variant_values['cached-cold']['steady']:6.2f}\n"
            f"  trained  {variant_values['cached-trained']['steady']:6.2f}\n"
            f"trained saved: i1 {trained_change['iteration_1_saved_pct']:4.1f}%, "
            f"steady {trained_change['steady_saved_pct']:4.1f}%"
        )
        ax.text(
            0.98,
            0.94,
            box,
            transform=ax.transAxes,
            ha="right",
            va="top",
            fontsize=8.4,
            family="monospace",
            bbox={"boxstyle": "round,pad=0.32", "facecolor": "white", "edgecolor": "#9A9A9A", "alpha": 0.92},
        )
        ax.set_title(display, fontsize=12.5)
        ax.set_xlabel("iteration")
        ax.set_ylabel("per-iteration latency (ms)")
        ax.set_xticks(x)
        ax.grid(True, alpha=0.24)
        ax.set_ylim(bottom=0, top=ymax * 1.20 if ymax else 1.0)

    axes_flat[-1].axis("off")
    steady_proxy = plt.Line2D([0], [0], color="#666666", linestyle="--", linewidth=1.2)
    handles.append(steady_proxy)
    labels.append("same-color dashed steady median")
    fig.legend(handles, labels, loc="upper center", ncol=4, frameon=False, bbox_to_anchor=(0.5, 0.94))
    fig.suptitle(
        "Node/V8 native first 10 iterations - source load vs V8 cachedData",
        fontsize=16,
        fontweight="bold",
        y=0.985,
    )
    fig.text(
        0.5,
        0.952,
        f"Per-iteration median across {repeats} fresh Node processes; steady = median of last 20% of {iterations} iterations",
        ha="center",
        fontsize=11.0,
    )
    fig.tight_layout(rect=[0.025, 0.025, 0.995, 0.91])
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=180, facecolor="white")
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--figure", type=Path, default=DEFAULT_FIGURE)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--repeats", type=int, default=15)
    parser.add_argument("--iterations", type=int, default=50)
    parser.add_argument("--first-n", type=int, default=10)
    parser.add_argument("--function-count", type=int, default=3200)
    parser.add_argument("--rounds", type=int, default=9000)
    parser.add_argument("--warmup-iterations", type=int, default=8)
    parser.add_argument("--seed", type=int, default=0x517CC1B7)
    parser.add_argument("--reuse", action="store_true")
    args = parser.parse_args()

    started = time.perf_counter()
    measurements = args.results / "measurements.csv"
    if args.reuse and measurements.exists():
        rows = read_rows(measurements)
    else:
        rows = run_matrix(args)
    summary = build_summary(rows, args.iterations, args.first_n)
    summary["config"] = {
        "repeats": args.repeats,
        "iterations": args.iterations,
        "first_n": args.first_n,
        "function_count": args.function_count,
        "rounds": args.rounds,
        "warmup_iterations": args.warmup_iterations,
        "results": str(args.results),
        "runner": str(RUNNER),
        "elapsed_s": time.perf_counter() - started,
    }
    plot(rows, summary, args.figure, args.repeats, args.iterations, args.first_n)
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.figure}")
    print(f"wrote {args.summary}")
    print(f"wrote {measurements}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
