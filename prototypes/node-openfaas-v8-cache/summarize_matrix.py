#!/usr/bin/env python3
"""Summarize real Node/V8 OpenFaaS pod-churn CSV pairs."""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from pathlib import Path
from typing import Any


DEFAULT_WORKLOADS = ["lusearch", "h2", "fop", "jython", "eclipse"]
TREATMENTS = {
    "baseline": "baseline",
    "v8-cached-data": "v8cache",
}


def as_float(value: str | None) -> float:
    try:
        return float(value or 0.0)
    except ValueError:
        return 0.0


def as_int(value: str | None) -> int:
    try:
        return int(value or 0)
    except ValueError:
        return 0


def read_rows(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    parsed = []
    for row in rows:
        status = as_int(row.get("status"))
        ok = 200 <= status < 400 and not row.get("error")
        parsed.append(
            {
                **row,
                "ok": ok,
                "status": status,
                "invocation": as_int(row.get("invocation")),
                "invocation_in_segment": as_int(row.get("invocation_in_segment") or row.get("request_in_pod")),
                "request_in_pod": as_int(row.get("request_in_pod")),
                "churn": str(row.get("churn", "")).lower() in {"1", "true", "yes"},
                "http_latency_ms": as_float(row.get("http_latency_ms")),
                "work_ms": as_float(row.get("work_ms")),
                "compile_ms": as_float(row.get("compile_ms")),
                "artifact_bytes": as_int(row.get("artifact_bytes")),
                "artifact_found": str(row.get("artifact_found", "")).lower() == "true",
                "cached_data_rejected": str(row.get("cached_data_rejected", "")).lower() == "true",
            }
        )
    return parsed


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


def stats(values: list[float]) -> dict[str, float]:
    return {
        "mean": statistics.fmean(values) if values else 0.0,
        "min": min(values) if values else 0.0,
        "p50": percentile(values, 50),
        "p95": percentile(values, 95),
        "p99": percentile(values, 99),
        "max": max(values) if values else 0.0,
    }


def phase_rows(rows: list[dict[str, Any]], phase: str) -> list[dict[str, Any]]:
    ok_rows = [row for row in rows if row["ok"]]
    if phase == "all":
        return ok_rows
    if phase == "cold":
        return [row for row in ok_rows if row["churn"] or row["invocation_in_segment"] == 1]
    if phase == "hot":
        return [row for row in ok_rows if row["invocation_in_segment"] >= 4]
    raise ValueError(phase)


def summarize_series(rows: list[dict[str, Any]], treatment: str) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "rows": len(rows),
        "ok": sum(1 for row in rows if row["ok"]),
        "statuses": {},
        "churn_invocations": [row["invocation"] for row in rows if row["churn"]],
        "request_in_pod_at_churn": [row["request_in_pod"] for row in rows if row["churn"]],
        "cached_data_rejected": sum(1 for row in rows if row["cached_data_rejected"]),
        "artifact_missing": sum(1 for row in rows if treatment != "baseline" and not row["artifact_found"]),
        "artifact_bytes": max((row["artifact_bytes"] for row in rows), default=0),
    }
    for row in rows:
        key = str(row["status"])
        summary["statuses"][key] = summary["statuses"].get(key, 0) + 1

    for phase in ("all", "cold", "hot"):
        selected = phase_rows(rows, phase)
        for metric in ("http_latency_ms", "work_ms", "compile_ms"):
            summary[f"{phase}_{metric}"] = stats([float(row[metric]) for row in selected])
    return summary


def saved_pct(before: float, after: float) -> float:
    return ((before - after) * 100.0 / before) if before else 0.0


def compare(base: dict[str, Any], cached: dict[str, Any]) -> dict[str, Any]:
    comparisons: dict[str, Any] = {}
    for phase in ("all", "cold", "hot"):
        for metric in ("http_latency_ms", "work_ms", "compile_ms"):
            key = f"{phase}_{metric}"
            comparisons[key] = {
                "p50_saved_ms": base[key]["p50"] - cached[key]["p50"],
                "p50_saved_pct": saved_pct(base[key]["p50"], cached[key]["p50"]),
                "p95_saved_ms": base[key]["p95"] - cached[key]["p95"],
                "p95_saved_pct": saved_pct(base[key]["p95"], cached[key]["p95"]),
            }
    return comparisons


def checksum_mismatches(baseline_rows: list[dict[str, Any]], cached_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    baseline = {row["invocation"]: row.get("checksum", "") for row in baseline_rows if row["ok"]}
    mismatches = []
    for row in cached_rows:
        if not row["ok"]:
            continue
        expected = baseline.get(row["invocation"])
        actual = row.get("checksum", "")
        if expected is not None and actual != expected:
            mismatches.append({"invocation": row["invocation"], "baseline": expected, "cached": actual})
    return mismatches


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--workloads", nargs="+", default=DEFAULT_WORKLOADS)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    result: dict[str, Any] = {
        "schema": "node-v8-openfaas-pod-churn-matrix.v1",
        "results": str(args.results),
        "workloads": {},
    }

    for workload in args.workloads:
        baseline_path = args.results / f"node-openfaas-{workload}-baseline-pod-churn.csv"
        cached_path = args.results / f"node-openfaas-{workload}-v8-cached-data-pod-churn.csv"
        baseline_rows = read_rows(baseline_path)
        cached_rows = read_rows(cached_path)
        baseline = summarize_series(baseline_rows, "baseline")
        cached = summarize_series(cached_rows, "v8-cached-data")
        result["workloads"][workload] = {
            "csv": {
                "baseline": str(baseline_path),
                "v8-cached-data": str(cached_path),
            },
            "series": {
                "baseline": baseline,
                "v8-cached-data": cached,
            },
            "comparisons": compare(baseline, cached),
            "checksum_mismatches_vs_baseline": checksum_mismatches(baseline_rows, cached_rows),
        }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
