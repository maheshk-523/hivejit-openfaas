# Non-JVM Profile Reuse Domains

The current JVM/George work proves profile persistence inside an OpenFaaS-style lifecycle. The strongest non-JVM follow-ups are domains where a profile exported from one execution can be imported into the next compile or runtime artifact.

## Current Recommendation

Use the new real OpenFaaS result package in
[Real Non-JVM OpenFaaS Pod-Churn Results](real-non-jvm-openfaas-pod-churn-results.md)
as the source of truth.

The current strongest non-JVM pair is:

- **.NET ReadyToRun/NativeAOT family** as the strong AOT artifact baseline. The
  existing five-workload run is IL/JIT vs ReadyToRun AOT; a separate NativeAOT
  run now covers 2,000 real OpenFaaS invocations per workload with fresh pods at
  OpenWhisk-like churn points. Treat this as a compiler-AOT control, not as
  runtime-profile export/import.
- **JAX/XLA persistent compilation cache** as the runtime-information artifact
  domain: tensor shape/dtype/static-arg observations drive XLA compilation, the
  persistent cache is exported to Redis, and fresh OpenFaaS pods import it.

Julia PackageCompiler sysimages are real profile-derived AOT artifacts, but the
current five-workload run is mixed and should be treated as a negative or
secondary result until tuned.

Python domain-specific profile specialization remains a useful application-level
fallback. It changes the executed application path in fresh Python processes,
but it is not a stock compiler AOT path.

## Earlier Python Proof

Proof package:

- [Python OpenFaaS profile-specialization proof](python-openfaas-specialization-proof.md)
- `scripts/build_python_specialization_proof.py`
- `docs/figures/python-openfaas-specialization-proof-summary.png`

This maps directly to the PrismX taxonomy in `snip3-3.pdf`: runtime values and
workload invariants drive code specialization, and the specialization is
domain-specific rather than only a runtime/compiler-internal heuristic.

Local strict evidence:

| workload | no saved state p50 | saved artifact p50 | point-by-point wins |
| --- | ---: | ---: | ---: |
| dacapo-lusearch | 207.0 ms | 183.6 ms | 16/16 |
| dacapo-h2 | 314.4 ms | 269.0 ms | 16/16 |
| dacapo-eclipse | 2077.9 ms | 1718.1 ms | 8/8 |

Graphable figures:

- `docs/figures/python-profile-specialization-strict-dacapo-lusearch-invocation-curves.png`
- `docs/figures/python-profile-specialization-strict-dacapo-h2-invocation-curves.png`
- `docs/figures/python-profile-specialization-strict-dacapo-eclipse-invocation-curves.png`
- `docs/figures/python-profile-specialization-lifecycle-dacapo-lusearch.png`
- `docs/figures/python-profile-specialization-lifecycle-dacapo-h2.png`
- `docs/figures/python-profile-specialization-lifecycle-dacapo-eclipse.png`
- `docs/figures/python-openfaas-profile-specialization-lifecycle-dacapo-lusearch.png`
- `docs/figures/python-openfaas-profile-specialization-lifecycle-dacapo-h2.png`
- `docs/figures/python-openfaas-profile-specialization-lifecycle-dacapo-eclipse.png`
- `docs/figures/python-openfaas-profile-specialization-lifecycle-median-dacapo-lusearch.png`
- `docs/figures/python-openfaas-profile-specialization-lifecycle-median-dacapo-h2.png`
- `docs/figures/python-openfaas-profile-specialization-lifecycle-median-dacapo-eclipse.png`
- `docs/figures/python-profile-specialization-paper-warmup-dacapo-lusearch.png`
- `docs/figures/python-profile-specialization-paper-warmup-dacapo-h2.png`
- `docs/figures/python-profile-specialization-paper-warmup-dacapo-eclipse.png`

Real OpenFaaS/Redis evidence:

| workload | cold median, no saved -> saved | hot median, no saved -> saved | median-position wins |
| --- | ---: | ---: | ---: |
| dacapo-lusearch | 1202.5 ms -> 1088.3 ms | 1115.2 ms -> 1046.7 ms | 8/8 median positions |
| dacapo-h2 | 352.0 ms -> 276.5 ms | 307.8 ms -> 228.5 ms | 8/8 median positions |
| dacapo-eclipse | 2172.2 ms -> 1911.2 ms | 2114.3 ms -> 1887.1 ms | 8/8 median positions |

## 1. Go Serverless Handlers

Fit: exact for profile-guided AOT rebuilds.

Loop:

```text
baseline execution -> CPU pprof export -> merge profile cache -> go build -pgo -> next execution
```

Why it is useful:

- Go uses CPU pprof profiles directly as PGO input.
- The build is still a normal static Go build, so it maps cleanly to serverless image rebuilds.
- The Go documentation explicitly describes the iterative production loop: build, collect profiles, rebuild with profile, repeat.

Local prototype:

```bash
cd /Users/maheshk/Documents/New\ project\ 5/prototypes/go-pgo-cache-demo
./run_profile_cache.sh
```

OpenFaaS/Redis prototype:

```bash
cd /Users/maheshk/Documents/New\ project\ 5/prototypes/go-openfaas-redis-pgo
BENCHMARKS="router dacapo-lusearch dacapo-eclipse dacapo-h2 dacapo-jython dacapo-fop" \
PROFILE_ITERS="5 10" \
./run_openfaas_redis_pgo.sh
```

`dacapo-jython` and `dacapo-fop` are non-JVM analogues, not JVM subprocesses.
`dacapo-jython` models a Python bytecode interpreter loop with hot opcode,
binary-operation, call-dispatch, and exception paths. `dacapo-fop` models an
XML/XSL-FO formatter with parse, layout-tree, pagination, and render paths.
Those shapes keep CPU inside the Go binary so `runtime/pprof` and
`go build -pgo` are measuring Go profile import, not a Java wrapper.

## 2. Native C/C++ Services on LLVM

Fit: exact for profile-guided AOT rebuilds.

Loop:

```text
instrumented execution -> .profraw export -> llvm-profdata merge -> clang -fprofile-use -> next execution
```

Why it is useful:

- Clang has a standard instrumentation PGO loop: build with `-fprofile-generate`, run representative inputs, merge raw profiles, rebuild with `-fprofile-use`.
- This is a good second domain for CPU-heavy serverless functions such as image transforms, compression, parsing, routing engines, or scoring services.
- The local machine has Apple Clang and `xcrun llvm-profdata`, so this can be prototyped here without installing another runtime.

Prototype shape:

```text
function.c or function.rs
run_profile_cache.sh
profiles/<run>/invoke-*.profraw
profiles/<run>/merged.profdata
build/function.pgo
results/*.csv
```

Implemented local shape:

```bash
BENCHMARKS="dacapo-lusearch dacapo-h2 dacapo-eclipse dacapo-jython dacapo-fop" \
PROFILE_ITERS="5 10" \
bash prototypes/llvm-aot-pgo/run_pgo.sh
```

The LLVM prototype now includes the same DaCapo-shaped benchmark set as the Go
profile-cache run:

- `dacapo-lusearch`: regex/search-heavy route distribution.
- `dacapo-h2`: database/index-style route distribution.
- `dacapo-eclipse`: parser/compiler-workbench route distribution.
- `dacapo-jython`: bytecode interpreter dispatch with load, binary operation,
  call, and exception paths.
- `dacapo-fop`: XML/XSL-FO parse, layout, pagination, and render paths.

For each benchmark, the script exports N `.profraw` files, merges them with
`llvm-profdata`, rebuilds with `clang -fprofile-instr-use`, and measures the
future binary.

Latest graph artifacts:

- `docs/figures/go-pgo-profile-cache-all-dacapo-results.png`
- `docs/figures/go-pgo-profile-cache-all-dacapo-linegraphs.png`
- `docs/figures/go-pgo-profile-cache-budget-linegraph.png`
- `docs/figures/go-openfaas-redis-pgo-linegraphs.png`
- `docs/figures/llvm-aot-pgo-all-results.png`
- `docs/figures/llvm-aot-pgo-profile-budget-linegraph.png`
- `docs/figures/real-dotnet-openfaas-pod-churn-invocation-traces.png`
- `docs/figures/real-dotnet-openfaas-pod-churn-position-medians.png`
- `docs/figures/real-julia-openfaas-pod-churn-invocation-traces.png`
- `docs/figures/real-jax-xla-openfaas-pod-churn-invocation-traces.png`
- `docs/figures/real-non-jvm-openfaas-pod-churn-summary.json`

Reported Julia, .NET, and JAX/XLA figures must only be generated from real
OpenFaaS measurement runs. Do not use `codex-*-openwhisk-level-*` emulation
directories for reported results.

## 3. Rust Services

Fit: exact for profile-guided AOT rebuilds.

Loop:

```text
instrumented execution -> .profraw export -> llvm-profdata merge -> rustc -Cprofile-use -> next execution
```

Why it is useful:

- Rust exposes an LLVM-backed PGO workflow through `rustc -Cprofile-generate` and `-Cprofile-use`.
- It is a distinct serverless language/domain from C/C++ even though the profile machinery is LLVM-based.
- It is a good target for compute-heavy serverless functions where cold starts matter less than per-request CPU once the function begins.

Local status: `rustc` is not installed on this machine, so this should be the third prototype once Rust is available.

## 4. .NET API Functions

Fit: good for cold-start/AOT study, partial for persistent profile import.

Loop candidates:

```text
execution -> dynamic PGO/tiered JIT within process
publish -> ReadyToRun or NativeAOT -> next cold execution
```

Why it is useful:

- ReadyToRun is a stock AOT deployment mode that reduces first-use JIT work and is directly relevant to serverless cold starts.
- Dynamic PGO in modern .NET optimizes hot types and paths during tiered compilation.
- Persistent static PGO exists historically in .NET Framework MPGO and internally around CoreCLR/crossgen2, but the stock modern SDK path is less clean than Go or LLVM. Treat .NET as a cold-start/AOT comparison first, and only pursue static profile import if the project can accept runtime-specific tooling.

## 5. Python Domain-Specific Specialization

Fit: exact for application-level runtime specialization.

Loop:

```text
generic execution -> route/query profile JSON -> generated Python specialization module -> next cold execution
```

Why it is useful:

- It fits the online-optimization taxonomy's code-specialization and domain-specific-specialization categories.
- The artifact is imported by fresh Python processes, so the result is not a warm-start effect.
- It is stock-Python and runnable locally, which makes it a reliable fallback when language-runtime JIT tooling is unavailable.

Local prototype:

```bash
cd /Users/maheshk/Documents/New\ project\ 5
bash prototypes/python-profile-specialization/run_profile_cache.sh
```

OpenFaaS/Redis shape:

```text
no saved state:
  fresh function pod -> generic Python handler -> route/query dispatch every request

saved state:
  profile pod -> route/query profile JSON -> generated Python module -> Redis
  fresh function pod -> load generated module from Redis -> direct specialized code
```

The baseline is explicitly "not saving warm state": every cold process imports
the generic handler and repeats the generic dispatch/interpreter path. The
optimized line imports the generated specialization artifact recovered from the
saved profile state.

## 6. JAX/XLA Tensor Programs

Fit: strong for domain-specific runtime-signature specialization.

Loop:

```text
representative execution -> tensor shape/dtype/static-arg profile -> JAX trace and XLA compile -> persistent compilation cache -> next cold execution
```

Why it is useful:

- JAX traces `jax.jit` functions against runtime-observed tensor shapes and
  dtypes, plus any static argument values.
- XLA then specializes the tensor graph with HLO-level compiler passes, backend
  layout decisions, fusion, and generated kernels.
- The persistent compilation cache gives a stock artifact path that can be
  reused by a fresh Python process, which maps directly to the profile-artifact
  cache framing.

Local prototype:

```bash
cd /Users/maheshk/Documents/New\ project\ 5
bash prototypes/jax-xla-runtime-specialization/run_jax_xla.sh
```

Current caveat: this is a compile-cache story, not a reliable every-request
hot-path improvement story. It is useful for showing saved compile state, but it
does not satisfy the stricter requirement that the saved-state line stay below
the no-saved-state line after warmup.

## 7. Packet-Processing / Network Dataplane Specialization

Fit: strongest alternate domain from the PrismX snippet's
domain-specific-specialization category.

Loop:

```text
generic packet pipeline -> runtime flow/header/profile export -> generated fast path -> next cold execution
```

Runtime information:

- hot header combinations and flow classes
- common packet-processing branches
- installed rule distributions
- frequently unused protocol stages

Why it is useful:

- It is explicitly domain-specific: the runtime profile is not just CPU samples,
  it is packet/header/flow structure that lets the system remove unused stages
  and generate fast paths.
- A serverless/OpenFaaS experiment can compare "generic packet pipeline every
  cold start" against "load saved fast path from Redis before serving."
- The expected graph is closer to the paper-style cold/warm/hot curve: initial
  generic requests discover the hot path, then future cold pods skip discovery
  and start on the specialized path.

This is the better research-domain pivot if the Python example feels too much
like an application-level toy. It will take more implementation than Python, but
the story aligns tightly with the cited packet-processing specialization work.

## Reading Anchors

- Go PGO: https://go.dev/doc/pgo
- Clang PGO: https://clang.llvm.org/docs/UsersManual.html#profile-guided-optimization
- Rust PGO: https://doc.rust-lang.org/rustc/profile-guided-optimization.html
- .NET ReadyToRun: https://learn.microsoft.com/en-us/dotnet/core/deploying/ready-to-run
- .NET dynamic PGO config: https://learn.microsoft.com/en-us/dotnet/core/runtime-config/compilation
- DaCapo paper/citation: https://dacapobench.sourceforge.net/cite.html
- Tratt meta-tracing paper: https://kclpure.kcl.ac.uk/portal/en/publications/the-impact-of-meta-tracing-on-vm-design-and-implementation
- Tratt 2026 JIT blog: https://tratt.net/laurie/blog/2026/retrofitting_jit_compilers_into_c_interpreters.html
- JAX tracing: https://docs.jax.dev/en/latest/tracing.html
- JAX persistent compilation cache: https://docs.jax.dev/en/latest/persistent_compilation_cache.html
- XLA overview: https://openxla.org/xla
