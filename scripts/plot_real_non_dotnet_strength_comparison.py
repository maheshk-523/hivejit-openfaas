#!/usr/bin/env python3
"""Compare real OpenFaaS non-.NET profile/AOT-cache candidates against .NET."""

from __future__ import annotations

import csv
import json
import statistics
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
WORKLOADS = ["lusearch", "h2", "fop", "jython", "eclipse"]
DISPLAY = {
    "lusearch": "lusearch",
    "h2": "h2",
    "fop": "fop",
    "jython": "jython",
    "eclipse": "eclipse",
}


def read_csv(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                status = int(row.get("status") or 0)
                if not (200 <= status < 400) or row.get("error", ""):
                    continue
                rows.append(
                    {
                        "invocation": int(row["invocation"]),
                        "position": int(row.get("invocation_in_segment") or row.get("request_in_pod") or row["invocation"]),
                        "latency_ms": float(row.get("http_latency_ms") or row.get("latency_ms")),
                    }
                )
            except (KeyError, TypeError, ValueError):
                continue
    if not rows:
        raise ValueError(f"no valid latency rows in {path}")
    return rows


def metrics(rows: list[dict[str, Any]]) -> dict[str, float]:
    cold = [row["latency_ms"] for row in rows if row["position"] == 1]
    hot = [row["latency_ms"] for row in rows if row["position"] >= 4]
    all_values = [row["latency_ms"] for row in rows]
    return {
        "cold_median_ms": statistics.median(cold),
        "hot_median_ms": statistics.median(hot),
        "all_median_ms": statistics.median(all_values),
        "trace_max_ms": max(all_values),
        "points": float(len(rows)),
    }


def saved_pct(before: float, after: float) -> float:
    return ((before - after) / before * 100.0) if before > 0 else 0.0


def compare_pair(baseline_path: Path, optimized_path: Path) -> dict[str, Any]:
    baseline = metrics(read_csv(baseline_path))
    optimized = metrics(read_csv(optimized_path))
    return {
        "baseline": baseline,
        "optimized": optimized,
        "cold_saved_pct": saved_pct(baseline["cold_median_ms"], optimized["cold_median_ms"]),
        "hot_saved_pct": saved_pct(baseline["hot_median_ms"], optimized["hot_median_ms"]),
        "all_saved_pct": saved_pct(baseline["all_median_ms"], optimized["all_median_ms"]),
        "trace_max_saved_pct": saved_pct(baseline["trace_max_ms"], optimized["trace_max_ms"]),
    }


def load_dotnet() -> dict[str, Any]:
    base = ROOT / "prototypes/dotnet-openfaas-readytorun/.runs/real-openfaas-pod-churn-dotnet-dacapo-20260517/results"
    out: dict[str, Any] = {}
    for workload in WORKLOADS:
        out[workload] = {
            "optimized_label": "ReadyToRun AOT",
            **compare_pair(
                base / f"dotnet-openfaas-il-{workload}-churn.csv",
                base / f"dotnet-openfaas-r2r-{workload}-churn.csv",
            ),
        }
    return out


def load_jax() -> dict[str, Any]:
    base = ROOT / "prototypes/jax-openfaas-redis-xla/.runs/real-jax-xla-openfaas-pod-churn-five-20260518/results"
    out: dict[str, Any] = {}
    for workload in WORKLOADS:
        prefix = f"dacapo-{workload}"
        out[workload] = {
            "optimized_label": "saved XLA artifact",
            **compare_pair(base / prefix / "baseline.csv", base / prefix / "redis-cache.csv"),
        }
    return out


def load_python() -> dict[str, Any]:
    base = ROOT / "prototypes/python-openfaas-redis-scale/.runs/real-python-openfaas-pod-churn-five-20260517/results"
    out: dict[str, Any] = {}
    for workload in WORKLOADS:
        out[workload] = {
            "optimized_label": "saved Redis artifact",
            **compare_pair(base / f"pod-churn-{workload}-baseline.csv", base / f"pod-churn-{workload}-saved.csv"),
        }
    return out


def load_go() -> dict[str, Any]:
    base = ROOT / "prototypes/go-openfaas-redis-pgo/.runs/real-go-openfaas-pod-churn-pgo-5-10-20260517/results"
    out: dict[str, Any] = {}
    variants = {
        "PGO, 5 profiles": "go-openfaas-pgo-5-pod-churn.csv",
        "PGO, 10 profiles": "go-openfaas-pgo-10-pod-churn.csv",
    }
    for workload in WORKLOADS:
        prefix = f"dacapo-{workload}"
        baseline = base / prefix / "go-openfaas-nopgo-pod-churn.csv"
        candidates: list[tuple[str, dict[str, Any]]] = []
        for label, filename in variants.items():
            result = compare_pair(baseline, base / prefix / filename)
            candidates.append((label, result))
        label, result = max(candidates, key=lambda item: item[1]["cold_saved_pct"])
        out[workload] = {"optimized_label": label, **result}
    return out


def build_summary() -> dict[str, Any]:
    return {
        "schema": "real-openfaas-profile-cache-strength-comparison.v1",
        "runs": {
            ".NET reference": {
                "domain": "C#/.NET",
                "mechanism": "ReadyToRun AOT control",
                "results": load_dotnet(),
            },
            "JAX/XLA": {
                "domain": "Python numerical compiler",
                "mechanism": "runtime shape/profile -> XLA artifact saved in Redis -> imported on fresh pod",
                "results": load_jax(),
            },
            "Python": {
                "domain": "CPython application specialization",
                "mechanism": "runtime profile -> Redis artifact -> imported on fresh pod",
                "results": load_python(),
            },
            "Go": {
                "domain": "Go compiler PGO",
                "mechanism": "5/10 runtime profiles -> PGO rebuild -> fresh pod execution",
                "results": load_go(),
            },
        },
    }


def values(summary: dict[str, Any], domain: str, metric: str) -> list[float]:
    results = summary["runs"][domain]["results"]
    return [results[workload][metric] for workload in WORKLOADS]


def plot(summary: dict[str, Any], out: Path) -> None:
    domains = [".NET reference", "JAX/XLA", "Python", "Go"]
    colors = {
        ".NET reference": "#202020",
        "JAX/XLA": "#0072B2",
        "Python": "#009E73",
        "Go": "#D55E00",
    }
    x = np.arange(len(WORKLOADS))
    width = 0.19

    fig, axes = plt.subplots(2, 1, figsize=(12.5, 8.3), sharex=True)
    panels = [
        ("cold_saved_pct", "fresh-pod request median saved (%)"),
        ("trace_max_saved_pct", "trace peak latency saved (%)"),
    ]
    for ax, (metric, ylabel) in zip(axes, panels):
        for index, domain in enumerate(domains):
            offset = (index - (len(domains) - 1) / 2) * width
            bars = ax.bar(
                x + offset,
                values(summary, domain, metric),
                width,
                label=domain,
                color=colors[domain],
                alpha=0.9,
            )
            for bar in bars:
                height = bar.get_height()
                if height < -0.5:
                    va = "top"
                    y = height - 2.0
                else:
                    va = "bottom"
                    y = height + 1.0
                ax.text(
                    bar.get_x() + bar.get_width() / 2,
                    y,
                    f"{height:.0f}",
                    ha="center",
                    va=va,
                    fontsize=7.2,
                )
        ax.axhline(0, color="#555555", linewidth=0.8)
        ax.set_ylabel(ylabel)
        ax.grid(True, axis="y", alpha=0.22)
        ax.set_ylim(
            min(-18.0, min(min(values(summary, domain, metric)) for domain in domains) - 6.0),
            max(70.0, max(max(values(summary, domain, metric)) for domain in domains) + 10.0),
        )
    axes[0].legend(loc="upper center", ncol=4, frameon=False, bbox_to_anchor=(0.5, 1.22))
    axes[-1].set_xticks(x, [DISPLAY[workload] for workload in WORKLOADS])
    fig.suptitle("Real OpenFaaS pod-churn profile/AOT-cache strength comparison", fontsize=15, fontweight="bold")
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def main() -> int:
    out = ROOT / "docs/figures/real-non-dotnet-openfaas-strength-comparison.png"
    summary_path = ROOT / "docs/figures/real-non-dotnet-openfaas-strength-comparison-summary.json"
    summary = build_summary()
    plot(summary, out)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    print(f"wrote {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
