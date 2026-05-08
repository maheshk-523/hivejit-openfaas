# C#/.NET ReadyToRun and Static PGO Prototype

This prototype covers the managed non-JVM domain:

```text
Execution -> trace/MIBC export -> ReadyToRun import -> future execution
```

There are two scripts because .NET exposes two relevant levels:

- `run_readytorun.sh` uses only the public .NET SDK. It compares IL/JIT,
  ReadyToRun, and in-process dynamic PGO. This proves the future-worker
  precompile artifact path, but it does not export reusable user profile data.
- `run_static_pgo.sh` is the full profile-artifact loop. It collects a
  `nettrace`, converts it to MIBC with `dotnet-pgo`, and passes that profile
  into `dotnet publish` with `ReadyToRunOptimizationData`.

## Run SDK-Only ReadyToRun

Requires a .NET 8+ SDK.

```bash
bash prototypes/dotnet-readytorun-pgo/run_readytorun.sh
```

## Run Full Static PGO

Requires:

```text
dotnet
dotnet-trace
dotnet-pgo
```

Then:

```bash
bash prototypes/dotnet-readytorun-pgo/run_static_pgo.sh
```

The full loop writes:

```text
prototypes/dotnet-readytorun-pgo/artifacts/train.nettrace
prototypes/dotnet-readytorun-pgo/artifacts/train.mibc
prototypes/dotnet-readytorun-pgo/build/static-pgo-r2r-with-mibc/
prototypes/dotnet-readytorun-pgo/results/static-pgo.jsonl
```

## Serverless Interpretation

The MIBC file is the reusable profile artifact. A production control loop would:

1. Run a baseline C# function under representative traffic.
2. Export runtime trace data from a canary or profiling window.
3. Convert the trace to MIBC and store it under source/runtime/RID/workload
   metadata.
4. Publish the next function image with ReadyToRun and
   `ReadyToRunOptimizationData=<artifact.mibc>`.
5. Compare cold-start and first-invocation latency curves against regular R2R.

The SDK-only script remains useful when `dotnet-pgo` is unavailable because it
isolates the value of precompilation from the value of imported profile data.
