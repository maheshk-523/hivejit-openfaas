#!/usr/bin/env python3
"""Run real .NET measurements for DaCapo-named workload shapes.

This script does not synthesize results. It publishes the project, launches the
published app as a fresh process for each sample, and records the JSON emitted by
the executable plus the outer process wall time.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PROJECT = ROOT / "ProfileCacheDotNet.csproj"
DEFAULT_WORKLOADS = ["lusearch", "h2", "fop", "jython", "eclipse"]


def rid() -> str:
    system = platform.system()
    machine = platform.machine().lower()
    if system == "Darwin" and machine == "arm64":
        return "osx-arm64"
    if system == "Darwin" and machine in {"x86_64", "amd64"}:
        return "osx-x64"
    if system == "Linux" and machine in {"aarch64", "arm64"}:
        return "linux-arm64"
    if system == "Linux" and machine in {"x86_64", "amd64"}:
        return "linux-x64"
    raise SystemExit(f"unable to infer RID for {system}-{machine}; pass --rid")


def run(cmd: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=ROOT, env=env, text=True, capture_output=True, check=True)


def publish(dotnet: str, out_dir: Path, publish_r2r: bool, target_rid: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        dotnet,
        "publish",
        str(PROJECT),
        "-c",
        "Release",
        "-o",
        str(out_dir),
        "-p:UseAppHost=false",
        f"-p:PublishReadyToRun={'true' if publish_r2r else 'false'}",
    ]
    if publish_r2r:
        cmd.extend(["-r", target_rid, "--self-contained", "false"])
    run(cmd)


def measure(
    dotnet: str,
    dll: Path,
    mode: str,
    workload: str,
    invocations: int,
    iterations: int,
    repeat: int,
) -> dict[str, object]:
    env = os.environ.copy()
    env.setdefault("DOTNET_CLI_HOME", "/private/tmp/dotnet-home")
    env.setdefault("DOTNET_SKIP_FIRST_TIME_EXPERIENCE", "1")
    if mode == "il-baseline":
        env["DOTNET_ReadyToRun"] = "0"
        env["DOTNET_TieredPGO"] = "0"
    elif mode == "dynamic-pgo":
        env["DOTNET_ReadyToRun"] = "0"
        env["DOTNET_TieredPGO"] = "1"
        env["DOTNET_TC_QuickJitForLoops"] = "1"
    elif mode == "r2r-aot":
        env["DOTNET_TieredPGO"] = "0"
    else:
        raise ValueError(mode)

    cmd = [
        dotnet,
        str(dll),
        "--scenario",
        workload,
        "--invocations",
        str(invocations),
        "--iterations",
        str(iterations),
        "--json",
    ]
    started = time.perf_counter()
    completed = run(cmd, env=env)
    wall_ms = (time.perf_counter() - started) * 1000.0
    payload = json.loads(completed.stdout)
    payload.update(
        {
            "mode": mode,
            "workload": workload,
            "repeat": repeat,
            "processWallMs": wall_ms,
            "command": " ".join(cmd),
            "runtimeInfoDriven": mode == "dynamic-pgo",
            "aotArtifact": mode == "r2r-aot",
        }
    )
    return payload


def median(rows: list[dict[str, object]], field: str) -> float:
    return float(statistics.median(float(row[field]) for row in rows))


def summarize(rows: list[dict[str, object]]) -> dict[str, object]:
    groups: dict[tuple[str, str], list[dict[str, object]]] = {}
    for row in rows:
        groups.setdefault((str(row["workload"]), str(row["mode"])), []).append(row)

    table = []
    by_workload: dict[str, dict[str, dict[str, float]]] = {}
    for (workload, mode), items in sorted(groups.items()):
        entry = {
            "workload": workload,
            "mode": mode,
            "samples": len(items),
            "medianProcessWallMs": median(items, "processWallMs"),
            "medianElapsedMs": median(items, "ElapsedMs"),
            "medianP50Ms": median(items, "InvocationP50Ms"),
            "medianP95Ms": median(items, "InvocationP95Ms"),
        }
        table.append(entry)
        by_workload.setdefault(workload, {})[mode] = entry

    comparisons = []
    for workload, modes in sorted(by_workload.items()):
        baseline = modes.get("il-baseline")
        if not baseline:
            continue
        for candidate in ("dynamic-pgo", "r2r-aot"):
            other = modes.get(candidate)
            if not other:
                continue
            base = baseline["medianProcessWallMs"]
            cand = other["medianProcessWallMs"]
            comparisons.append(
                {
                    "workload": workload,
                    "baseline": "il-baseline",
                    "candidate": candidate,
                    "baselineMedianProcessWallMs": base,
                    "candidateMedianProcessWallMs": cand,
                    "savedPct": (base - cand) * 100.0 / base if base else 0.0,
                }
            )

    best_aot = max(
        (c for c in comparisons if c["candidate"] == "r2r-aot"),
        key=lambda c: c["savedPct"],
        default=None,
    )
    best_runtime_pgo = max(
        (c for c in comparisons if c["candidate"] == "dynamic-pgo"),
        key=lambda c: c["savedPct"],
        default=None,
    )
    return {
        "generatedAtUnix": time.time(),
        "note": "Rows are real process executions. dynamic-pgo uses runtime information inside the process; r2r-aot is an AOT artifact but is not custom-profile-guided unless a MIBC file is supplied separately.",
        "table": table,
        "comparisons": comparisons,
        "bestAotRun": best_aot,
        "bestRuntimeInfoRun": best_runtime_pgo,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dotnet", default=os.environ.get("DOTNET_BIN", "dotnet"))
    parser.add_argument("--rid", default=os.environ.get("RID") or rid())
    parser.add_argument("--workloads", nargs="+", default=DEFAULT_WORKLOADS)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--invocations", type=int, default=10)
    parser.add_argument("--iterations", type=int, default=220000)
    parser.add_argument("--out-dir", default=str(ROOT / "results" / "dacapo-matrix"))
    parser.add_argument("--no-build", action="store_true")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    build_dir = ROOT / "build" / "dacapo-matrix"
    il_dir = build_dir / "il"
    r2r_dir = build_dir / "r2r"
    out_dir.mkdir(parents=True, exist_ok=True)

    if not args.no_build:
        publish(args.dotnet, il_dir, publish_r2r=False, target_rid=args.rid)
        publish(args.dotnet, r2r_dir, publish_r2r=True, target_rid=args.rid)

    il_dll = il_dir / "ProfileCacheDotNet.dll"
    r2r_dll = r2r_dir / "ProfileCacheDotNet.dll"
    rows = []
    jsonl_path = out_dir / "real-dotnet-dacapo-matrix.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as fh:
        for workload in args.workloads:
            workload = "fop" if workload == "fopo" else workload
            for mode, dll in (("il-baseline", il_dll), ("dynamic-pgo", il_dll), ("r2r-aot", r2r_dll)):
                for repeat in range(1, args.repeats + 1):
                    row = measure(args.dotnet, dll, mode, workload, args.invocations, args.iterations, repeat)
                    rows.append(row)
                    fh.write(json.dumps(row, sort_keys=True) + "\n")
                    fh.flush()
                    print(
                        f"{workload:8s} {mode:12s} repeat={repeat} "
                        f"wall_ms={row['processWallMs']:.1f} p50_ms={row['InvocationP50Ms']:.3f}",
                        file=sys.stderr,
                    )

    summary = summarize(rows)
    summary_path = out_dir / "real-dotnet-dacapo-matrix-summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
