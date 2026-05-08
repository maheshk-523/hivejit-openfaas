# Progress Log

## 2026-05-08

### Shared Chat Context

The shared chat points to a project best framed as **profile-artifact caching
for serverless**:

```text
Execution -> profile/artifact export -> profile/artifact import -> future execution
```

The main research system is JVM/HiveJIT-style profile reuse, but the immediate
task is to find and prototype non-JVM analogues.

### Local Toolchain Check

Available:

```text
Node 24.11.1
Apple Clang / xcrun clang
xcrun llvm-profdata
```

Not available:

```text
go
dotnet
```

Because Go and .NET are not installed locally, I built runnable Node/V8 and
LLVM/Clang prototypes while keeping Go and .NET in the research map as the
recommended second and third domains.

### Node/V8 Prototype Run

Command:

```bash
node prototypes/node-v8-artifact-cache/bench.js --runs 8
```

Observed result:

```text
artifact_bytes: 168904
cachedDataRejected: 0

none:
  compile_mean: 0.792 ms
  total_mean:   6.514 ms
  execute_mean: 5.436 ms

import:
  compile_mean: 0.275 ms
  total_mean:   4.120 ms
  execute_mean: 3.467 ms
```

Interpretation: a fresh Node process accepted the exported V8 cached data and
used it on import. This is a working public-API baseline for serverless
cold-start artifact reuse, but it is not full JIT feedback import.

### LLVM AOT PGO Prototype Run

Command:

```bash
bash prototypes/llvm-aot-pgo/run_pgo.sh
```

Observed result:

```text
training:
  train, 1,200,000 invocations: 53.801 ms

serve-hot:
  baseline: 88.042 ms, 40.02 ns/invocation
  pgo:      85.653 ms, 38.93 ns/invocation

serve-mixed:
  baseline: 137.614 ms, 62.55 ns/invocation
  pgo:      138.045 ms, 62.75 ns/invocation
```

Interpretation: the trained hot route improved slightly, while the mixed route
did not. That is expected and useful: AOT PGO depends on representative
profiles, so the serverless profile cache must version artifacts by workload
shape and reject stale or mismatched profiles.

### Next Slice

1. Install or provide Go if we want the recommended second-domain prototype:
   `pprof -> go build -pgo -> benchmark`.
2. Install or provide .NET if we want the recommended third-domain prototype:
   `trace -> MIBC -> ReadyToRunOptimizationData -> benchmark`.
3. For HiveJIT/JVM, instrument export overhead first:
   `safepoint_entry_ms`, `enumerate_methods_ms`, `find_method_data_ms`,
   `serialize_counters_ms`, `symbolize_type_profiles_ms`, `compress_ms`,
   `write_ms`, and `total_export_ms`.
