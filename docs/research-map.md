# Research Map

## Thesis

Serverless platforms create many fresh workers. Managed runtimes and optimizing
compilers spend requests learning which code is hot, which branches are common,
which types appear at polymorphic call sites, and which modules should be
compiled. When the worker is evicted, that learning disappears.

The project should treat those learned facts as cacheable artifacts:

```text
function code + runtime version + architecture + workload profile
  -> optimizer artifact
  -> future worker starts with the artifact
```

This is intentionally smaller than heap checkpointing. The research question is
whether compact optimizer artifacts capture most of the benefit without the cost
and fragility of whole-process snapshots.

## Domain Matrix

| Domain | Export | Import | Compile mode | Prototype status |
| --- | --- | --- | --- | --- |
| JVM / HiveJIT | HotSpot MethodData, invocation/backedge counters, type profiles, inline-cache-like feedback | Load profile into a clean JVM, then trigger eager JIT for hot methods | JIT | Main research direction; not implemented in this empty workspace. |
| Go | CPU `pprof` profile | `go build -pgo=profile.pprof` | AOT | Implemented and locally verified in `prototypes/go-pgo-cache-demo`; Go 1.26.3 is available. |
| .NET | `dotnet-trace` data converted to MIBC profile data | `ReadyToRunOptimizationData` during publish | AOT plus tiered JIT | SDK-only ReadyToRun locally verified in `prototypes/dotnet-readytorun-pgo`; full static PGO still needs `dotnet-pgo`. |
| Node/V8 | V8 code cache via `vm.Script.createCachedData()` or Node module compile cache | `cachedData` in a fresh `vm.Script`, or `NODE_COMPILE_CACHE` | Parse/compile cache, not full JIT profile | Implemented in `prototypes/node-v8-artifact-cache`. |
| LLVM/Clang native | Instrumented `.profraw` profile | `llvm-profdata merge`, then compile with `-fprofile-instr-use` | AOT | Implemented in `prototypes/llvm-aot-pgo`. |
| Wasmtime | Serialized/precompiled module (`.cwasm`) | `Module::deserialize_file` | AOT-style Wasm precompile | Good future serverless edge/runtime domain. |
| AWS SnapStart | Firecracker microVM snapshot | Restore snapshot for future Lambda version starts | Whole runtime snapshot | Existing platform baseline; now supports Java, Python 3.12+, and .NET 8+. |

## Why Go And .NET Are The Second And Third Domains

Go is the cleanest AOT version:

```text
run function -> collect pprof -> rebuild with go build -pgo -> deploy optimized binary
```

The official Go PGO docs describe this as feeding information from
representative runs back into the compiler for the next build. Go uses CPU
`pprof` profiles, supports production profiles, and describes an iterative
release loop.

.NET is the closest managed-runtime comparison:

```text
run function -> collect trace -> create MIBC -> publish ReadyToRun with profile data
```

ReadyToRun is a form of AOT compilation that reduces the JIT work needed at
startup and first use. .NET dynamic/static PGO work is relevant because it
explicitly treats past behavior as input to future JIT or pre-JIT decisions.

## What The Local Prototypes Prove

The local prototypes are not the full HiveJIT system. They prove the common
artifact lifecycle with available tools.

Node/V8:

```text
fresh process -> compile handler source -> run representative invocations
  -> export V8 cachedData
fresh process -> import cachedData -> run handler
```

This is a serverless cold-start baseline. It caches compilation artifacts, not
runtime type feedback or optimized machine code.

LLVM/Clang:

```text
instrumented binary -> run training workload -> .profraw
  -> llvm-profdata merge -> .profdata
  -> clang -fprofile-instr-use -> optimized binary
```

This is the canonical AOT profile export/import loop.

## Laurence Tratt / Warmup Takeaway

Use the spelling **Laurence Tratt**. The key paper is *Virtual Machine Warmup
Blows Hot and Cold*. Its practical lesson for this project is that warmup is not
monotonic or guaranteed. A benchmark that discards warmup and reports only a
steady state can hide the problem that ProfileCache-FaaS is meant to solve.

Evaluation should include:

```text
cold-start latency
per-invocation latency curves
time to hot state
p50 / p95 / p99 latency
profile export overhead
profile import overhead
compilation events
deoptimization events
artifact size
artifact rejection / invalidation rate
```

## DaCapo / Benchmarking Takeaway

DaCapo is useful as a benchmark methodology guide and workload source, not as a
single long-running Java process. For serverless, wrap DaCapo-style units as
short handlers:

```text
setup once
invoke(input_json) -> output_json
export_profile()
import_profile()
teardown()
```

Good JVM workload candidates:

```text
xalan      XML transforms
fop        allocation-heavy formatting
pmd        parsing/static analysis
luindex    indexing
lusearch   search
hsqldb     database-like workload
jython     dynamic-language behavior on JVM
```

Non-JVM benchmark candidates:

```text
JSON parsing
regular expressions
template rendering
word count
graph traversal
image resize via native library
database/network dominated control handlers
```

The control handlers matter. Profile-artifact caching should help compute-bound
and runtime-optimization-sensitive functions more than handlers dominated by
network, disk, or native libraries.

## Heap Traversal TODO

The shared-chat TODO says heap traversal is slow. The likely issue is scanning
too much state. HiveJIT should not need arbitrary application objects if the
artifact is MethodData/profile state.

Instrument these timers:

```text
safepoint_entry_ms
enumerate_methods_ms
find_method_data_ms
serialize_counters_ms
symbolize_type_profiles_ms
bytecode_hash_ms
compress_ms
write_ms
upload_ms
total_export_ms
```

Preferred direction:

```text
Do not walk the whole heap.
Walk class/method metadata and MethodData structures directly.
Serialize only hot or non-empty profile records.
Version records by class name, method signature, bytecode hash, JVM build, and flags.
```

## Next Implementation Steps

1. Run the Go DaCapo-shaped profile-cache benchmark for reproducible graphs:
   `BENCHMARKS="dacapo-lusearch dacapo-eclipse dacapo-h2" PROFILE_ITERS="3 5" bash prototypes/go-pgo-cache-demo/run_profile_cache.sh`.
2. Run the .NET SDK-only ReadyToRun comparison:
   `DOTNET_BIN=/private/tmp/dotnet-sdk/dotnet bash prototypes/dotnet-readytorun-pgo/run_readytorun.sh`.
3. Install or build `dotnet-pgo`, then run
   `ProfileCache-DotNet`:
   `DOTNET_ROOT=/private/tmp/dotnet-sdk DOTNET_BIN=/private/tmp/dotnet-sdk/dotnet DOTNET_TRACE_BIN=/Users/maheshk/.dotnet/tools/dotnet-trace bash prototypes/dotnet-readytorun-pgo/run_static_pgo.sh`.
4. For JVM/HiveJIT, add export-path timers before changing algorithms so the
   heap traversal bottleneck is measured, not guessed.
