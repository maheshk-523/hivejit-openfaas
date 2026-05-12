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

1. Install or provide Go to run the implemented second-domain prototype:
   `prototypes/go-pgo-serverless`.
2. Install or provide .NET to run the implemented third-domain prototype:
   `prototypes/dotnet-readytorun-pgo`.
3. For HiveJIT/JVM, instrument export overhead first:
   `safepoint_entry_ms`, `enumerate_methods_ms`, `find_method_data_ms`,
   `serialize_counters_ms`, `symbolize_type_profiles_ms`, `compress_ms`,
   `write_ms`, and `total_export_ms`.

## 2026-05-08 Follow-Up Implementation

Added:

```text
prototypes/go-pgo-serverless
prototypes/dotnet-readytorun-pgo
scripts/run_profile_cache_matrix.py
docs/serverless-profile-cache-design.md
```

The Go and C# prototypes are source-complete but cannot be executed on this
machine until the corresponding SDKs are installed. The matrix runner skips
missing SDKs and still runs the locally available Node/V8 and LLVM/Clang loops.

## 2026-05-12 Follow-Up Validation

Local toolchain now has:

```text
Go 1.26.3 darwin/arm64
.NET SDK 8.0.420 via /private/tmp/dotnet-sdk
```

Fixed the Go profile-cache runner so profile merge reads only
`invoke-*.pprof`, writes `merged.pprof` through a temporary file, and fails if
the merged artifact is empty. This prevents reruns from accidentally passing
the output profile back into `go tool pprof`.

Fresh Go validation run:

```bash
RUN_ID=codex-dacapo-fixed-20260512 \
BENCHMARKS="dacapo-lusearch dacapo-eclipse dacapo-h2" \
INVOKES=16 REQUESTS=150000 PROFILE_REQUESTS=400000 PROFILE_ITERS="3 5" \
bash prototypes/go-pgo-cache-demo/run_profile_cache.sh
```

All three DaCapo-shaped Go workloads completed the full loop:

```text
baseline cold execution -> pprof export -> profile merge
-> go build -pgo=<merged.pprof> -> optimized cold execution
```

The clearest working benchmark shapes are `dacapo-lusearch` and `dacapo-h2`.
Both complete export/import and show lower p50 latency with imported profile
data. The short local run still has large first-process outliers in PGO p95
columns, so do not claim stable tail-latency improvement yet.

C#/.NET SDK-only ReadyToRun also ran successfully:

```bash
DOTNET_BIN=/private/tmp/dotnet-sdk/dotnet \
bash prototypes/dotnet-readytorun-pgo/run_readytorun.sh
```

ReadyToRun improved p50 in this run:

```text
serve-hot:   IL p50 17.358 ms -> R2R p50 11.578 ms
serve-mixed: IL p50 49.509 ms -> R2R p50 18.493 ms
```

The full C# static-PGO/MIBC loop remains blocked locally because `dotnet-pgo`
is not installed. `dotnet-trace` is installed as a global tool and runs when
`DOTNET_ROOT=/private/tmp/dotnet-sdk` is set.
