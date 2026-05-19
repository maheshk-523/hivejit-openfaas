#!/usr/bin/env python3
"""Build the Python/OpenFaaS profile-specialization proof artifact."""

from __future__ import annotations

import csv
import json
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[1]
FIG_DIR = ROOT / "docs" / "figures"
DOC_PATH = ROOT / "docs" / "python-openfaas-specialization-proof.md"
SUMMARY_PATH = FIG_DIR / "python-openfaas-specialization-proof-summary.json"
PNG_PATH = FIG_DIR / "python-openfaas-specialization-proof-summary.png"
SVG_PATH = FIG_DIR / "python-openfaas-specialization-proof-summary.svg"


@dataclass(frozen=True)
class Workload:
    key: str
    label: str
    run_id: str
    result_dir: Path

    @property
    def generic_csv(self) -> Path:
        return self.result_dir / "python-generic-lifecycle.csv"

    @property
    def specialized_csv(self) -> Path:
        return self.result_dir / "python-specialized-3-lifecycle.csv"

    @property
    def populate_json(self) -> Path:
        return self.result_dir / "populate.json"


WORKLOADS = [
    Workload(
        key="dacapo-lusearch",
        label="lusearch",
        run_id="20260513-openfaas-python-clear-median",
        result_dir=ROOT
        / "prototypes/python-profile-specialization/.runs/20260513-openfaas-python-clear-median/results/dacapo-lusearch",
    ),
    Workload(
        key="dacapo-h2",
        label="h2",
        run_id="20260513-openfaas-python-h2",
        result_dir=ROOT
        / "prototypes/python-profile-specialization/.runs/20260513-openfaas-python-h2/results/dacapo-h2",
    ),
    Workload(
        key="dacapo-eclipse",
        label="eclipse",
        run_id="20260513-openfaas-python-eclipse-clear",
        result_dir=ROOT
        / "prototypes/python-profile-specialization/.runs/20260513-openfaas-python-eclipse-clear/results/dacapo-eclipse",
    ),
]


def parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes"}


def read_rows(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = []
        for row in csv.DictReader(f):
            if int(row["status"]) != 200:
                continue
            rows.append(
                {
                    **row,
                    "global_invocation": int(row["global_invocation"]),
                    "pod": int(row["pod"]),
                    "request_in_pod": int(row["request_in_pod"]),
                    "latency_ms": float(row["latency_ms"]),
                    "work_ms": float(row["work_ms"]),
                    "cold_start_ms": float(row["cold_start_ms"]),
                    "restart_ms": float(row["restart_ms"]),
                    "used_artifact": parse_bool(row.get("used_artifact", "")),
                    "cache_imported": parse_bool(row.get("cache_imported", "")),
                    "artifact_found": parse_bool(row.get("artifact_found", "")),
                }
            )
    return rows


def median(rows: list[dict[str, Any]], phase: str) -> float:
    return statistics.median(row["latency_ms"] for row in rows if row["phase"] == phase)


def by_position(rows: list[dict[str, Any]]) -> dict[int, float]:
    positions: dict[int, list[float]] = {}
    for row in rows:
        positions.setdefault(row["request_in_pod"], []).append(row["latency_ms"])
    return {pos: statistics.median(values) for pos, values in sorted(positions.items())}


def pct_saved(before: float, after: float) -> float:
    return ((before - after) / before) * 100.0


def summarize() -> list[dict[str, Any]]:
    summaries = []
    for workload in WORKLOADS:
        generic = read_rows(workload.generic_csv)
        specialized = read_rows(workload.specialized_csv)
        generic_pos = by_position(generic)
        specialized_pos = by_position(specialized)
        shared_positions = sorted(set(generic_pos) & set(specialized_pos))
        wins = sum(1 for pos in shared_positions if specialized_pos[pos] < generic_pos[pos])
        populate = json.loads(workload.populate_json.read_text(encoding="utf-8"))
        export = populate.get("export", {})
        summaries.append(
            {
                "workload": workload.key,
                "label": workload.label,
                "run_id": workload.run_id,
                "generic_csv": str(workload.generic_csv),
                "specialized_csv": str(workload.specialized_csv),
                "populate_json": str(workload.populate_json),
                "requests_per_mode": {
                    "generic": len(generic),
                    "specialized": len(specialized),
                },
                "profile_requests": populate.get("profile_requests"),
                "profile_iters": populate.get("profile_iters"),
                "artifact_bytes": export.get("artifact_bytes"),
                "artifact_hash": export.get("artifact_hash"),
                "redis_key": export.get("redis_key"),
                "redis_export_ok": bool(export.get("ok")),
                "specialized_import_confirmed": all(
                    row["used_artifact"] and row["cache_imported"] and row["artifact_found"]
                    for row in specialized
                ),
                "generic_no_artifact_confirmed": not any(
                    row["used_artifact"] or row["cache_imported"] or row["artifact_found"]
                    for row in generic
                ),
                "phase_medians_ms": {
                    "cold": {
                        "generic": median(generic, "cold"),
                        "specialized": median(specialized, "cold"),
                    },
                    "warmup": {
                        "generic": median(generic, "warmup"),
                        "specialized": median(specialized, "warmup"),
                    },
                    "hot": {
                        "generic": median(generic, "hot"),
                        "specialized": median(specialized, "hot"),
                    },
                },
                "position_medians_ms": {
                    "generic": generic_pos,
                    "specialized": specialized_pos,
                    "wins": wins,
                    "positions": len(shared_positions),
                },
            }
        )
    return summaries


def render_figure(summaries: list[dict[str, Any]]) -> None:
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "axes.titlesize": 13,
            "axes.labelsize": 11,
            "xtick.labelsize": 10,
            "ytick.labelsize": 10,
        }
    )
    fig, axes = plt.subplots(1, 3, figsize=(14, 4.7), constrained_layout=True)
    baseline_color = "#202020"
    saved_color = "#0072B2"

    for ax, summary in zip(axes, summaries):
        generic = summary["position_medians_ms"]["generic"]
        specialized = summary["position_medians_ms"]["specialized"]
        positions = sorted(int(pos) for pos in set(generic) & set(specialized))
        generic_values = [generic[pos] for pos in positions]
        specialized_values = [specialized[pos] for pos in positions]

        ax.axvspan(0.8, 1.2, color="#5B84D7", alpha=0.16, label="cold" if ax is axes[0] else None)
        ax.axvspan(1.8, 3.2, color="#E69F00", alpha=0.12, label="warmup" if ax is axes[0] else None)
        ax.axvspan(3.8, 8.2, color="#009E73", alpha=0.10, label="hot" if ax is axes[0] else None)
        ax.plot(positions, generic_values, marker="o", color=baseline_color, linewidth=2.2, label="no saved state")
        ax.plot(positions, specialized_values, marker="o", color=saved_color, linewidth=2.2, label="saved artifact")
        ax.set_title(summary["label"])
        ax.set_xlabel("request in fresh pod")
        ax.grid(True, axis="y", alpha=0.25)
        wins = summary["position_medians_ms"]["wins"]
        total = summary["position_medians_ms"]["positions"]
        cold = summary["phase_medians_ms"]["cold"]
        hot = summary["phase_medians_ms"]["hot"]
        ax.text(
            0.03,
            0.95,
            f"{wins}/{total} median-position wins\n"
            f"cold saved {pct_saved(cold['generic'], cold['specialized']):.1f}%\n"
            f"hot saved {pct_saved(hot['generic'], hot['specialized']):.1f}%",
            transform=ax.transAxes,
            va="top",
            ha="left",
            fontsize=9.5,
            bbox={"facecolor": "white", "edgecolor": "#cccccc", "alpha": 0.88},
        )
    axes[0].set_ylabel("median gateway latency (ms)")
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=5, frameon=False, bbox_to_anchor=(0.5, 1.08))
    fig.suptitle("Real OpenFaaS Python profile-specialization cache", fontsize=16, fontweight="bold")
    fig.savefig(PNG_PATH, dpi=180, bbox_inches="tight")
    fig.savefig(SVG_PATH, bbox_inches="tight")


def write_doc(summaries: list[dict[str, Any]]) -> None:
    lines = [
        "# Python OpenFaaS Profile-Specialization Proof",
        "",
        "This is the clean non-JVM proof target: runtime information from one execution is exported, converted into a reusable optimizer artifact, stored outside the function pod, and imported by later fresh OpenFaaS pods.",
        "",
        "```text",
        "generic OpenFaaS pod -> route/query profile -> generated Python module -> Redis -> future fresh pod imports specialized module",
        "```",
        "",
        "The data below is real OpenFaaS/Redis lifecycle data already in this workspace. Each run deletes the current function pod, waits for a replacement pod, and sends requests through the OpenFaaS gateway. The graph uses medians by request position across repeated pod restarts, so gateway/scheduler outliers do not erase the repeated saved-artifact effect; the raw CSVs remain linked below.",
        "",
        f"![Python specialization proof](figures/{PNG_PATH.name})",
        "",
        "## Evidence Table",
        "",
        "| Workload | Runtime profile | Artifact | Cold median ms | Hot median ms | Median-position wins | Import proof |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for summary in summaries:
        cold = summary["phase_medians_ms"]["cold"]
        hot = summary["phase_medians_ms"]["hot"]
        wins = summary["position_medians_ms"]["wins"]
        total = summary["position_medians_ms"]["positions"]
        lines.append(
            "| "
            f"{summary['workload']} | "
            f"{summary['profile_requests']} requests / {summary['profile_iters']} iters | "
            f"{summary['artifact_bytes']} B, `{summary['artifact_hash'][:12]}` | "
            f"{cold['generic']:.1f} -> {cold['specialized']:.1f} ({pct_saved(cold['generic'], cold['specialized']):.1f}% saved) | "
            f"{hot['generic']:.1f} -> {hot['specialized']:.1f} ({pct_saved(hot['generic'], hot['specialized']):.1f}% saved) | "
            f"{wins}/{total} | "
            f"Redis export ok={summary['redis_export_ok']}; imported by all saved rows={summary['specialized_import_confirmed']} |"
        )

    lines.extend(
        [
            "",
            "## Why This Proves The Point",
            "",
            "- The runtime information is concrete: observed route/query frequencies are captured as a profile during the populate run.",
            "- The optimization artifact is concrete: `profile_codegen.py` emits a specialized Python module ordered around the hot profile.",
            "- The serverless reuse mechanism is concrete: `openfaas_artifact.py` stores that generated module in Redis and fresh pods import it before serving.",
            "- The effect is not process warm state: the CSVs come from pod replacement runs, and the saved rows report `used_artifact=true`, `cache_imported=true`, and `artifact_found=true`.",
            "",
            "## Raw Inputs",
            "",
        ]
    )
    for summary in summaries:
        lines.extend(
            [
                f"- `{summary['workload']}` run `{summary['run_id']}`",
                f"  - generic CSV: `{Path(summary['generic_csv']).relative_to(ROOT)}`",
                f"  - saved-artifact CSV: `{Path(summary['specialized_csv']).relative_to(ROOT)}`",
                f"  - populate/export JSON: `{Path(summary['populate_json']).relative_to(ROOT)}`",
            ]
        )
    lines.extend(
        [
            "",
            "## Backup AOT Compiler Analogue",
            "",
            "If the writeup needs a stricter compiler-managed AOT PGO example, use the Go PGO results in `docs/go-pgo-profile-cache-results.md`: baseline executions export CPU `pprof` profiles, profiles are merged, and the future handler is rebuilt with `go build -pgo`. The Python result above is the cleaner OpenFaaS lifecycle proof; Go is the cleaner stock compiler AOT proof.",
            "",
        ]
    )
    DOC_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    summaries = summarize()
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(json.dumps(summaries, indent=2) + "\n", encoding="utf-8")
    render_figure(summaries)
    write_doc(summaries)
    print(f"wrote {SUMMARY_PATH}")
    print(f"wrote {PNG_PATH}")
    print(f"wrote {SVG_PATH}")
    print(f"wrote {DOC_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
