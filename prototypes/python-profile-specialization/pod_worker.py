#!/usr/bin/env python3
"""Long-lived worker used to measure cold, warm, and hot pod requests."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import handler


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark", choices=sorted(handler.ROUTES), required=True)
    parser.add_argument("--artifact", type=Path)
    args = parser.parse_args()

    artifact = None
    if args.artifact:
        artifact = handler.load_artifact(args.artifact)
        artifact_benchmark = getattr(artifact, "BENCHMARK", None)
        if artifact_benchmark != args.benchmark:
            raise SystemExit(f"artifact benchmark {artifact_benchmark!r} does not match {args.benchmark!r}")

    print(json.dumps({"ready": True, "usedArtifact": artifact is not None}), flush=True)

    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        request = json.loads(line)
        command = request.get("command")
        if command == "stop":
            break
        if command != "invoke":
            print(json.dumps({"error": f"unknown command {command!r}"}), flush=True)
            continue

        requests = int(request["requests"])
        seed = int(request["seed"])
        start = time.perf_counter()
        if artifact is None:
            checksum, _route_counts = handler.run_generic(args.benchmark, requests, seed)
            used_artifact = False
        else:
            checksum = int(artifact.run(requests, seed))
            used_artifact = True
        work_ms = (time.perf_counter() - start) * 1000.0
        print(
            json.dumps(
                {
                    "benchmark": args.benchmark,
                    "requests": requests,
                    "seed": seed,
                    "workMs": work_ms,
                    "checksum": checksum,
                    "usedArtifact": used_artifact,
                },
                sort_keys=True,
            ),
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
