#!/usr/bin/env python3
"""Render LLVM AOT PGO benchmark results from run_pgo.sh outputs."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np


LINE_RE = re.compile(
    r"scenario=(?P<scenario>\S+)\s+iterations=(?P<iterations>\d+)\s+"
    r"result=(?P<result>\d+)\s+elapsed_ms=(?P<elapsed_ms>[0-9.]+)\s+"
    r"per_invocation_ns=(?P<per_invocation_ns>[0-9.]+)"
)
COLORS = {
    "baseline": "#222222",
    "pgo-5": "#0072B2",
    "pgo-10": "#009E73",
}
DISPLAY = {
    "baseline": "Baseline",
    "pgo-5": "PGO, 5 profiles",
    "pgo-10": "PGO, 10 profiles",
}


def parse_result(path: Path, label: str) -> dict[str, Any] | None:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8").strip()
    match = LINE_RE.search(text)
    if not match:
        return None
    return {
        "label": label,
        "scenario": match.group("scenario"),
        "iterations": int(match.group("iterations")),
        "result": int(match.group("result")),
        "elapsed_ms": float(match.group("elapsed_ms")),
        "per_invocation_ns": float(match.group("per_invocation_ns")),
        "path": str(path),
    }


def collect(results_root: Path) -> dict[str, dict[str, dict[str, Any]]]:
    data: dict[str, dict[str, dict[str, Any]]] = {}
    for bench_dir in sorted(path for path in results_root.iterdir() if path.is_dir()):
        benchmark = bench_dir.name
        entries = {
            "baseline": parse_result(bench_dir / "baseline.txt", "baseline"),
            "pgo-5": parse_result(bench_dir / "pgo-5.txt", "pgo-5"),
            "pgo-10": parse_result(bench_dir / "pgo-10.txt", "pgo-10"),
        }
        data[benchmark] = {label: row for label, row in entries.items() if row is not None}
    return {benchmark: rows for benchmark, rows in data.items() if rows}


def summarize(data: dict[str, dict[str, dict[str, Any]]]) -> dict[str, Any]:
    summary: dict[str, Any] = {"schema": "llvm-aot-pgo-summary.v1", "benchmarks": {}}
    for benchmark, rows in data.items():
        baseline = rows.get("baseline", {}).get("elapsed_ms", 0.0)
        current: dict[str, Any] = {}
        for label, row in rows.items():
            elapsed = row["elapsed_ms"]
            current[label] = {
                "elapsed_ms": elapsed,
                "per_invocation_ns": row["per_invocation_ns"],
                "iterations": row["iterations"],
                "result": row["result"],
                "improvement_pct": ((baseline - elapsed) / baseline * 100.0) if baseline > 0 else 0.0,
            }
        summary["benchmarks"][benchmark] = current
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-root", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--summary", required=True, type=Path)
    parser.add_argument("--title", default="LLVM AOT PGO results across benchmark analogues")
    parser.add_argument("--dpi", type=int, default=170)
    args = parser.parse_args()

    data = collect(args.results_root)
    if not data:
        raise SystemExit(f"no benchmark result directories found under {args.results_root}")
    summary = summarize(data)

    benchmarks = sorted(data)
    labels = ["baseline", "pgo-5", "pgo-10"]
    x = np.arange(len(benchmarks))
    width = 0.24

    fig, axes = plt.subplots(2, 1, figsize=(12.4, 8.0), sharex=True)
    for index, label in enumerate(labels):
        offset = (index - 1) * width
        elapsed = [summary["benchmarks"][benchmark].get(label, {}).get("elapsed_ms", 0.0) for benchmark in benchmarks]
        improvement = [
            summary["benchmarks"][benchmark].get(label, {}).get("improvement_pct", 0.0)
            for benchmark in benchmarks
        ]
        axes[0].bar(x + offset, elapsed, width=width, color=COLORS[label], label=DISPLAY[label])
        axes[1].bar(x + offset, improvement, width=width, color=COLORS[label], label=DISPLAY[label])

    axes[0].set_title(args.title, fontsize=15, fontweight="bold")
    axes[0].set_ylabel("elapsed time (ms)")
    axes[1].set_ylabel("improvement vs baseline (%)")
    axes[1].set_xlabel("Benchmark")
    axes[1].set_xticks(x)
    axes[1].set_xticklabels([benchmark.removeprefix("dacapo-") for benchmark in benchmarks])
    axes[1].axhline(0, color="#8a8a8a", linewidth=0.8)
    for ax in axes:
        ax.grid(axis="y", color="#e0e0e0", linewidth=0.6)
        ax.set_axisbelow(True)
    axes[0].legend(loc="upper right", ncol=3, framealpha=0.94)
    plt.tight_layout()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.out, dpi=args.dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.out}")
    print(f"wrote {args.summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
