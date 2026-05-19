#!/usr/bin/env python3
"""Serverless-style Python handler with profile-guided specialization support."""

from __future__ import annotations

import argparse
import importlib.util
import json
import platform
import time
from pathlib import Path
from types import ModuleType
from typing import Callable


MASK = (1 << 64) - 1

LUSEARCH_ROUTES = [
    {"name": "term", "threshold": 78, "rounds": 7, "salt": 0x9E3779B97F4A7C15},
    {"name": "phrase", "threshold": 91, "rounds": 11, "salt": 0xC2B2AE3D27D4EB4F},
    {"name": "wildcard", "threshold": 97, "rounds": 13, "salt": 0x165667B19E3779F9},
    {"name": "rank", "threshold": 100, "rounds": 17, "salt": 0x85EBCA77C2B2AE63},
]

H2_ROUTES = [
    {"name": "index_probe", "threshold": 70, "rounds": 6, "salt": 0xD6E8FEB86659FD93},
    {"name": "range_scan", "threshold": 88, "rounds": 10, "salt": 0xA5A3564E27F8866F},
    {"name": "join", "threshold": 97, "rounds": 14, "salt": 0x27D4EB2F165667C5},
    {"name": "aggregate", "threshold": 100, "rounds": 16, "salt": 0x94D049BB133111EB},
]

ECLIPSE_ROUTES = [
    {"name": "parse_unit", "threshold": 58, "rounds": 8, "salt": 0xBF58476D1CE4E5B9},
    {"name": "resolve_symbols", "threshold": 80, "rounds": 12, "salt": 0x94D049BB133111EB},
    {"name": "index_workspace", "threshold": 94, "rounds": 15, "salt": 0xD6E8FEB86659FD93},
    {"name": "refactor_plan", "threshold": 100, "rounds": 18, "salt": 0xA5A3564E27F8866F},
]

JYTHON_ROUTES = [
    {"name": "bytecode_dispatch", "threshold": 44, "rounds": 10, "salt": 0xDB4F0B9175AE2165},
    {"name": "call_site", "threshold": 69, "rounds": 13, "salt": 0xBBE0563303A4615F},
    {"name": "parser", "threshold": 88, "rounds": 15, "salt": 0xA0F2EC75A1FE1575},
    {"name": "object_graph", "threshold": 100, "rounds": 17, "salt": 0x89E182857D9ED689},
]

FOP_ROUTES = [
    {"name": "xml_parse", "threshold": 45, "rounds": 12, "salt": 0xC13FA9A902A6328F},
    {"name": "layout", "threshold": 76, "rounds": 16, "salt": 0x91E10DA5C79E7B1D},
    {"name": "render", "threshold": 92, "rounds": 18, "salt": 0xD1B54A32D192ED03},
    {"name": "regex", "threshold": 100, "rounds": 14, "salt": 0xABC98388FB8FAC03},
]

ROUTES = {
    "dacapo-lusearch": LUSEARCH_ROUTES,
    "dacapo-h2": H2_ROUTES,
    "dacapo-eclipse": ECLIPSE_ROUTES,
    "dacapo-jython": JYTHON_ROUTES,
    "dacapo-fop": FOP_ROUTES,
}


def mix64(value: int) -> int:
    value &= MASK
    value ^= value >> 33
    value = (value * 0xFF51AFD7ED558CCD) & MASK
    value ^= value >> 33
    value = (value * 0xC4CEB9FE1A85EC53) & MASK
    value ^= value >> 33
    return value & MASK


def choose_route(routes: list[dict[str, int | str]], index: int, state: int) -> dict[str, int | str]:
    ticket = mix64(index ^ state) % 100
    for route in routes:
        if ticket < int(route["threshold"]):
            return route
    return routes[-1]


def normalize_route(benchmark: str, route: dict[str, int | str]) -> dict[str, object]:
    """Normalize a dynamic route/query config as a generic framework would."""
    route_name = str(route["name"])
    normalized: dict[str, object] = {
        "name": route_name,
        "threshold": int(str(route["threshold"])),
        "rounds": int(str(route["rounds"])),
        "salt": int(str(route["salt"])),
    }
    if benchmark == "dacapo-lusearch":
        operations: dict[str, tuple[str, str, str]] = {
            "term": ("tokenize", "score", "rank"),
            "phrase": ("tokenize", "window", "score"),
            "wildcard": ("expand", "tokenize", "rank"),
            "rank": ("score", "boost", "rank"),
        }
        normalized["operations"] = tuple(str(step) for step in operations[route_name])
    elif benchmark == "dacapo-h2":
        query_plan: dict[str, tuple[tuple[str, int], ...]] = {
            "index_probe": (("eq_region", 2), ("amount_gt", 120)),
            "range_scan": (("amount_gt", 260), ("account_lt", 20)),
            "join": (("eq_region", 3), ("join_account", 7)),
            "aggregate": (("amount_gt", 80), ("group_region", 5)),
        }
        normalized["predicates"] = tuple((str(op), int(operand)) for op, operand in query_plan[route_name])
    elif benchmark == "dacapo-eclipse":
        phase_ops: dict[str, tuple[str, ...]] = {
            "parse_unit": ("scan", "parse", "fold"),
            "resolve_symbols": ("scan", "resolve", "fold"),
            "index_workspace": ("scan", "index", "resolve", "fold"),
            "refactor_plan": ("scan", "resolve", "rewrite", "fold"),
        }
        normalized["phases"] = tuple(str(phase) for phase in phase_ops[route_name])
    elif benchmark == "dacapo-jython":
        opcode_groups: dict[str, tuple[str, ...]] = {
            "bytecode_dispatch": ("load", "binary", "store"),
            "call_site": ("load", "dispatch", "guard", "return"),
            "parser": ("scan", "parse", "build"),
            "object_graph": ("lookup", "link", "trace"),
        }
        normalized["opcodes"] = tuple(str(opcode) for opcode in opcode_groups[route_name])
    elif benchmark == "dacapo-fop":
        pipeline_ops: dict[str, tuple[str, ...]] = {
            "xml_parse": ("token", "tree", "validate"),
            "layout": ("measure", "break", "place"),
            "render": ("paint", "encode", "flush"),
            "regex": ("match", "replace", "fold"),
        }
        normalized["pipeline"] = tuple(str(step) for step in pipeline_ops[route_name])
    else:
        raise ValueError(f"unknown benchmark {benchmark}")
    return normalized


def interpreted_lusearch(route: dict[str, int | str], state: int, index: int) -> int:
    route_name = str(route["name"])
    rounds = int(route["rounds"])
    salt = int(route["salt"])
    acc = state ^ ((index + 1) * salt)

    # Intentionally generic: each operation goes through the same interpreter
    # shape so the generated artifact has real dispatch and constant work to remove.
    operations = route.get("operations")
    if not operations:
        operations = {
            "term": ("tokenize", "score", "rank"),
            "phrase": ("tokenize", "window", "score"),
            "wildcard": ("expand", "tokenize", "rank"),
            "rank": ("score", "boost", "rank"),
        }[route_name]
    for step in operations:
        for round_index in range(rounds):
            probe = mix64(acc + salt + round_index + (index << 1))
            if step == "tokenize":
                acc ^= (probe >> 7) | (probe << 3)
            elif step == "window":
                acc = (acc + ((probe & 0xFFFF) * 17)) & MASK
            elif step == "expand":
                acc ^= mix64(probe ^ 0xABC98388FB8FAC03)
            elif step == "boost":
                acc = (acc * 3 + (probe & 0xFFF)) & MASK
            else:
                acc = (acc + mix64(probe ^ acc)) & MASK
    return acc & MASK


def interpreted_h2(route: dict[str, int | str], state: int, index: int) -> int:
    route_name = str(route["name"])
    rounds = int(route["rounds"])
    salt = int(route["salt"])
    rows = [
        {"account": (index + i) & 31, "region": i % 5, "amount": ((state >> (i % 11)) + i * 17) & 0x3FF}
        for i in range(24)
    ]
    predicates = route.get("predicates")
    if not predicates:
        predicates = {
            "index_probe": (("eq_region", 2), ("amount_gt", 120)),
            "range_scan": (("amount_gt", 260), ("account_lt", 20)),
            "join": (("eq_region", 3), ("join_account", 7)),
            "aggregate": (("amount_gt", 80), ("group_region", 5)),
        }[route_name]
    acc = state ^ salt
    for row in rows:
        amount = int(row["amount"])
        account = int(row["account"])
        region = int(row["region"])
        matched = True
        for op, operand in predicates:
            if op == "eq_region":
                matched = matched and region == operand
            elif op == "amount_gt":
                matched = matched and amount > operand
            elif op == "account_lt":
                matched = matched and account < operand
            elif op == "join_account":
                matched = matched and ((account ^ operand) & 7) == (index & 7)
            elif op == "group_region":
                matched = matched and ((region + operand + index) % 3) == 0
            else:
                matched = matched and True
            if not matched:
                break
        if matched:
            for round_index in range(rounds):
                acc ^= mix64(amount + account * 131 + region * 17 + salt + round_index)
                acc = ((acc << 5) | (acc >> 59)) & MASK
    return acc & MASK


def interpreted_eclipse(route: dict[str, int | str], state: int, index: int) -> int:
    route_name = str(route["name"])
    rounds = int(route["rounds"])
    salt = int(route["salt"])
    phases = route.get("phases")
    if not phases:
        phases = {
            "parse_unit": ("scan", "parse", "fold"),
            "resolve_symbols": ("scan", "resolve", "fold"),
            "index_workspace": ("scan", "index", "resolve", "fold"),
            "refactor_plan": ("scan", "resolve", "rewrite", "fold"),
        }[route_name]
    acc = state ^ salt ^ index
    for phase in phases:
        for round_index in range(rounds):
            value = mix64(acc + salt + round_index * 0x100000001B3)
            if phase == "scan":
                acc = (acc + (value & 0xFFFF)) & MASK
            elif phase == "parse":
                acc ^= (value << 11) & MASK
            elif phase == "resolve":
                acc = (acc ^ (value >> 9) ^ mix64(index + round_index)) & MASK
            elif phase == "index":
                acc = (acc * 5 + (value & 0x7FFF)) & MASK
            elif phase == "rewrite":
                acc ^= mix64(value ^ 0xD1B54A32D192ED03)
            else:
                acc = (acc + mix64(value ^ acc)) & MASK
    return acc & MASK


def interpreted_jython(route: dict[str, int | str], state: int, index: int) -> int:
    route_name = str(route["name"])
    rounds = int(route["rounds"])
    salt = int(route["salt"])
    opcodes = route.get("opcodes")
    if not opcodes:
        opcodes = {
            "bytecode_dispatch": ("load", "binary", "store"),
            "call_site": ("load", "dispatch", "guard", "return"),
            "parser": ("scan", "parse", "build"),
            "object_graph": ("lookup", "link", "trace"),
        }[route_name]
    stack = [state ^ salt, index, salt, state, index ^ salt, 0, 0, 0]
    sp = 0
    acc = state ^ salt ^ (index * 0x100000001B3)
    for opcode in opcodes:
        for round_index in range(rounds):
            probe = mix64(acc + stack[(sp - 1) & 7] + salt + round_index)
            if opcode == "load":
                stack[sp & 7] = probe
                sp += 1
            elif opcode == "binary":
                rhs = stack[(sp - 1) & 7]
                lhs = stack[(sp - 2) & 7]
                acc ^= (lhs + rhs + probe) & MASK
            elif opcode == "store":
                stack[(sp + 3) & 7] = acc ^ probe
                acc = (acc + mix64(stack[(sp + 3) & 7])) & MASK
            elif opcode == "dispatch":
                acc = mix64(acc ^ probe ^ stack[sp & 7])
            elif opcode == "guard":
                acc = (acc + probe) & MASK if (probe & 1) else (acc ^ mix64(probe + index))
            elif opcode == "return":
                acc ^= mix64(acc + stack[(sp - 1) & 7])
            elif opcode == "scan":
                acc = (acc + (probe & 0xFFFF)) & MASK
            elif opcode == "parse":
                acc ^= (probe << 7) & MASK
            elif opcode == "build":
                stack[sp & 7] = mix64(acc + probe)
                sp += 1
            elif opcode == "lookup":
                acc ^= mix64(stack[(probe >> 3) & 7] + probe)
            elif opcode == "link":
                acc = (acc * 5 + probe + stack[sp & 7]) & MASK
            elif opcode == "trace":
                acc ^= mix64(acc ^ probe ^ round_index)
            else:
                acc = mix64(acc + probe)
    return acc & MASK


def interpreted_fop(route: dict[str, int | str], state: int, index: int) -> int:
    route_name = str(route["name"])
    rounds = int(route["rounds"])
    salt = int(route["salt"])
    pipeline = route.get("pipeline")
    if not pipeline:
        pipeline = {
            "xml_parse": ("token", "tree", "validate"),
            "layout": ("measure", "break", "place"),
            "render": ("paint", "encode", "flush"),
            "regex": ("match", "replace", "fold"),
        }[route_name]
    acc = state ^ salt
    cursor = index & 0x3FF
    page = 1
    depth = 0
    for step in pipeline:
        for round_index in range(rounds):
            probe = mix64(acc + salt + round_index + (index << 2))
            if step == "token":
                depth += 1 if (probe & 3) else -1
                depth = max(depth, 0)
                acc ^= mix64(probe + depth)
            elif step == "tree":
                acc = (acc + ((depth + 1) * (probe & 0xFFFF))) & MASK
            elif step == "validate":
                acc ^= mix64(acc + probe + depth)
            elif step == "measure":
                cursor += 8 + (probe & 63)
                acc = (acc + cursor + page) & MASK
            elif step == "break":
                if cursor > 720:
                    page += 1
                    cursor = cursor % 89
                acc ^= mix64(page + cursor + probe)
            elif step == "place":
                acc = ((acc << 5) | (acc >> 59)) & MASK
                acc ^= mix64(cursor + round_index)
            elif step == "paint":
                acc = mix64(acc ^ ((round_index + 1) * 0x45D9F3B))
            elif step == "encode":
                acc = (acc + mix64(probe ^ page)) & MASK
            elif step == "flush":
                acc ^= mix64(acc + cursor + page)
            elif step == "match":
                acc ^= mix64(probe + 31) if (acc & 1) == 0 else mix64(probe + 17)
            elif step == "replace":
                acc = (acc * 3 + (probe & 0xFFF)) & MASK
            elif step == "fold":
                acc = (acc + mix64(probe ^ acc)) & MASK
            else:
                acc = mix64(acc + probe)
    return acc & MASK


INTERPRETERS: dict[str, Callable[[dict[str, int | str], int, int], int]] = {
    "dacapo-lusearch": interpreted_lusearch,
    "dacapo-h2": interpreted_h2,
    "dacapo-eclipse": interpreted_eclipse,
    "dacapo-jython": interpreted_jython,
    "dacapo-fop": interpreted_fop,
}


def run_generic(benchmark: str, requests: int, seed: int) -> tuple[int, dict[str, int]]:
    routes = ROUTES[benchmark]
    interpret = INTERPRETERS[benchmark]
    counts = {str(route["name"]): 0 for route in routes}
    state = (0x123456789ABCDEF0 ^ seed) & MASK
    for index in range(requests):
        route = choose_route(routes, index, state)
        route_name = str(route["name"])
        counts[route_name] += 1
        route = normalize_route(benchmark, route)
        state ^= interpret(route, state, index)
        state &= MASK
    return state, counts


def load_artifact(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("python_profile_specialized_artifact", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load specialization artifact: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_profile(path: Path, benchmark: str, requests: int, seed: int, checksum: int, route_counts: dict[str, int]) -> None:
    total = sum(route_counts.values()) or 1
    profile = {
        "schema": "python-profile-specialization.v1",
        "benchmark": benchmark,
        "requests": requests,
        "seed": seed,
        "checksum": checksum,
        "runtime": platform.python_version(),
        "generatedAtUnix": time.time(),
        "routeCounts": route_counts,
        "routeFractions": {route: count / total for route, count in route_counts.items()},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(profile, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark", choices=sorted(ROUTES), default="dacapo-lusearch")
    parser.add_argument("--requests", type=int, default=20000)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--profile-out", type=Path)
    parser.add_argument("--artifact", type=Path, help="generated specialization module to import")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.requests <= 0:
        raise SystemExit("--requests must be positive")

    start = time.perf_counter()
    used_artifact = False
    route_counts: dict[str, int] = {}
    if args.artifact:
        artifact = load_artifact(args.artifact)
        artifact_benchmark = getattr(artifact, "BENCHMARK", None)
        if artifact_benchmark != args.benchmark:
            raise SystemExit(f"artifact benchmark {artifact_benchmark!r} does not match {args.benchmark!r}")
        checksum = int(artifact.run(args.requests, args.seed))
        used_artifact = True
    else:
        checksum, route_counts = run_generic(args.benchmark, args.requests, args.seed)
    elapsed_ms = (time.perf_counter() - start) * 1000.0

    if args.profile_out:
        if used_artifact:
            checksum_for_profile, route_counts = run_generic(args.benchmark, args.requests, args.seed)
            if checksum_for_profile != checksum:
                raise SystemExit("specialized checksum differs from generic checksum during profile export")
        write_profile(args.profile_out, args.benchmark, args.requests, args.seed, checksum, route_counts)

    result = {
        "domain": "python-profile-specialization",
        "benchmark": args.benchmark,
        "requests": args.requests,
        "seed": args.seed,
        "usedArtifact": used_artifact,
        "workMs": elapsed_ms,
        "checksum": checksum,
    }
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        mode = "specialized" if used_artifact else "generic"
        print(f"{mode} benchmark={args.benchmark} requests={args.requests} work_ms={elapsed_ms:.3f} checksum={checksum}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
