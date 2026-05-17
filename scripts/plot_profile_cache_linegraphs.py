#!/usr/bin/env python3
"""Render line graphs for baseline vs saved/AOT/profile-cache runs."""

from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
COLORS = {
    "baseline": "#202020",
    "saved": "#0072B2",
    "go-nopgo": "#202020",
    "go-pgo-5": "#0072B2",
    "go-pgo-10": "#009E73",
    "go-openfaas-nopgo": "#202020",
    "go-openfaas-pgo-5": "#0072B2",
    "go-openfaas-pgo-10": "#009E73",
    "llvm-baseline": "#202020",
    "llvm-pgo-5": "#0072B2",
    "llvm-pgo-10": "#009E73",
    "julia-baseline": "#202020",
    "julia-sysimage5": "#0072B2",
    "julia-sysimage10": "#009E73",
    "dotnet-il": "#202020",
    "dotnet-r2r": "#0072B2",
    "dotnet-nativeaot": "#009E73",
}
DISPLAY = {
    "baseline": "Baseline",
    "saved": "Saved artifact",
    "go-nopgo": "No PGO",
    "go-pgo-5": "AOT PGO, 5 profiles",
    "go-pgo-10": "AOT PGO, 10 profiles",
    "go-openfaas-nopgo": "OpenFaaS no PGO",
    "go-openfaas-pgo-5": "OpenFaaS AOT PGO, 5 profiles",
    "go-openfaas-pgo-10": "OpenFaaS AOT PGO, 10 profiles",
    "llvm-baseline": "Baseline",
    "llvm-pgo-5": "AOT PGO, 5 profiles",
    "llvm-pgo-10": "AOT PGO, 10 profiles",
    "julia-baseline": "Baseline",
    "julia-sysimage5": "AOT sysimage, 5 profiles",
    "julia-sysimage10": "AOT sysimage, 10 profiles",
    "dotnet-il": ".NET IL baseline",
    "dotnet-r2r": "ReadyToRun",
    "dotnet-nativeaot": "NativeAOT",
}
BENCHMARKS_5 = ["dacapo-lusearch", "dacapo-h2", "dacapo-eclipse", "dacapo-jython", "dacapo-fop"]
PYTHON_BENCHMARKS = ["dacapo-lusearch", "dacapo-h2", "dacapo-eclipse"]
LINE_RE = re.compile(
    r"scenario=(?P<scenario>\S+)\s+iterations=(?P<iterations>\d+)\s+"
    r"result=(?P<result>\d+)\s+elapsed_ms=(?P<elapsed_ms>[0-9.]+)\s+"
    r"per_invocation_ns=(?P<per_invocation_ns>[0-9.]+)"
)


def bench_short(name: str) -> str:
    return name.removeprefix("dacapo-")


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = (len(ordered) - 1) * p / 100.0
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    if lower == upper:
        return ordered[lower]
    weight = index - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def ewma(values: list[float], alpha: float = 0.10) -> list[float]:
    out: list[float] = []
    current = 0.0
    for index, value in enumerate(values):
        current = value if index == 0 else alpha * value + (1.0 - alpha) * current
        out.append(current)
    return out


def summary(values: list[float]) -> dict[str, float | int]:
    return {
        "n": len(values),
        "mean_ms": statistics.fmean(values) if values else 0.0,
        "p50_ms": percentile(values, 50),
        "p95_ms": percentile(values, 95),
        "min_ms": min(values) if values else 0.0,
        "max_ms": max(values) if values else 0.0,
    }


def usable(row: dict[str, str]) -> bool:
    try:
        status = int(row.get("status") or "0")
    except ValueError:
        return False
    return 200 <= status < 400 and not row.get("error")


def parse_trace_csv(path: Path, latency_column: str = "http_latency_ms") -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for row in read_csv_rows(path):
        if not usable(row):
            continue
        try:
            rows.append(
                {
                    "invocation": int(row["invocation"]),
                    "latency_ms": float(row[latency_column]),
                    "churn": row.get("churn") == "1",
                    "checksum": row.get("checksum", ""),
                }
            )
        except (KeyError, ValueError):
            continue
    return rows


def reject_emulation_source(path: Path, domain: str) -> None:
    normalized = str(path)
    blocked = ("openwhisk-level", "emulation", "demo")
    if any(token in normalized for token in blocked):
        raise SystemExit(
            f"refusing {domain} non-measurement source: {path}. "
            "Use a real OpenFaaS run directory instead."
        )


def draw_trace_panels(
    panels: list[tuple[str, dict[str, list[dict[str, Any]]]]],
    out: Path,
    title: str,
    summary_path: Path,
    schema: str,
    metadata: dict[str, Any] | None = None,
    raw_alpha: float = 0.20,
    ewma_alpha: float = 0.10,
) -> dict[str, Any]:
    panels = [
        (benchmark, {label: rows for label, rows in series.items() if rows})
        for benchmark, series in panels
        if any(series.values())
    ]
    if not panels:
        raise SystemExit(f"no real trace rows available for {out}")
    fig, axes = plt.subplots(len(panels), 1, figsize=(13.6, max(3.2, 3.0 * len(panels))), sharex=True)
    if len(panels) == 1:
        axes = [axes]
    result: dict[str, Any] = {"schema": schema, "benchmarks": {}}
    if metadata:
        result["metadata"] = metadata

    for ax, (benchmark, series) in zip(axes, panels):
        churn_points = sorted(
            {
                row["invocation"]
                for rows in series.values()
                for row in rows
                if row.get("churn")
            }
        )
        for index, point in enumerate(churn_points):
            ax.axvline(
                point,
                color="#6f6f6f",
                linestyle="--",
                linewidth=0.8,
                alpha=0.35,
                label="restart" if index == 0 else "_nolegend_",
                zorder=1,
            )

        bench_summary: dict[str, Any] = {}
        for label, rows in series.items():
            x = [row["invocation"] for row in rows]
            y = [row["latency_ms"] for row in rows]
            color = COLORS.get(label, "#334155")
            ax.plot(x, y, color=color, linewidth=0.8, alpha=raw_alpha, label=f"{DISPLAY.get(label, label)} raw")
            if y:
                ax.plot(x, ewma(y, ewma_alpha), color=color, linewidth=2.2, label=DISPLAY.get(label, label))
            bench_summary[label] = summary(y)

        baseline_key = next((key for key in series if "baseline" in key or key.endswith("nopgo") or key.endswith("il")), None)
        if baseline_key and baseline_key in bench_summary:
            base = float(bench_summary[baseline_key]["p50_ms"])
            for label, current in bench_summary.items():
                p50 = float(current["p50_ms"])
                current["p50_saved_pct"] = ((base - p50) / base * 100.0) if base > 0 else 0.0

        result["benchmarks"][benchmark] = bench_summary
        ax.set_title(bench_short(benchmark), loc="left", fontsize=11, fontweight="bold")
        ax.set_ylabel("latency (ms)")
        ax.set_ylim(bottom=0)
        ax.grid(axis="y", color="#e0e0e0", linewidth=0.6)
        ax.xaxis.set_major_locator(ticker.MaxNLocator(integer=True, nbins=10))

    axes[0].legend(loc="upper right", ncol=4, fontsize=8.5, framealpha=0.94)
    axes[-1].set_xlabel("Invocation / request index")
    fig.suptitle(title, fontsize=15, fontweight="bold", y=0.998)
    fig.tight_layout(rect=(0, 0, 1, 0.975))
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=170, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    summary_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    print(f"wrote {summary_path}")
    return result


def python_linegraphs(input_dir: Path, out_dir: Path) -> None:
    panels = []
    for benchmark in PYTHON_BENCHMARKS:
        short = bench_short(benchmark)
        baseline = parse_trace_csv(input_dir / f"openwhisk-{short}-baseline.csv")
        saved_all = parse_trace_csv(input_dir / f"openwhisk-{short}-saved.csv")
        checksums = {row["invocation"]: row["checksum"] for row in baseline if row["checksum"]}
        saved = [
            row
            for row in saved_all
            if not row["checksum"] or row["checksum"] == checksums.get(row["invocation"])
        ]
        panels.append((benchmark, {"baseline": baseline, "saved": saved}))
    draw_trace_panels(
        panels,
        out_dir / "python-openfaas-openwhisk-all-linegraphs.png",
        "Python OpenFaaS Redis specialization: baseline vs saved artifact",
        out_dir / "python-openfaas-openwhisk-all-linegraphs-summary.json",
        "python-openfaas-openwhisk-linegraphs.v1",
    )


def go_linegraphs(results_root: Path, out_dir: Path) -> None:
    panels = []
    budget: dict[str, dict[int, dict[str, float | int]]] = {}
    label_to_profiles = {"go-nopgo": 0, "go-pgo-5": 5, "go-pgo-10": 10}
    for benchmark_dir in sorted(path for path in results_root.iterdir() if path.is_dir()):
        benchmark = benchmark_dir.name
        series: dict[str, list[dict[str, Any]]] = {}
        for csv_path in sorted(benchmark_dir.glob("go-*.csv")):
            for row in read_csv_rows(csv_path):
                try:
                    label = row["label"]
                    item = {"invocation": int(row["iteration"]), "latency_ms": float(row["wall_ms"])}
                except (KeyError, ValueError):
                    continue
                series.setdefault(label, []).append(item)
        for rows in series.values():
            rows.sort(key=lambda item: item["invocation"])
        panels.append((benchmark, series))
        budget[benchmark] = {
            label_to_profiles[label]: summary([row["latency_ms"] for row in rows])
            for label, rows in series.items()
            if label in label_to_profiles
        }

    draw_trace_panels(
        panels,
        out_dir / "go-pgo-profile-cache-all-dacapo-linegraphs.png",
        "Go AOT PGO profile cache: cold invocation traces",
        out_dir / "go-pgo-profile-cache-all-dacapo-linegraphs-summary.json",
        "go-pgo-profile-cache-linegraphs.v1",
        raw_alpha=0.32,
        ewma_alpha=0.20,
    )
    plot_budget_lines(
        budget,
        out_dir / "go-pgo-profile-cache-budget-linegraph.png",
        out_dir / "go-pgo-profile-cache-budget-linegraph-summary.json",
        "Go AOT PGO profile budget line graph",
        "p50_ms",
        "p50 latency (ms)",
        "go-pgo-profile-budget-linegraph.v1",
    )


def go_openfaas_linegraphs(results_dir: Path, out_dir: Path) -> None:
    series = {
        "go-openfaas-nopgo": parse_trace_csv(results_dir / "go-openfaas-nopgo.csv", "latency_ms"),
        "go-openfaas-pgo-5": parse_trace_csv(results_dir / "go-openfaas-pgo-5.csv", "latency_ms"),
        "go-openfaas-pgo-10": parse_trace_csv(results_dir / "go-openfaas-pgo-10.csv", "latency_ms"),
    }
    draw_trace_panels(
        [("router", series)],
        out_dir / "go-openfaas-redis-pgo-linegraphs.png",
        "Real Go OpenFaaS Redis AOT PGO: baseline vs profile cache",
        out_dir / "go-openfaas-redis-pgo-linegraphs-summary.json",
        "go-openfaas-redis-pgo-linegraphs.v1",
        raw_alpha=0.30,
        ewma_alpha=0.12,
    )


def parse_llvm_result(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    match = LINE_RE.search(path.read_text(encoding="utf-8"))
    if not match:
        return None
    return {
        "elapsed_ms": float(match.group("elapsed_ms")),
        "per_invocation_ns": float(match.group("per_invocation_ns")),
        "iterations": int(match.group("iterations")),
        "result": int(match.group("result")),
    }


def llvm_linegraphs(results_root: Path, out_dir: Path) -> None:
    budget: dict[str, dict[int, dict[str, float | int]]] = {}
    for benchmark_dir in sorted(path for path in results_root.iterdir() if path.is_dir()):
        rows: dict[int, dict[str, float | int]] = {}
        for profiles, name in [(0, "baseline.txt"), (5, "pgo-5.txt"), (10, "pgo-10.txt")]:
            parsed = parse_llvm_result(benchmark_dir / name)
            if parsed:
                rows[profiles] = {
                    "elapsed_ms": parsed["elapsed_ms"],
                    "per_invocation_ns": parsed["per_invocation_ns"],
                    "n": 1,
                }
        budget[benchmark_dir.name] = rows
    plot_budget_lines(
        budget,
        out_dir / "llvm-aot-pgo-profile-budget-linegraph.png",
        out_dir / "llvm-aot-pgo-profile-budget-linegraph-summary.json",
        "LLVM AOT PGO profile budget line graph",
        "elapsed_ms",
        "elapsed time (ms)",
        "llvm-aot-pgo-profile-budget-linegraph.v1",
    )


def plot_budget_lines(
    budget: dict[str, dict[int, dict[str, float | int]]],
    out: Path,
    summary_path: Path,
    title: str,
    value_key: str,
    ylabel: str,
    schema: str,
) -> None:
    profile_counts = [0, 5, 10]
    fig, axes = plt.subplots(2, 1, figsize=(12.6, 8.0), sharex=True)
    result: dict[str, Any] = {"schema": schema, "benchmarks": {}}

    for benchmark, rows in sorted(budget.items()):
        values = [float(rows.get(count, {}).get(value_key, 0.0)) for count in profile_counts]
        if not any(values):
            continue
        baseline = values[0]
        improvement = [((baseline - value) / baseline * 100.0) if baseline > 0 else 0.0 for value in values]
        axes[0].plot(profile_counts, values, marker="o", linewidth=2.2, label=bench_short(benchmark))
        axes[1].plot(profile_counts, improvement, marker="o", linewidth=2.2, label=bench_short(benchmark))
        result["benchmarks"][benchmark] = {
            str(count): {**rows.get(count, {}), "improvement_pct": improvement[index]}
            for index, count in enumerate(profile_counts)
            if count in rows
        }

    axes[0].set_title(title, fontsize=15, fontweight="bold")
    axes[0].set_ylabel(ylabel)
    axes[1].set_ylabel("improvement vs baseline (%)")
    axes[1].set_xlabel("Imported profile count")
    axes[1].axhline(0, color="#8a8a8a", linewidth=0.8)
    axes[1].set_xticks(profile_counts)
    for ax in axes:
        ax.grid(axis="y", color="#e0e0e0", linewidth=0.6)
        ax.set_axisbelow(True)
    axes[0].legend(loc="upper right", ncol=3, fontsize=9, framealpha=0.94)
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=170, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    summary_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    print(f"wrote {summary_path}")


def julia_linegraphs(results_dir: Path, out_dir: Path) -> None:
    reject_emulation_source(results_dir, "Julia")
    panels = []
    for workload in ["lusearch", "h2", "eclipse"]:
        series = {
            "julia-baseline": parse_trace_csv(results_dir / f"{workload}-baseline.csv"),
            "julia-sysimage5": parse_trace_csv(results_dir / f"{workload}-sysimage5.csv"),
            "julia-sysimage10": parse_trace_csv(results_dir / f"{workload}-sysimage10.csv"),
        }
        panels.append((workload, series))
    available = [workload for workload, series in panels if any(series.values())]
    missing = [workload for workload in ["lusearch", "h2", "eclipse"] if workload not in available]
    draw_trace_panels(
        panels,
        out_dir / "julia-aot-profile-cache-openfaas-pod-churn-linegraphs.png",
        "Real Julia AOT sysimage profile cache: OpenFaaS pod-churn traces",
        out_dir / "julia-aot-profile-cache-openfaas-pod-churn-linegraphs-summary.json",
        "julia-aot-profile-cache-linegraphs.v1",
        metadata={
            "data_kind": "real-openfaas-measurement",
            "source_dir": str(results_dir),
            "available_workloads": available,
            "missing_real_workloads": missing,
            "note": "No emulation/smoke-test CSVs are included.",
        },
    )


def dotnet_linegraphs(results_dir: Path, out_dir: Path) -> None:
    reject_emulation_source(results_dir, ".NET")
    panels = []
    scenario_files = {
        "serve-hot": {
            "dotnet-il": "dotnet-openfaas-il-serve-hot-churn.csv",
            "dotnet-r2r": "dotnet-openfaas-r2r-serve-hot-churn.csv",
            "dotnet-nativeaot": "dotnet-openfaas-nativeaot-serve-hot-churn.csv",
        },
        "serve-mixed": {
            "dotnet-il": "dotnet-openfaas-il-serve-mixed-churn.csv",
            "dotnet-r2r": "dotnet-openfaas-r2r-serve-mixed-churn.csv",
            "dotnet-nativeaot": "dotnet-openfaas-nativeaot-serve-mixed-churn.csv",
        },
    }
    for scenario, files in scenario_files.items():
        series = {
            label: parse_trace_csv(results_dir / filename)
            for label, filename in files.items()
        }
        panels.append((scenario, series))
    available = [scenario for scenario, series in panels if any(series.values())]
    missing = [scenario for scenario in scenario_files if scenario not in available]
    draw_trace_panels(
        panels,
        out_dir / "dotnet-aot-openfaas-pod-churn-linegraphs.png",
        "Real .NET OpenFaaS pod-churn AOT comparison traces",
        out_dir / "dotnet-aot-openfaas-pod-churn-linegraphs-summary.json",
        "dotnet-aot-linegraphs.v1",
        metadata={
            "data_kind": "real-openfaas-measurement",
            "source_dir": str(results_dir),
            "available_scenarios": available,
            "missing_real_scenarios": missing,
            "note": "No emulation/smoke-test CSVs are included.",
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, default=ROOT / "docs/figures")
    parser.add_argument(
        "--python-dir",
        type=Path,
        default=ROOT / "prototypes/python-openfaas-redis-scale/.runs/20260516-195437/results",
    )
    parser.add_argument(
        "--go-results-root",
        type=Path,
        default=ROOT / "prototypes/go-pgo-cache-demo/results/20260516-go-all-dacapo-profile-graphs-v2",
    )
    parser.add_argument(
        "--go-openfaas-results-dir",
        type=Path,
        default=ROOT / "prototypes/go-openfaas-redis-pgo/.runs/20260511-171511/results",
    )
    parser.add_argument(
        "--llvm-results-root",
        type=Path,
        default=ROOT / "prototypes/llvm-aot-pgo/build/20260516-llvm-all-dacapo-profile-graphs-v2/results",
    )
    parser.add_argument(
        "--julia-results-dir",
        type=Path,
        default=ROOT / "prototypes/julia-openfaas-redis-precompile/.runs/real-julia-lusearch-openwhisk-20260516/results",
    )
    parser.add_argument(
        "--dotnet-results-dir",
        type=Path,
        default=ROOT / "prototypes/dotnet-openfaas-readytorun/.runs/real-dotnet-serve-hot-openwhisk-20260516/results",
    )
    args = parser.parse_args()

    reject_emulation_source(args.julia_results_dir, "Julia")
    reject_emulation_source(args.dotnet_results_dir, ".NET")

    python_linegraphs(args.python_dir, args.out_dir)
    go_linegraphs(args.go_results_root, args.out_dir)
    go_openfaas_linegraphs(args.go_openfaas_results_dir, args.out_dir)
    llvm_linegraphs(args.llvm_results_root, args.out_dir)
    julia_linegraphs(args.julia_results_dir, args.out_dir)
    dotnet_linegraphs(args.dotnet_results_dir, args.out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
