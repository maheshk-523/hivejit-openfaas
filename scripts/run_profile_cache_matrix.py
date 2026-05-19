#!/usr/bin/env python3
"""Run available profile-artifact cache prototypes and write one matrix result."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Domain:
    name: str
    command: list[str]
    tools: list[str]
    description: str


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def domains() -> list[Domain]:
    return [
        Domain(
            name="node-v8-artifact-cache",
            command=["node", "prototypes/node-v8-artifact-cache/bench.js", "--runs", "6"],
            tools=["node"],
            description="V8 cachedData export/import in fresh Node processes.",
        ),
        Domain(
            name="llvm-aot-pgo",
            command=["bash", "prototypes/llvm-aot-pgo/run_pgo.sh"],
            tools=["bash", "xcrun"],
            description="Clang instrumentation profile export and -fprofile-instr-use import.",
        ),
        Domain(
            name="go-pgo-serverless",
            command=["bash", "prototypes/go-pgo-serverless/run_pgo.sh"],
            tools=["bash", "go"],
            description="Go pprof export and go build -pgo import.",
        ),
        Domain(
            name="python-profile-specialization",
            command=["bash", "prototypes/python-profile-specialization/run_profile_cache.sh"],
            tools=["bash", "python3"],
            description="Python route/query profile export and generated specialization artifact import.",
        ),
        Domain(
            name="jax-xla-runtime-specialization",
            command=["bash", "prototypes/jax-xla-runtime-specialization/run_jax_xla.sh"],
            tools=["bash"],
            description="JAX tensor signature export and XLA persistent compilation cache import.",
        ),
        Domain(
            name="dotnet-readytorun-pgo",
            command=["bash", "prototypes/dotnet-readytorun-pgo/run_readytorun.sh"],
            tools=["bash", "dotnet"],
            description=".NET IL/JIT vs ReadyToRun and dynamic PGO comparison.",
        ),
        Domain(
            name="dotnet-static-pgo",
            command=["bash", "prototypes/dotnet-readytorun-pgo/run_static_pgo.sh"],
            tools=["bash", "dotnet", "dotnet-trace", "dotnet-pgo"],
            description=".NET nettrace to MIBC to ReadyToRunOptimizationData loop.",
        ),
    ]


def tail(text: str, max_chars: int = 4000) -> str:
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def run_domain(root: Path, domain: Domain, timeout_s: int) -> dict:
    missing = [tool for tool in domain.tools if shutil.which(tool) is None]
    if missing:
        return {
            "name": domain.name,
            "status": "skipped",
            "description": domain.description,
            "missingTools": missing,
        }

    started = time.perf_counter()
    try:
        completed = subprocess.run(
            domain.command,
            cwd=root,
            text=True,
            capture_output=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        elapsed = time.perf_counter() - started
        return {
            "name": domain.name,
            "status": "timeout",
            "description": domain.description,
            "elapsedSeconds": elapsed,
            "stdoutTail": tail(exc.stdout or ""),
            "stderrTail": tail(exc.stderr or ""),
        }

    elapsed = time.perf_counter() - started
    status = "ok" if completed.returncode == 0 else "failed"
    if completed.returncode == 2:
        status = "skipped"

    return {
        "name": domain.name,
        "status": status,
        "description": domain.description,
        "returnCode": completed.returncode,
        "elapsedSeconds": elapsed,
        "stdoutTail": tail(completed.stdout),
        "stderrTail": tail(completed.stderr),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=int, default=240, help="per-domain timeout in seconds")
    parser.add_argument("--only", action="append", default=[], help="domain name to run; repeatable")
    parser.add_argument(
        "--out",
        default="generated/profile-cache-matrix/last.json",
        help="matrix result path relative to repo root",
    )
    args = parser.parse_args()

    root = repo_root()
    selected = domains()
    if args.only:
        requested = set(args.only)
        selected = [domain for domain in selected if domain.name in requested]

    output = {
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "pattern": "Execution -> profile/artifact export -> profile/artifact import -> future execution",
        "domains": [],
    }

    for domain in selected:
        print(f"== {domain.name}")
        result = run_domain(root, domain, args.timeout)
        output["domains"].append(result)
        print(f"{domain.name}: {result['status']}")

    out_path = root / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out_path}")

    failed = [domain for domain in output["domains"] if domain["status"] in {"failed", "timeout"}]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
