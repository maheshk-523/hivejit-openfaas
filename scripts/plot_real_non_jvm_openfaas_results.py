#!/usr/bin/env python3
"""Plot real non-JVM OpenFaaS pod-churn results from CSV artifacts."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


WORKLOADS = ["lusearch", "h2", "fop", "jython", "eclipse"]


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def run(cmd: list[str]) -> None:
    root = repo_root()
    env = os.environ.copy()
    env.setdefault("MPLBACKEND", "Agg")
    env.setdefault("MPLCONFIGDIR", str(root / ".matplotlib-cache"))
    env.setdefault("XDG_CACHE_HOME", str(root / ".cache"))
    (root / ".matplotlib-cache").mkdir(exist_ok=True)
    (root / ".cache").mkdir(exist_ok=True)
    subprocess.run(cmd, check=True, env=env)


def panel_arg(workload: str, series: list[tuple[str, Path]]) -> str:
    return f"{workload}:" + ",".join(f"{label}={path}" for label, path in series)


def existing_series(series: list[tuple[str, Path]]) -> list[tuple[str, Path]]:
    return [(label, path) for label, path in series if path.exists()]


def plot_domain(
    root: Path,
    name: str,
    title: str,
    domain_dir: Path,
    series_by_workload: dict[str, list[tuple[str, Path]]],
    yscale: str,
) -> dict[str, Any]:
    panels = []
    missing: dict[str, list[str]] = {}
    for workload in WORKLOADS:
        requested = series_by_workload[workload]
        present = existing_series(requested)
        if len(present) < 2:
            missing[workload] = [str(path) for _label, path in requested if not path.exists()]
            continue
        panels.append(panel_arg(workload, present))

    out_dir = root / "docs" / "figures"
    invocation_png = out_dir / f"real-{name}-openfaas-pod-churn-invocation-traces.png"
    invocation_summary = out_dir / f"real-{name}-openfaas-pod-churn-invocation-traces-summary.json"
    median_png = out_dir / f"real-{name}-openfaas-pod-churn-position-medians.png"
    median_summary = out_dir / f"real-{name}-openfaas-pod-churn-position-medians-summary.json"

    if panels:
        run(
            [
                sys.executable,
                str(root / "scripts" / "plot_pod_churn_invocation_traces.py"),
                *sum([["--panel", panel] for panel in panels], []),
                "--out",
                str(invocation_png),
                "--summary",
                str(invocation_summary),
                "--title",
                title,
                "--yscale",
                yscale,
            ]
        )
        run(
            [
                sys.executable,
                str(root / "scripts" / "plot_pod_churn_position_medians.py"),
                *sum([["--panel", panel] for panel in panels], []),
                "--out",
                str(median_png),
                "--summary",
                str(median_summary),
                "--title",
                title.replace("traces", "median-position traces"),
            ]
        )

    return {
        "name": name,
        "source": str(domain_dir),
        "panels": panels,
        "missing": missing,
        "invocation_png": str(invocation_png) if panels else "",
        "invocation_summary": str(invocation_summary) if panels else "",
        "position_medians_png": str(median_png) if panels else "",
        "position_medians_summary": str(median_summary) if panels else "",
    }


def main() -> int:
    root = repo_root()
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dotnet-results",
        default=str(
            root
            / "prototypes/dotnet-openfaas-readytorun/.runs/"
            / "real-openfaas-pod-churn-dotnet-dacapo-20260517/results"
        ),
    )
    parser.add_argument(
        "--dotnet-nativeaot-results",
        default="",
        help="Optional separate .NET run directory containing nativeaot churn CSVs.",
    )
    parser.add_argument(
        "--julia-results",
        default=str(
            root
            / "prototypes/julia-openfaas-redis-precompile/.runs/"
            / "real-openfaas-pod-churn-julia-dacapo-workload-aot2-20260517/results"
        ),
    )
    parser.add_argument(
        "--jax-results",
        default=str(
            root
            / "prototypes/jax-openfaas-redis-xla/.runs/"
            / "real-jax-xla-openfaas-pod-churn-five-20260518/results"
        ),
    )
    parser.add_argument("--out", default=str(root / "docs/figures/real-non-jvm-openfaas-pod-churn-summary.json"))
    args = parser.parse_args()

    dotnet = Path(args.dotnet_results)
    dotnet_nativeaot = Path(args.dotnet_nativeaot_results) if args.dotnet_nativeaot_results else dotnet
    julia = Path(args.julia_results)
    jax = Path(args.jax_results)

    domains = [
        plot_domain(
            root,
            "dotnet",
            "Real .NET OpenFaaS pod-churn traces",
            dotnet,
            {
                workload: [
                    ("il", dotnet / f"dotnet-openfaas-il-{workload}-churn.csv"),
                    ("r2r", dotnet / f"dotnet-openfaas-r2r-{workload}-churn.csv"),
                    ("nativeaot", dotnet_nativeaot / f"dotnet-openfaas-nativeaot-{workload}-churn.csv"),
                ]
                for workload in WORKLOADS
            },
            "linear",
        ),
        plot_domain(
            root,
            "julia",
            "Real Julia OpenFaaS pod-churn traces",
            julia,
            {
                workload: [
                    ("baseline", julia / f"{workload}-baseline.csv"),
                    ("sysimage5", julia / f"{workload}-sysimage5.csv"),
                    ("sysimage10", julia / f"{workload}-sysimage10.csv"),
                ]
                for workload in WORKLOADS
            },
            "linear",
        ),
    ]

    jax_series = {}
    for workload in WORKLOADS:
        signature = f"dacapo-{workload}"
        jax_series[workload] = [
            ("baseline", jax / signature / "baseline.csv"),
            ("redis-cache", jax / signature / "redis-cache.csv"),
        ]
    domains.append(
        plot_domain(
            root,
            "jax-xla",
            "Real JAX/XLA OpenFaaS pod-churn traces",
            jax,
            jax_series,
            "log",
        )
    )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({"schema": "real-non-jvm-openfaas-pod-churn.v1", "domains": domains}, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
