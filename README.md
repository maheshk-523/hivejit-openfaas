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
- Checked the local toolchain. Go is now available locally; .NET was restored
  for the latest run under `/private/tmp/dotnet-sdk`. Node 24 and Apple Clang
  are also installed.
- Added two runnable prototypes that fit the local environment:
  - [Node/V8 artifact cache](prototypes/node-v8-artifact-cache/README.md)
  - [LLVM AOT PGO loop](prototypes/llvm-aot-pgo/README.md)
- Added source-complete prototypes for the requested second and third non-JVM
  domains:
  - [Go PGO serverless loop](prototypes/go-pgo-serverless/README.md)
  - [C#/.NET ReadyToRun + static PGO loop](prototypes/dotnet-readytorun-pgo/README.md)
- Added a matrix runner that executes every available domain and skips missing
  SDKs:
  - [scripts/run_profile_cache_matrix.py](scripts/run_profile_cache_matrix.py)
- Added a JVM/HiveJIT export-overhead analyzer for future instrumentation logs:
  - [scripts/analyze_export_overhead.py](scripts/analyze_export_overhead.py)
- Added an HTTP invocation benchmark and SVG graph generator for deployed
  serverless functions:
  - [scripts/http_invoke_latency.py](scripts/http_invoke_latency.py)
  - [docs/serverless-http-benchmark.md](docs/serverless-http-benchmark.md)
- Added JVM vs Go/.NET runtime comparison graphs:
  - [scripts/plot_runtime_comparison.py](scripts/plot_runtime_comparison.py)
  - [docs/runtime-comparison.md](docs/runtime-comparison.md)
- Ran the Go/OpenFaaS Redis-backed profile-cache experiment:
  - [prototypes/go-openfaas-redis-pgo](prototypes/go-openfaas-redis-pgo)
  - [docs/go-openfaas-redis-pgo-results.md](docs/go-openfaas-redis-pgo-results.md)
- Added a C#/.NET OpenFaaS ReadyToRun packaging prototype:
  - [prototypes/dotnet-openfaas-readytorun](prototypes/dotnet-openfaas-readytorun)
- Added a Python/domain-specific profile-specialization cache prototype:
  - [prototypes/python-profile-specialization](prototypes/python-profile-specialization)
  - [docs/python-profile-specialization-results.md](docs/python-profile-specialization-results.md)
- Added a JAX/XLA runtime-signature specialization and persistent compilation
  cache prototype:
  - [prototypes/jax-xla-runtime-specialization](prototypes/jax-xla-runtime-specialization)
  - [docs/jax-xla-runtime-specialization-results.md](docs/jax-xla-runtime-specialization-results.md)
- Added an OpenFaaS/Redis version of the JAX/XLA persistent-cache experiment for
  baseline-vs-cache cold-start measurements:
  - [prototypes/jax-openfaas-redis-xla](prototypes/jax-openfaas-redis-xla)
  - [docs/jax-openfaas-redis-xla-results.md](docs/jax-openfaas-redis-xla-results.md)
- Added the research map and benchmark plan in
  [docs/research-map.md](docs/research-map.md).
- Added implementation notes for cache keys, heap traversal instrumentation,
  function creation overhead, and benchmark selection in
  [docs/serverless-profile-cache-design.md](docs/serverless-profile-cache-design.md).
- Added a JVM/DaCapo OpenFaaS churn harness that runs real `h2`, `lusearch`,
  and `eclipse` in one long-lived JVM process and plots pod-restart warmup
  resets:
  - [prototypes/jvm-openfaas-dacapo](prototypes/jvm-openfaas-dacapo)
- Added a Julia/OpenFaaS/Redis precompile-cache experiment that demonstrates
  the same warmup graph shape as the JVM but using Julia's LLVM JIT and
  `--trace-compile` precompile artifacts stored in Redis:
  - [prototypes/julia-openfaas-redis-precompile](prototypes/julia-openfaas-redis-precompile)

## Quick Start

Run the V8 cached artifact prototype:

```bash
node prototypes/node-v8-artifact-cache/bench.js --runs 8
```

Run the native AOT PGO prototype:

```bash
bash prototypes/llvm-aot-pgo/run_pgo.sh
```

Run every available prototype as a serverless profile-cache matrix:

```bash
python3 scripts/run_profile_cache_matrix.py
```

Benchmark a deployed serverless HTTP endpoint and produce a latency graph:

```bash
python3 scripts/http_invoke_latency.py --url http://127.0.0.1:8080/function/profilecache --requests 100
```

Generate JVM/OpenFaaS vs Go/.NET comparison graphs after running the prototypes:

```bash
python3 scripts/plot_runtime_comparison.py
```

Rank export overhead buckets once HiveJIT emits CSV or JSONL timing logs:

```bash
python3 scripts/analyze_export_overhead.py export-timings.jsonl
```

Run the Go PGO prototype when Go is installed:

```bash
bash prototypes/go-pgo-serverless/run_pgo.sh
```

Run the Go profile-cache demo across the DaCapo-shaped Go workloads:

```bash
cd prototypes/go-pgo-cache-demo
BENCHMARKS="dacapo-lusearch dacapo-eclipse dacapo-h2" PROFILE_ITERS="3 5" ./run_profile_cache.sh
```

Those benchmark aliases are Go-native workload shapes. The real DaCapo
`lusearch`, `eclipse`, and `h2` programs are JVM benchmarks and should be used
with the George/JVM path rather than as subprocesses for Go PGO.

Run the Python profile-specialization cache across two DaCapo-shaped workloads:

```bash
bash prototypes/python-profile-specialization/run_profile_cache.sh
```

Run the JAX/XLA runtime-signature specialization prototype:

```bash
bash prototypes/jax-xla-runtime-specialization/run_jax_xla.sh
```

Run the JAX/XLA OpenFaaS Redis cold-start experiment when OpenFaaS is installed:

```bash
bash scripts/07_run_jax_openfaas_redis_xla.sh
```

Run real DaCapo `h2`, `lusearch`, and `eclipse` on OpenFaaS with scripted pod
churn:

```bash
bash scripts/08_run_jvm_openfaas_dacapo_churn.sh
```

Run the Julia precompile-cache experiment (DaCapo-analog workloads, same warmup
graph style, different language/compiler):

```bash
bash scripts/09_run_julia_openfaas_redis_precompile.sh
```

Run the C#/.NET ReadyToRun prototype when the .NET SDK is installed:

```bash
bash prototypes/dotnet-readytorun-pgo/run_readytorun.sh
```

## Current Domain Choices

| Domain | Pattern | Why it matters |
| --- | --- | --- |
| JVM / HiveJIT | Execution -> MethodData export -> MethodData import -> eager JIT | Main research system; avoids whole heap traversal. |
| Go PGO | Execution -> pprof export -> `go build -pgo` -> execution | Clean AOT version of the loop; implemented as a prototype. |
| Python profile specialization | Execution -> route/query profile export -> generated specialization module -> execution | Domain-specific specialization path; latest run improves cold-process p50 on lusearch and h2. |
| JAX/XLA | Execution -> tensor signature profile -> XLA persistent compilation cache -> execution | ML/tensor compiler path; runtime shapes, dtypes, and static args drive specialized XLA executables. |
| JAX/XLA on OpenFaaS + Redis | OpenFaaS pod -> Redis cache artifact import -> JAX persistent compilation cache -> first request | Serverless version of the JAX path for measuring baseline versus Redis-backed cold starts. |
| Julia LLVM JIT | Execution -> `--trace-compile` precompile.jl export -> Redis import -> `include(precompile.jl)` at startup -> execution | Same warmup graph as JVM but for Julia's LLVM JIT; DaCapo-analog workloads (lusearch/h2/eclipse) in pure Julia. |
| .NET PGO + ReadyToRun | Execution -> trace/MIBC -> ReadyToRun publish -> execution | Managed runtime comparison with JIT and AOT pieces; implemented as SDK/static-PGO scripts. |
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
- JAX tracing: https://docs.jax.dev/en/latest/tracing.html
- JAX persistent compilation cache: https://docs.jax.dev/en/latest/persistent_compilation_cache.html
- XLA overview: https://openxla.org/xla
