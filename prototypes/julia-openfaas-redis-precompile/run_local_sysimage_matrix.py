#!/usr/bin/env python3
"""Run real Julia baseline vs PackageCompiler sysimage measurements.

The sysimage is built from runtime precompile information: the build step
executes the requested workloads N times through the same handler dispatch path,
then PackageCompiler records and imports those method specializations into the
sysimage used by the measured runs.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
HANDLER = ROOT / "handler.jl"
DEFAULT_JULIA = "/private/tmp/julia-1.10.4/Julia-1.10.app/Contents/Resources/julia/bin/julia"
DEFAULT_WORKLOADS = ["lusearch", "h2", "fop", "jython", "eclipse"]


def run(cmd: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=ROOT, env=env, text=True, capture_output=True, check=True)


def write_driver(path: Path) -> None:
    path.write_text(
        r'''
using JSON3

ENV["JULIA_BUILD_SYSIMAGE"] = "1"
include(ENV["HANDLER_PATH"])

function _percentile(values::Vector{Float64}, pct::Int)::Float64
    isempty(values) && return 0.0
    sorted = sort(values)
    idx = clamp(ceil(Int, pct / 100 * length(sorted)), 1, length(sorted))
    sorted[idx]
end

function _run(workload::String, size::Int, invocations::Int)
    times = Float64[]
    checksum = UInt64(0)
    total_start = time_ns()
    for i in 1:invocations
        one_start = time_ns()
        result = dispatch_workload(workload, size)
        push!(times, (time_ns() - one_start) / 1.0e6)
        checksum ⊻= UInt64(sizeof(JSON3.write(result)) + i)
    end
    elapsed_ms = (time_ns() - total_start) / 1.0e6
    JSON3.write(Dict{String,Any}(
        "domain" => "julia-packagecompiler-sysimage",
        "workload" => workload,
        "size" => size,
        "invocations" => invocations,
        "elapsedMs" => elapsed_ms,
        "p50Ms" => _percentile(times, 50),
        "p95Ms" => _percentile(times, 95),
        "invocationTimesMs" => times,
        "runtime" => string(VERSION),
        "checksum" => string(checksum),
    )) |> println
end

_run(ARGS[1], parse(Int, ARGS[2]), parse(Int, ARGS[3]))
'''.lstrip(),
        encoding="utf-8",
    )


def write_sysimage_builder(path: Path) -> None:
    path.write_text(
        r'''
using PackageCompiler

n_profiles = parse(Int, ENV["N_PROFILES"])
workloads = split(ENV["WORKLOADS"])
size = parse(Int, ENV["SIZE"])
exec_file = ENV["PRECOMPILE_EXEC_FILE"]
sysimage_path = ENV["SYSIMAGE_PATH"]

open(exec_file, "w") do io
    println(io, "ENV[\"JULIA_BUILD_SYSIMAGE\"] = \"1\"")
    println(io, "include(ENV[\"HANDLER_PATH\"])")
    println(io, "for _profile in 1:$n_profiles")
    println(io, "    for _workload in $(repr(workloads))")
    println(io, "        dispatch_workload(_workload, $size)")
    println(io, "    end")
    println(io, "end")
end

t0 = time()
create_sysimage(
    [:HTTP, :JSON3],
    sysimage_path = sysimage_path,
    precompile_execution_file = exec_file,
)
println("sysimage=$sysimage_path build_seconds=$(round(time() - t0; digits=1)) profiles=$n_profiles workloads=$(join(workloads, ","))")
'''.lstrip(),
        encoding="utf-8",
    )


def build_sysimage(julia: str, depot: str, out_dir: Path, workloads: list[str], size: int, profiles: int) -> Path:
    builder = out_dir / "build_sysimage_local.jl"
    precompile_exec = out_dir / f"precompile-{profiles}.jl"
    sysimage = out_dir / f"sysimage{profiles}.dylib"
    write_sysimage_builder(builder)
    env = os.environ.copy()
    env.update(
        {
            "JULIA_DEPOT_PATH": depot,
            "HANDLER_PATH": str(HANDLER),
            "N_PROFILES": str(profiles),
            "WORKLOADS": " ".join(workloads),
            "SIZE": str(size),
            "PRECOMPILE_EXEC_FILE": str(precompile_exec),
            "SYSIMAGE_PATH": str(sysimage),
        }
    )
    run([julia, "--startup-file=no", str(builder)], env)
    return sysimage


def measure(
    julia: str,
    depot: str,
    driver: Path,
    workload: str,
    size: int,
    invocations: int,
    repeat: int,
    mode: str,
    sysimage: Path | None,
) -> dict[str, object]:
    env = os.environ.copy()
    env.update({"JULIA_DEPOT_PATH": depot, "HANDLER_PATH": str(HANDLER)})
    cmd = [julia, "--startup-file=no"]
    if sysimage is not None:
        cmd.extend(["-J", str(sysimage)])
    cmd.extend([str(driver), workload, str(size), str(invocations)])
    started = time.perf_counter()
    completed = run(cmd, env)
    wall_ms = (time.perf_counter() - started) * 1000.0
    payload = json.loads(completed.stdout)
    payload.update(
        {
            "mode": mode,
            "repeat": repeat,
            "processWallMs": wall_ms,
            "runtimeInfoDriven": mode.startswith("sysimage"),
            "aotArtifact": mode.startswith("sysimage"),
            "command": " ".join(cmd),
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
            "medianElapsedMs": median(items, "elapsedMs"),
            "medianP50Ms": median(items, "p50Ms"),
            "medianP95Ms": median(items, "p95Ms"),
        }
        table.append(entry)
        by_workload.setdefault(workload, {})[mode] = entry

    comparisons = []
    for workload, modes in sorted(by_workload.items()):
        baseline = modes.get("baseline")
        if not baseline:
            continue
        for mode, other in sorted(modes.items()):
            if mode == "baseline":
                continue
            base = baseline["medianProcessWallMs"]
            cand = other["medianProcessWallMs"]
            comparisons.append(
                {
                    "workload": workload,
                    "baseline": "baseline",
                    "candidate": mode,
                    "baselineMedianProcessWallMs": base,
                    "candidateMedianProcessWallMs": cand,
                    "savedPct": (base - cand) * 100.0 / base if base else 0.0,
                }
            )

    best = max(comparisons, key=lambda c: c["savedPct"], default=None)
    return {
        "generatedAtUnix": time.time(),
        "note": "Rows are real fresh Julia process executions. sysimage modes are built from runtime precompile executions of the requested workloads.",
        "table": table,
        "comparisons": comparisons,
        "bestAotRun": best,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--julia", default=os.environ.get("JULIA_BIN", DEFAULT_JULIA))
    parser.add_argument("--depot", default=os.environ.get("JULIA_DEPOT_PATH", "/private/tmp/julia-depot"))
    parser.add_argument("--workloads", nargs="+", default=DEFAULT_WORKLOADS)
    parser.add_argument("--profiles", nargs="+", type=int, default=[5, 10])
    parser.add_argument("--repeats", type=int, default=4)
    parser.add_argument("--invocations", type=int, default=5)
    parser.add_argument("--size", type=int, default=1)
    parser.add_argument("--out-dir", default=str(ROOT / "results" / "local-sysimage-matrix"))
    parser.add_argument("--skip-build", action="store_true")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    driver = out_dir / "bench_driver.jl"
    write_driver(driver)

    workloads = ["fop" if workload == "fopo" else workload for workload in args.workloads]
    sysimages: dict[int, Path] = {}
    for profiles in args.profiles:
        sysimage = out_dir / f"sysimage{profiles}.dylib"
        if args.skip_build and sysimage.exists():
            sysimages[profiles] = sysimage
        else:
            sysimages[profiles] = build_sysimage(args.julia, args.depot, out_dir, workloads, args.size, profiles)

    rows = []
    jsonl_path = out_dir / "real-julia-sysimage-matrix.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as fh:
        for workload in workloads:
            modes: list[tuple[str, Path | None]] = [("baseline", None)]
            modes.extend((f"sysimage{profiles}", sysimages[profiles]) for profiles in args.profiles)
            for mode, sysimage in modes:
                for repeat in range(1, args.repeats + 1):
                    row = measure(args.julia, args.depot, driver, workload, args.size, args.invocations, repeat, mode, sysimage)
                    rows.append(row)
                    fh.write(json.dumps(row, sort_keys=True) + "\n")
                    fh.flush()
                    print(
                        f"{workload:8s} {mode:10s} repeat={repeat} "
                        f"wall_ms={row['processWallMs']:.1f} p50_ms={row['p50Ms']:.3f}",
                        file=sys.stderr,
                    )

    summary = summarize(rows)
    summary_path = out_dir / "real-julia-sysimage-matrix-summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
