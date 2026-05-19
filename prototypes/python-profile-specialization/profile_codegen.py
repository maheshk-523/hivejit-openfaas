#!/usr/bin/env python3
"""Generate a Python specialization artifact from exported runtime profiles."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


MASK = (1 << 64) - 1

ROUTE_SPECS: dict[str, list[dict[str, int | str]]] = {
    "dacapo-lusearch": [
        {"name": "term", "threshold": 78, "rounds": 7, "salt": 0x9E3779B97F4A7C15},
        {"name": "phrase", "threshold": 91, "rounds": 11, "salt": 0xC2B2AE3D27D4EB4F},
        {"name": "wildcard", "threshold": 97, "rounds": 13, "salt": 0x165667B19E3779F9},
        {"name": "rank", "threshold": 100, "rounds": 17, "salt": 0x85EBCA77C2B2AE63},
    ],
    "dacapo-h2": [
        {"name": "index_probe", "threshold": 70, "rounds": 6, "salt": 0xD6E8FEB86659FD93},
        {"name": "range_scan", "threshold": 88, "rounds": 10, "salt": 0xA5A3564E27F8866F},
        {"name": "join", "threshold": 97, "rounds": 14, "salt": 0x27D4EB2F165667C5},
        {"name": "aggregate", "threshold": 100, "rounds": 16, "salt": 0x94D049BB133111EB},
    ],
    "dacapo-eclipse": [
        {"name": "parse_unit", "threshold": 58, "rounds": 8, "salt": 0xBF58476D1CE4E5B9},
        {"name": "resolve_symbols", "threshold": 80, "rounds": 12, "salt": 0x94D049BB133111EB},
        {"name": "index_workspace", "threshold": 94, "rounds": 15, "salt": 0xD6E8FEB86659FD93},
        {"name": "refactor_plan", "threshold": 100, "rounds": 18, "salt": 0xA5A3564E27F8866F},
    ],
    "dacapo-jython": [
        {"name": "bytecode_dispatch", "threshold": 44, "rounds": 10, "salt": 0xDB4F0B9175AE2165},
        {"name": "call_site", "threshold": 69, "rounds": 13, "salt": 0xBBE0563303A4615F},
        {"name": "parser", "threshold": 88, "rounds": 15, "salt": 0xA0F2EC75A1FE1575},
        {"name": "object_graph", "threshold": 100, "rounds": 17, "salt": 0x89E182857D9ED689},
    ],
    "dacapo-fop": [
        {"name": "xml_parse", "threshold": 45, "rounds": 12, "salt": 0xC13FA9A902A6328F},
        {"name": "layout", "threshold": 76, "rounds": 16, "salt": 0x91E10DA5C79E7B1D},
        {"name": "render", "threshold": 92, "rounds": 18, "salt": 0xD1B54A32D192ED03},
        {"name": "regex", "threshold": 100, "rounds": 14, "salt": 0xABC98388FB8FAC03},
    ],
}


def load_profiles(paths: list[Path]) -> tuple[str, dict[str, int], int]:
    benchmark = ""
    counts: dict[str, int] = {}
    requests = 0
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema") != "python-profile-specialization.v1":
            raise ValueError(f"unsupported profile schema in {path}")
        current = str(data["benchmark"])
        if benchmark and current != benchmark:
            raise ValueError("all profiles must use the same benchmark")
        benchmark = current
        requests += int(data.get("requests", 0))
        for route, count in data.get("routeCounts", {}).items():
            counts[str(route)] = counts.get(str(route), 0) + int(count)
    if not benchmark:
        raise ValueError("no profiles loaded")
    return benchmark, counts, requests


def route_expression(route: dict[str, int | str], previous_threshold: int) -> str:
    threshold = int(route["threshold"])
    if previous_threshold == 0:
        return f"ticket < {threshold}"
    return f"{previous_threshold} <= ticket < {threshold}"


def render_route_block(benchmark: str, route: dict[str, int | str], indent: str) -> list[str]:
    name = str(route["name"])
    rounds = int(route["rounds"])
    salt = int(route["salt"])
    if benchmark == "dacapo-lusearch":
        return render_lusearch_route(name, rounds, salt, indent)
    if benchmark == "dacapo-h2":
        return render_h2_route(name, rounds, salt, indent)
    if benchmark == "dacapo-eclipse":
        return render_eclipse_route(name, rounds, salt, indent)
    if benchmark == "dacapo-jython":
        return render_jython_route(name, rounds, salt, indent)
    if benchmark == "dacapo-fop":
        return render_fop_route(name, rounds, salt, indent)
    raise ValueError(f"unknown benchmark {benchmark}")


def render_lusearch_route(name: str, rounds: int, salt: int, indent: str) -> list[str]:
    operations = {
        "term": ("tokenize", "score", "rank"),
        "phrase": ("tokenize", "window", "score"),
        "wildcard": ("expand", "tokenize", "rank"),
        "rank": ("score", "boost", "rank"),
    }[name]
    lines = [
        f"{indent}acc = state ^ (((index + 1) * {salt}) & MASK)",
    ]
    for step in operations:
        lines.append(f"{indent}for round_index in range({rounds}):")
        lines.append(f"{indent}    probe = mix64(acc + {salt} + round_index + (index << 1))")
        if step == "tokenize":
            lines.append(f"{indent}    acc ^= (probe >> 7) | ((probe << 3) & MASK)")
        elif step == "window":
            lines.append(f"{indent}    acc = (acc + ((probe & 0xFFFF) * 17)) & MASK")
        elif step == "expand":
            lines.append(f"{indent}    acc ^= mix64(probe ^ 0xABC98388FB8FAC03)")
        elif step == "boost":
            lines.append(f"{indent}    acc = (acc * 3 + (probe & 0xFFF)) & MASK")
        else:
            lines.append(f"{indent}    acc = (acc + mix64(probe ^ acc)) & MASK")
    lines.append(f"{indent}state ^= acc & MASK")
    lines.append(f"{indent}state &= MASK")
    return lines


def render_h2_route(name: str, rounds: int, salt: int, indent: str) -> list[str]:
    query_plan = {
        "index_probe": [("eq_region", 2), ("amount_gt", 120)],
        "range_scan": [("amount_gt", 260), ("account_lt", 20)],
        "join": [("eq_region", 3), ("join_account", 7)],
        "aggregate": [("amount_gt", 80), ("group_region", 5)],
    }[name]
    conditions = []
    for op, operand in query_plan:
        if op == "eq_region":
            conditions.append(f"region == {operand}")
        elif op == "amount_gt":
            conditions.append(f"amount > {operand}")
        elif op == "account_lt":
            conditions.append(f"account < {operand}")
        elif op == "join_account":
            conditions.append(f"((account ^ {operand}) & 7) == (index & 7)")
        elif op == "group_region":
            conditions.append(f"((region + {operand} + index) % 3) == 0")
        else:
            conditions.append("True")
    condition = " and ".join(conditions) if conditions else "True"
    lines = [
        f"{indent}acc = state ^ {salt}",
        f"{indent}for row_index in range(24):",
        f"{indent}    amount = ((state >> (row_index % 11)) + row_index * 17) & 0x3FF",
        f"{indent}    account = (index + row_index) & 31",
        f"{indent}    region = row_index % 5",
        f"{indent}    if {condition}:",
        f"{indent}        for round_index in range({rounds}):",
        f"{indent}            acc ^= mix64(amount + account * 131 + region * 17 + {salt} + round_index)",
        f"{indent}            acc = ((acc << 5) | (acc >> 59)) & MASK",
    ]
    lines.append(f"{indent}state ^= acc & MASK")
    lines.append(f"{indent}state &= MASK")
    return lines


def render_eclipse_route(name: str, rounds: int, salt: int, indent: str) -> list[str]:
    phase_ops = {
        "parse_unit": ("scan", "parse", "fold"),
        "resolve_symbols": ("scan", "resolve", "fold"),
        "index_workspace": ("scan", "index", "resolve", "fold"),
        "refactor_plan": ("scan", "resolve", "rewrite", "fold"),
    }[name]
    lines = [
        f"{indent}acc = state ^ {salt} ^ index",
    ]
    for phase in phase_ops:
        lines.append(f"{indent}for round_index in range({rounds}):")
        lines.append(f"{indent}    value = mix64(acc + {salt} + round_index * 0x100000001B3)")
        if phase == "scan":
            lines.append(f"{indent}    acc = (acc + (value & 0xFFFF)) & MASK")
        elif phase == "parse":
            lines.append(f"{indent}    acc ^= (value << 11) & MASK")
        elif phase == "resolve":
            lines.append(f"{indent}    acc = (acc ^ (value >> 9) ^ mix64(index + round_index)) & MASK")
        elif phase == "index":
            lines.append(f"{indent}    acc = (acc * 5 + (value & 0x7FFF)) & MASK")
        elif phase == "rewrite":
            lines.append(f"{indent}    acc ^= mix64(value ^ 0xD1B54A32D192ED03)")
        else:
            lines.append(f"{indent}    acc = (acc + mix64(value ^ acc)) & MASK")
    lines.append(f"{indent}state ^= acc & MASK")
    lines.append(f"{indent}state &= MASK")
    return lines


def render_jython_route(name: str, rounds: int, salt: int, indent: str) -> list[str]:
    opcode_groups = {
        "bytecode_dispatch": ("load", "binary", "store"),
        "call_site": ("load", "dispatch", "guard", "return"),
        "parser": ("scan", "parse", "build"),
        "object_graph": ("lookup", "link", "trace"),
    }[name]
    lines = [
        f"{indent}stack = [state ^ {salt}, index, {salt}, state, index ^ {salt}, 0, 0, 0]",
        f"{indent}sp = 0",
        f"{indent}acc = state ^ {salt} ^ (index * 0x100000001B3)",
    ]
    for opcode in opcode_groups:
        lines.append(f"{indent}for round_index in range({rounds}):")
        lines.append(f"{indent}    probe = mix64(acc + stack[(sp - 1) & 7] + {salt} + round_index)")
        if opcode == "load":
            lines.append(f"{indent}    stack[sp & 7] = probe")
            lines.append(f"{indent}    sp += 1")
        elif opcode == "binary":
            lines.append(f"{indent}    rhs = stack[(sp - 1) & 7]")
            lines.append(f"{indent}    lhs = stack[(sp - 2) & 7]")
            lines.append(f"{indent}    acc ^= (lhs + rhs + probe) & MASK")
        elif opcode == "store":
            lines.append(f"{indent}    stack[(sp + 3) & 7] = acc ^ probe")
            lines.append(f"{indent}    acc = (acc + mix64(stack[(sp + 3) & 7])) & MASK")
        elif opcode == "dispatch":
            lines.append(f"{indent}    acc = mix64(acc ^ probe ^ stack[sp & 7])")
        elif opcode == "guard":
            lines.append(f"{indent}    acc = (acc + probe) & MASK if (probe & 1) else (acc ^ mix64(probe + index))")
        elif opcode == "return":
            lines.append(f"{indent}    acc ^= mix64(acc + stack[(sp - 1) & 7])")
        elif opcode == "scan":
            lines.append(f"{indent}    acc = (acc + (probe & 0xFFFF)) & MASK")
        elif opcode == "parse":
            lines.append(f"{indent}    acc ^= (probe << 7) & MASK")
        elif opcode == "build":
            lines.append(f"{indent}    stack[sp & 7] = mix64(acc + probe)")
            lines.append(f"{indent}    sp += 1")
        elif opcode == "lookup":
            lines.append(f"{indent}    acc ^= mix64(stack[(probe >> 3) & 7] + probe)")
        elif opcode == "link":
            lines.append(f"{indent}    acc = (acc * 5 + probe + stack[sp & 7]) & MASK")
        elif opcode == "trace":
            lines.append(f"{indent}    acc ^= mix64(acc ^ probe ^ round_index)")
        else:
            lines.append(f"{indent}    acc = mix64(acc + probe)")
    lines.append(f"{indent}state ^= acc & MASK")
    lines.append(f"{indent}state &= MASK")
    return lines


def render_fop_route(name: str, rounds: int, salt: int, indent: str) -> list[str]:
    pipeline_ops = {
        "xml_parse": ("token", "tree", "validate"),
        "layout": ("measure", "break", "place"),
        "render": ("paint", "encode", "flush"),
        "regex": ("match", "replace", "fold"),
    }[name]
    lines = [
        f"{indent}acc = state ^ {salt}",
        f"{indent}cursor = index & 0x3FF",
        f"{indent}page = 1",
        f"{indent}depth = 0",
    ]
    for step in pipeline_ops:
        lines.append(f"{indent}for round_index in range({rounds}):")
        lines.append(f"{indent}    probe = mix64(acc + {salt} + round_index + (index << 2))")
        if step == "token":
            lines.append(f"{indent}    depth += 1 if (probe & 3) else -1")
            lines.append(f"{indent}    depth = max(depth, 0)")
            lines.append(f"{indent}    acc ^= mix64(probe + depth)")
        elif step == "tree":
            lines.append(f"{indent}    acc = (acc + ((depth + 1) * (probe & 0xFFFF))) & MASK")
        elif step == "validate":
            lines.append(f"{indent}    acc ^= mix64(acc + probe + depth)")
        elif step == "measure":
            lines.append(f"{indent}    cursor += 8 + (probe & 63)")
            lines.append(f"{indent}    acc = (acc + cursor + page) & MASK")
        elif step == "break":
            lines.append(f"{indent}    if cursor > 720:")
            lines.append(f"{indent}        page += 1")
            lines.append(f"{indent}        cursor = cursor % 89")
            lines.append(f"{indent}    acc ^= mix64(page + cursor + probe)")
        elif step == "place":
            lines.append(f"{indent}    acc = ((acc << 5) | (acc >> 59)) & MASK")
            lines.append(f"{indent}    acc ^= mix64(cursor + round_index)")
        elif step == "paint":
            lines.append(f"{indent}    acc = mix64(acc ^ ((round_index + 1) * 0x45D9F3B))")
        elif step == "encode":
            lines.append(f"{indent}    acc = (acc + mix64(probe ^ page)) & MASK")
        elif step == "flush":
            lines.append(f"{indent}    acc ^= mix64(acc + cursor + page)")
        elif step == "match":
            lines.append(f"{indent}    acc ^= mix64(probe + 31) if (acc & 1) == 0 else mix64(probe + 17)")
        elif step == "replace":
            lines.append(f"{indent}    acc = (acc * 3 + (probe & 0xFFF)) & MASK")
        elif step == "fold":
            lines.append(f"{indent}    acc = (acc + mix64(probe ^ acc)) & MASK")
        else:
            lines.append(f"{indent}    acc = mix64(acc + probe)")
    lines.append(f"{indent}state ^= acc & MASK")
    lines.append(f"{indent}state &= MASK")
    return lines


def generate_artifact(benchmark: str, counts: dict[str, int], requests: int) -> str:
    routes = ROUTE_SPECS[benchmark]
    route_by_name = {str(route["name"]): route for route in routes}
    hot_order = sorted(route_by_name, key=lambda name: counts.get(name, 0), reverse=True)
    total = sum(counts.values()) or 1
    profile_summary: dict[str, Any] = {
        "benchmark": benchmark,
        "profiledRequests": requests,
        "routeCounts": counts,
        "routeFractions": {name: counts.get(name, 0) / total for name in route_by_name},
        "specializedOrder": hot_order,
    }

    lines = [
        "# Generated by profile_codegen.py. Do not edit by hand.",
        "from __future__ import annotations",
        "",
        f"BENCHMARK = {benchmark!r}",
        f"PROFILE_SUMMARY = {json.dumps(profile_summary, sort_keys=True)!r}",
        "MASK = (1 << 64) - 1",
        "",
        "",
        "def mix64(value: int) -> int:",
        "    value &= MASK",
        "    value ^= value >> 33",
        "    value = (value * 0xFF51AFD7ED558CCD) & MASK",
        "    value ^= value >> 33",
        "    value = (value * 0xC4CEB9FE1A85EC53) & MASK",
        "    value ^= value >> 33",
        "    return value & MASK",
        "",
        "",
        "def run(requests: int, seed: int) -> int:",
        "    state = (0x123456789ABCDEF0 ^ seed) & MASK",
        "    for index in range(requests):",
        "        ticket = mix64(index ^ state) % 100",
    ]

    first = True
    for name in hot_order:
        route = route_by_name[name]
        previous = previous_threshold(routes, name)
        keyword = "if" if first else "elif"
        lines.append(f"        {keyword} {route_expression(route, previous)}:")
        lines.extend(render_route_block(benchmark, route, "            "))
        first = False
    lines.extend(
        [
            "        else:",
            "            raise AssertionError('unreachable route ticket')",
            "    return state & MASK",
            "",
        ]
    )
    return "\n".join(lines)


def previous_threshold(routes: list[dict[str, int | str]], name: str) -> int:
    previous = 0
    for route in routes:
        if str(route["name"]) == name:
            return previous
        previous = int(route["threshold"])
    raise ValueError(f"route {name} not found")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("profiles", nargs="+", type=Path)
    args = parser.parse_args()

    benchmark, counts, requests = load_profiles(args.profiles)
    artifact = generate_artifact(benchmark, counts, requests)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(artifact, encoding="utf-8")
    print(f"wrote {args.out}")
    print(f"benchmark={benchmark} profiled_requests={requests} route_counts={json.dumps(counts, sort_keys=True)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
