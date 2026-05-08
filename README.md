# Profile Artifact Cache for Serverless

This repo is a research/prototype workspace for a serverless optimization idea:

```text
Execution -> profile/artifact export -> profile/artifact import -> faster future execution
```

The shared-chat framing is that this generalizes the HiveJIT idea. Short-lived
serverless workers repeatedly lose runtime state that was expensive to learn:
hot methods, type feedback, branch frequencies, parse/compile artifacts, and
other optimizer inputs. Instead of snapshotting a whole heap, the system should
cache compact profile artifacts that a fresh worker can reuse.

## What We Are Building

Working name: **ProfileCache-FaaS**.

The goal is a serverless control loop:

1. Run a function normally.
2. Export a small optimizer artifact after representative execution.
3. Store and version the artifact by code hash, runtime version, architecture,
   and workload profile.
4. Start a future fresh worker with that artifact.
5. Measure whether the future worker reaches near-hot performance sooner.

For JVM/HiveJIT, the artifact is HotSpot JIT profile state such as MethodData,
type profiles, counters, and tiered compilation metadata. For non-JVM systems,
the same abstraction maps to different artifact types.

## Progress In This Workspace

- Extracted the shared-chat conclusion: the strongest research framing is
  profile-artifact caching for serverless, not whole-process heap snapshotting.
- Identified Go PGO and .NET PGO/ReadyToRun as the clean second and third
  non-JVM domains from the chat.
- Checked the local toolchain: Go and `dotnet` are not installed here, but
  Node 24 and Apple Clang are installed.
- Added two runnable prototypes that fit the local environment:
  - [Node/V8 artifact cache](prototypes/node-v8-artifact-cache/README.md)
  - [LLVM AOT PGO loop](prototypes/llvm-aot-pgo/README.md)
- Added the research map and benchmark plan in
  [docs/research-map.md](docs/research-map.md).

## Quick Start

Run the V8 cached artifact prototype:

```bash
node prototypes/node-v8-artifact-cache/bench.js --runs 8
```

Run the native AOT PGO prototype:

```bash
bash prototypes/llvm-aot-pgo/run_pgo.sh
```

## Current Domain Choices

| Domain | Pattern | Why it matters |
| --- | --- | --- |
| JVM / HiveJIT | Execution -> MethodData export -> MethodData import -> eager JIT | Main research system; avoids whole heap traversal. |
| Go PGO | Execution -> pprof export -> `go build -pgo` -> execution | Clean AOT version of the loop. |
| .NET PGO + ReadyToRun | Execution -> trace/MIBC -> ReadyToRun publish -> execution | Managed runtime comparison with JIT and AOT pieces. |
| Node/V8 | Execution -> V8 code cache export -> cachedData import -> execution | Runnable local prototype; useful serverless cold-start baseline. |
| LLVM/Clang | Execution -> `.profraw` -> `.profdata` -> `-fprofile-use` -> execution | Strict AOT profile export/import loop; runnable local prototype. |

## Key Evaluation Rule

Do not report only steady-state throughput. Laurence Tratt's VM warmup work
shows that warmup is often unstable. Evaluation should report cold-start
latency, per-invocation warmup curves, time-to-hot, p50/p95/p99 latency,
compilation events, deoptimization events, and artifact import/export overhead.

## Sources

- Go PGO: https://go.dev/doc/pgo
- Node module compile cache: https://nodejs.org/download/release/v24.0.1/docs/api/module.html#module-compile-cache
- Node `vm.Script` cached data: https://nodejs.org/api/vm.html#scriptcreatecacheddata
- LLVM `llvm-profdata`: https://llvm.org/docs/CommandGuide/llvm-profdata.html
- Clang PGO user manual: https://releases.llvm.org/17.0.1/tools/clang/docs/UsersManual.html#profile-guided-optimization
- .NET ReadyToRun: https://learn.microsoft.com/en-us/dotnet/core/deploying/ready-to-run
- .NET PGO design notes: https://github.com/dotnet/runtime/issues/43618
- Wasmtime precompilation: https://docs.wasmtime.dev/examples-pre-compiling-wasm.html
- AWS Lambda SnapStart: https://docs.aws.amazon.com/lambda/latest/dg/snapstart.html
- VM warmup paper: https://doi.org/10.1145/3133876
- DaCapo benchmarks: https://www.dacapobench.org/
