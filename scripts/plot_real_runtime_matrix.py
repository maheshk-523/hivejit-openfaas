#!/usr/bin/env python3
"""Plot real Julia/.NET baseline vs optimized runtime matrix summaries."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt


WORKLOADS = ["lusearch", "h2", "fop", "jython", "eclipse"]


def load_series(summary_path: Path) -> dict[str, dict[str, float]]:
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    series: dict[str, dict[str, float]] = {}
    for row in summary["table"]:
        series.setdefault(row["mode"], {})[row["workload"]] = row["medianProcessWallMs"]
    return series


def plot(summary_path: Path, out_path: Path, title: str, mode_order: list[str], labels: dict[str, str]) -> None:
    series = load_series(summary_path)
    fig, ax = plt.subplots(figsize=(10.5, 5.8))
    xs = list(range(len(WORKLOADS)))
    for mode in mode_order:
        if mode not in series:
            continue
        ys = [series[mode].get(workload, float("nan")) for workload in WORKLOADS]
        ax.plot(xs, ys, marker="o", linewidth=2.4, label=labels.get(mode, mode))
    ax.set_title(title, fontsize=15, weight="bold")
    ax.set_xticks(xs, WORKLOADS)
    ax.set_ylabel("median fresh-process wall time (ms)")
    ax.grid(True, axis="y", alpha=0.25)
    ax.legend(loc="best", frameon=False)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=180)
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dotnet-summary", required=True)
    parser.add_argument("--julia-summary", required=True)
    parser.add_argument("--out-dir", default="docs/figures")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    plot(
        Path(args.dotnet_summary),
        out_dir / "real-dotnet-dacapo-aot-runtime-linegraph.png",
        "Real .NET DaCapo-shaped workloads: baseline vs runtime-info/JIT and AOT",
        ["il-baseline", "dynamic-pgo", "r2r-aot"],
        {
            "il-baseline": "IL baseline",
            "dynamic-pgo": "Dynamic PGO",
            "r2r-aot": "ReadyToRun AOT",
        },
    )
    plot(
        Path(args.julia_summary),
        out_dir / "real-julia-dacapo-sysimage-runtime-linegraph.png",
        "Real Julia DaCapo-shaped workloads: baseline vs runtime-profile sysimages",
        ["baseline", "sysimage5", "sysimage10"],
        {
            "baseline": "baseline",
            "sysimage5": "sysimage, 5 profiles",
            "sysimage10": "sysimage, 10 profiles",
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
