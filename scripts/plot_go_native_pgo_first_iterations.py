#!/usr/bin/env python3
"""Build, run, and plot Go-native first-iteration PGO comparisons."""

from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import subprocess
import time
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "prototypes/go-native-pgo-first-iterations"
DEFAULT_RUN_ROOT = SOURCE_DIR / "results"
DEFAULT_FIGURE = ROOT / "docs/figures/go-native-pgo-first-10-iterations.png"
DEFAULT_SUMMARY = ROOT / "docs/figures/go-native-pgo-first-10-iterations-summary.json"

WORKLOADS = [
    ("router-dispatch", "Router dispatch"),
    ("json-codec", "JSON encode/decode"),
    ("template-render", "Template render"),
    ("regexp-scan", "Regexp scan"),
    ("gzip-hash", "Gzip compress+hash"),
]
MODES = [
    ("default", "Go default build", "#222222"),
    ("pgo", "Go build -pgo", "#0067D8"),
]


def run_env(run_dir: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["GOCACHE"] = str(run_dir / ".gocache")
    env.setdefault("GOTOOLCHAIN", "local")
    return env


def run_cmd(args: list[str], *, cwd: Path, env: dict[str, str], capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        env=env,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )


def build_and_collect(run_dir: Path, repeats: int, iterations: int, scale: int, profile_scale: int) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    build_dir = run_dir / "build"
    build_dir.mkdir(parents=True, exist_ok=True)
    env = run_env(run_dir)

    train_bin = build_dir / "go-native-train"
    default_bin = build_dir / "go-native-default"
    pgo_bin = build_dir / "go-native-pgo"
    profile = run_dir / "profiles" / "cpu.pprof"

    print("== gofmt ==")
    run_cmd(["gofmt", "-w", "main.go"], cwd=SOURCE_DIR, env=env)
    print("== build training binary ==")
    run_cmd(["go", "build", "-o", str(train_bin), "."], cwd=SOURCE_DIR, env=env)
    print("== collect CPU profile ==")
    run_cmd(
        [
            str(train_bin),
            "-iterations",
            str(iterations * 4),
            "-scale",
            str(profile_scale),
            "-profile-out",
            str(profile),
        ],
        cwd=SOURCE_DIR,
        env=env,
    )
    print("== build default binary ==")
    run_cmd(["go", "build", "-o", str(default_bin), "."], cwd=SOURCE_DIR, env=env)
    print("== build PGO binary ==")
    run_cmd(["go", "build", f"-pgo={profile}", "-o", str(pgo_bin), "."], cwd=SOURCE_DIR, env=env)

    for mode, _label, _color in MODES:
        with (run_dir / f"{mode}.csv").open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["mode", "run_id", "workload", "iteration", "elapsed_ms", "checksum", "go_version"])

    for run_id in range(1, repeats + 1):
        for mode, binary in (("default", default_bin), ("pgo", pgo_bin)):
            print(f"== run {mode} repeat {run_id}/{repeats} ==")
            completed = run_cmd(
                [
                    str(binary),
                    "-iterations",
                    str(iterations),
                    "-scale",
                    str(scale),
                    "-seed",
                    str(1000 + run_id),
                    "-csv",
                ],
                cwd=SOURCE_DIR,
                env=env,
                capture=True,
            )
            rows = list(csv.DictReader(completed.stdout.splitlines()))
            with (run_dir / f"{mode}.csv").open("a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                for row in rows:
                    writer.writerow(
                        [
                            mode,
                            run_id,
                            row["workload"],
                            row["iteration"],
                            row["elapsed_ms"],
                            row["checksum"],
                            row["go_version"],
                        ]
                    )


def read_rows(run_dir: Path, mode: str) -> list[dict[str, Any]]:
    path = run_dir / f"{mode}.csv"
    with path.open(newline="", encoding="utf-8") as f:
        return [
            {
                "mode": row["mode"],
                "run_id": int(row["run_id"]),
                "workload": row["workload"],
                "iteration": int(row["iteration"]),
                "elapsed_ms": float(row["elapsed_ms"]),
                "go_version": row["go_version"],
            }
            for row in csv.DictReader(f)
        ]


def medians_by_iteration(rows: list[dict[str, Any]]) -> dict[int, float]:
    grouped: dict[int, list[float]] = {}
    for row in rows:
        grouped.setdefault(row["iteration"], []).append(row["elapsed_ms"])
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


def load_datasets(run_dir: Path) -> dict[str, dict[str, Any]]:
    rows_by_mode = {mode: read_rows(run_dir, mode) for mode, _label, _color in MODES}
    datasets: dict[str, dict[str, Any]] = {}
    for workload, _display in WORKLOADS:
        datasets[workload] = {}
        for mode, _label, _color in MODES:
            rows = [row for row in rows_by_mode[mode] if row["workload"] == workload]
            medians = medians_by_iteration(rows)
            datasets[workload][mode] = {
                "rows": rows,
                "medians": medians,
                "iteration_counts": {
                    iteration: sum(1 for row in rows if row["iteration"] == iteration)
                    for iteration in sorted(medians)
                },
                "iter1_median_ms": medians.get(1, 0.0),
                "steady_median_ms": steady_value(medians),
            }
    return datasets


def render(run_dir: Path, figure: Path, summary_path: Path) -> None:
    datasets = load_datasets(run_dir)
    fig, axes = plt.subplots(2, 3, figsize=(18, 10.4))
    flat_axes = list(axes.ravel())
    summary: dict[str, Any] = {
        "schema": "go-native-pgo-first-iterations.v1",
        "source": str(run_dir),
        "panels": {},
    }

    repeat_count = 0
    for rows in datasets[WORKLOADS[0][0]]["default"]["rows"]:
        repeat_count = max(repeat_count, rows["run_id"])

    for panel_index, (workload, display) in enumerate(WORKLOADS):
        ax = flat_axes[panel_index]
        max_y = 0.0
        for mode, label, color in MODES:
            medians = datasets[workload][mode]["medians"]
            xs = sorted(medians)
            ys = [medians[x] for x in xs]
            max_y = max(max_y, max(ys, default=0.0))
            ax.plot(xs, ys, marker="o", linewidth=2.4, markersize=5.5, color=color, label=label)

        default_steady = datasets[workload]["default"]["steady_median_ms"]
        pgo_steady = datasets[workload]["pgo"]["steady_median_ms"]
        if default_steady > 0:
            ax.axhline(
                default_steady,
                color="#222222",
                linestyle="--",
                linewidth=1.5,
                alpha=0.70,
                label=f"steady default = {default_steady:.2f} ms",
            )
        if pgo_steady > 0:
            ax.axhline(
                pgo_steady,
                color="#0067D8",
                linestyle=":",
                linewidth=2.0,
                label=f"steady PGO = {pgo_steady:.2f} ms",
            )

        default_iter1 = datasets[workload]["default"]["iter1_median_ms"]
        pgo_iter1 = datasets[workload]["pgo"]["iter1_median_ms"]
        steady_change = pct_saved(default_steady, pgo_steady)
        annotation = (
            "iter-1 medians (ms):\n"
            f"default       {default_iter1:8.2f}\n"
            f"pgo           {pgo_iter1:8.2f}\n"
            "steady medians:\n"
            f"default       {default_steady:8.2f}\n"
            f"pgo           {pgo_steady:8.2f}\n"
            f"pgo change    {steady_change:7.1f}%"
        )
        ax.text(
            0.66,
            0.40,
            annotation,
            transform=ax.transAxes,
            ha="left",
            va="top",
            family="monospace",
            fontsize=8.6,
            bbox={"facecolor": "white", "edgecolor": "#b8b8b8", "alpha": 0.88},
        )

        ax.set_title(display, fontsize=12.5)
        ax.set_xlabel("iteration")
        ax.set_ylabel("per-iter latency (ms)")
        ax.set_xticks(range(1, max(datasets[workload]["default"]["medians"] or {1: 0}) + 1))
        ax.set_ylim(bottom=0, top=max_y * 1.14 if max_y > 0 else 1)
        ax.grid(True, alpha=0.22)
        ax.legend(loc="upper right", fontsize=8.6, frameon=True)

        summary["panels"][workload] = {
            mode: {
                "iteration_counts": datasets[workload][mode]["iteration_counts"],
                "iteration_medians_ms": datasets[workload][mode]["medians"],
                "iteration_1_median_ms": datasets[workload][mode]["iter1_median_ms"],
                "steady_median_ms": datasets[workload][mode]["steady_median_ms"],
            }
            for mode, _label, _color in MODES
        }
        summary["panels"][workload]["comparison"] = {
            "iteration_1_pgo_saved_percent": pct_saved(default_iter1, pgo_iter1),
            "steady_pgo_saved_percent": pct_saved(
                datasets[workload]["default"]["steady_median_ms"],
                datasets[workload]["pgo"]["steady_median_ms"],
            ),
        }

    flat_axes[-1].axis("off")
    fig.suptitle("First 10 iterations - Go default build vs Go PGO", fontsize=17, fontweight="bold", y=0.985)
    fig.text(
        0.5,
        0.952,
        f"Per-iteration median across repeat fresh runs (default: {repeat_count}, PGO: {repeat_count}).",
        ha="center",
        fontsize=11,
    )
    fig.tight_layout(rect=(0.025, 0.045, 0.995, 0.935))

    figure.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(figure, dpi=180, facecolor="white")
    plt.close(fig)

    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def latest_run_dir() -> Path:
    return DEFAULT_RUN_ROOT / f"{time.strftime('%Y%m%d-%H%M%S')}-go-native-pgo-first-iterations"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--repeats", type=int, default=15)
    parser.add_argument("--iterations", type=int, default=10)
    parser.add_argument("--scale", type=int, default=1)
    parser.add_argument("--profile-scale", type=int, default=2)
    parser.add_argument("--skip-collect", action="store_true")
    parser.add_argument("--figure", type=Path, default=DEFAULT_FIGURE)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    args = parser.parse_args()

    run_dir = (args.run_dir or latest_run_dir()).resolve()
    if not args.skip_collect:
        if run_dir.exists() and any(run_dir.iterdir()):
            raise SystemExit(f"refusing to overwrite non-empty run dir: {run_dir}")
        build_and_collect(run_dir, args.repeats, args.iterations, args.scale, args.profile_scale)
    render(run_dir, args.figure, args.summary)
    print(f"wrote {args.figure}")
    print(f"wrote {args.summary}")
    print(f"source run: {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
