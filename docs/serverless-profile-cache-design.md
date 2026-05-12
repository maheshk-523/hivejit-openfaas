# Serverless Profile-Artifact Cache Design

## Target Pattern

```text
Execution -> profile/artifact export -> profile/artifact import -> future execution
```

The unit of reuse is an optimizer artifact, not a whole heap. The cache should
help fresh serverless workers avoid relearning hot routes, type behavior, branch
weights, parse artifacts, and precompiled code.

## Implemented Domains

| Domain | Export | Import | Prototype |
| --- | --- | --- | --- |
| Node/V8 | `vm.Script.createCachedData()` bytes | `new vm.Script(..., { cachedData })` | `prototypes/node-v8-artifact-cache` |
| LLVM/Clang | `.profraw` then `.profdata` | `clang -fprofile-instr-use` | `prototypes/llvm-aot-pgo` |
| Go | CPU `pprof` | `go build -pgo=<profile>` | `prototypes/go-pgo-serverless` |
| C#/.NET | `nettrace` converted to MIBC | `ReadyToRunOptimizationData=<mibc>` | `prototypes/dotnet-readytorun-pgo` |

Run the local matrix with:

```bash
python3 scripts/run_profile_cache_matrix.py
```

The runner skips domains whose SDKs are not installed. On this machine, Go
1.26.3 and a .NET 8 SDK are available; the full .NET static-PGO path still
needs `dotnet-pgo`. `dotnet-trace` is present as a global tool when
`DOTNET_ROOT=/private/tmp/dotnet-sdk` is set.

## Cache Key

Every artifact should be addressed by:

```text
source hash
runtime/compiler name and version
OS / architecture / runtime identifier
compiler flags that affect code generation
workload profile label
artifact kind and schema version
```

The workload label is important. The LLVM prototype already shows the failure
mode: a hot-route profile can help the hot route while doing little or nothing
for a mixed route.

## JVM / HiveJIT Export Overhead

The heap traversal TODO should be converted into timing buckets before changing
the export algorithm:

```text
safepoint_entry_ms
enumerate_classes_ms
enumerate_methods_ms
find_method_data_ms
serialize_counters_ms
serialize_type_profiles_ms
bytecode_hash_ms
symbolize_ms
compress_ms
write_ms
upload_ms
total_export_ms
records_scanned
records_serialized
bytes_uncompressed
bytes_compressed
```

The likely optimization is to avoid heap traversal entirely. HiveJIT-style
profile export should walk VM metadata and MethodData/profile records directly,
serialize only hot or non-empty records, and key each record by class name,
method signature, bytecode hash, JVM build, and relevant JVM flags.

Once those buckets are emitted as CSV or JSONL, rank them with:

```bash
python3 scripts/analyze_export_overhead.py export-timings.jsonl
```

## Function Creation TODO

Treat function creation as a separate benchmark variable:

```text
parse/load time
function object creation time
profile import time
first invocation time
time to hot route
```

The Node prototype is useful here because it separates V8 script compilation
from handler execution. A deeper V8/runtime prototype would need a separate
artifact for feedback vectors or optimized code; public Node cachedData does not
export those.

## Benchmark Plan

Use short serverless-style handlers, not only long steady-state benchmarks:

```text
json parse / transform
template render
regex-heavy route
graph traversal
search / indexing
image resize control handler
network/disk dominated control handler
DaCapo lusearch, luindex, xalan, fop, pmd as JVM workloads
```

Report cold-start latency, per-invocation curves, p50/p95/p99, time-to-hot,
export cost, import cost, artifact size, rejection rate, compilation events,
and deoptimization events. Do not report only final throughput.

## Research Takeaways

Laurence Tratt and coauthors' warmup work is a warning against assuming that a
JIT reaches a stable peak after a fixed number of iterations. For this project,
that means the metric is not just "faster after warmup"; it is whether imported
artifacts reduce the number and cost of unstable early invocations.

The 2026 Tratt blog post on retrofitting JIT compilers into C interpreters is
also relevant: JITs work by betting that recent execution predicts future
execution. ProfileCache-FaaS externalizes that bet across short-lived workers.

The DaCapo paper is relevant for methodology: managed runtime benchmarks need
time-series and heap/runtime behavior, because compiler, VM, architecture,
memory management, and application behavior interact.

## Sources

- Go PGO: https://go.dev/doc/pgo
- .NET ReadyToRun: https://learn.microsoft.com/en-us/dotnet/core/deploying/
- .NET dynamic/static PGO design issue: https://github.com/dotnet/runtime/issues/43618
- dotnet-pgo source README: https://github.com/dotnet/runtime/blob/main/src/coreclr/tools/dotnet-pgo/README.md
- dotnet-pgo design notes: https://github.com/dotnet/runtime/blob/main/src/coreclr/tools/dotnet-pgo/dotnet-pgo-experiment.md
- Laurence Tratt, "Retrofitting JIT Compilers into C Interpreters": https://tratt.net/laurie/blog/2026/retrofitting_jit_compilers_into_c_interpreters.html
- Virtual Machine Warmup Blows Hot and Cold: https://doi.org/10.1145/3133876
- DaCapo paper summary: https://research.ibm.com/publications/the-dacapo-benchmarks-java-benchmarking-development-and-analysis
