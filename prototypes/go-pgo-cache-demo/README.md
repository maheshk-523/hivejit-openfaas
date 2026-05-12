# Go Serverless Profile-Cache Demo

This prototype shows the non-JVM version of the George/OpenFaaS loop:

```text
Execution -> Profile Export -> Profile Cache -> Profile Import/AOT Build -> Execution
```

For Go, the import step happens at compile time. A serverless platform would collect profiles from short-lived instances, merge them in a cache, then rebuild the next function image with `go build -pgo=<merged.pprof>`.

## Run

```bash
cd /Users/maheshk/Documents/New\ project\ 5/prototypes/go-pgo-cache-demo
chmod +x run_profile_cache.sh
./run_profile_cache.sh
```

The script runs both requested profile budgets:

- 5 profiling invocations
- 10 profiling invocations

Useful knobs:

```bash
INVOKES=40 REQUESTS=500000 PROFILE_REQUESTS=1200000 ./run_profile_cache.sh
PROFILE_ITERS="5 10 20" ./run_profile_cache.sh
BENCHMARKS="dacapo-lusearch dacapo-eclipse dacapo-h2" PROFILE_ITERS="3 5" ./run_profile_cache.sh
```

`BENCHMARKS` accepts `router` plus Go-native DaCapo-shaped workloads:

- `dacapo-lusearch`: search/indexing style route mix.
- `dacapo-eclipse`: parser, resolver, and workspace-index route mix.
- `dacapo-h2`: scan, index probe, join, and aggregation route mix.

These names are intentionally explicit aliases for the benchmark shapes. The real DaCapo
`lusearch`, `eclipse`, and `h2` programs are JVM workloads; wrapping them in a Go process
would mostly profile `os/exec` and waiting, not the Java CPU work that Go PGO can optimize.

## What It Measures

- `handler.nopgo`: baseline AOT Go binary, built with `-pgo=off`.
- `invoke-N.pprof`: CPU profiles exported by one-shot handler invocations.
- `merged.pprof`: profile cache created by `go tool pprof -proto`.
- `handler.pgo.5` and `handler.pgo.10`: rebuilt AOT binaries using the imported profile cache.
- `go-nopgo.csv`, `go-pgo-5.csv`, `go-pgo-10.csv`: cold process invocation timings.
- `summary.csv`: mean, p50, p95, min, and max wall-clock latency.
- `docs/figures/go-pgo-profile-cache-*.svg`: reproducible charts for the latest run.

The default `router` workload intentionally uses a skewed route mix through interface dispatch.
The DaCapo-shaped workloads use the same profile-cache loop with different CPU-bound route
families, giving Go PGO concrete hot-path information it can use for inlining and
devirtualization decisions.

The latest multi-benchmark validation run on this machine used:

```bash
RUN_ID=codex-dacapo-fixed-20260512 \
BENCHMARKS="dacapo-lusearch dacapo-eclipse dacapo-h2" \
INVOKES=16 REQUESTS=150000 PROFILE_REQUESTS=400000 PROFILE_ITERS="3 5" \
./run_profile_cache.sh
```

The p50 result improved for each workload, while the short 16-invocation run
still showed first-process outliers in the p95/mean columns:

| benchmark | build | n | mean wall ms | p50 wall ms | p95 wall ms |
|---|---|---:|---:|---:|---:|
| dacapo-lusearch | No PGO | 16 | 72.045 | 68.967 | 84.866 |
| dacapo-lusearch | PGO, 3 profiles | 16 | 74.803 | 63.710 | 114.567 |
| dacapo-lusearch | PGO, 5 profiles | 16 | 72.611 | 63.058 | 101.761 |
| dacapo-eclipse | No PGO | 16 | 78.171 | 76.301 | 84.932 |
| dacapo-eclipse | PGO, 3 profiles | 16 | 91.222 | 74.729 | 148.503 |
| dacapo-eclipse | PGO, 5 profiles | 16 | 84.210 | 74.437 | 114.005 |
| dacapo-h2 | No PGO | 16 | 58.682 | 58.302 | 59.970 |
| dacapo-h2 | PGO, 3 profiles | 16 | 66.063 | 55.794 | 98.508 |
| dacapo-h2 | PGO, 5 profiles | 16 | 68.135 | 57.928 | 109.333 |

On this machine, the graph run `go-pgo-graphs-20260511` produced:

| label | n | mean wall ms | p50 wall ms | p95 wall ms |
|---|---:|---:|---:|---:|
| go-nopgo | 40 | 131.057 | 113.981 | 200.212 |
| go-pgo-5 | 40 | 108.796 | 103.365 | 122.443 |
| go-pgo-10 | 40 | 113.733 | 105.839 | 159.453 |

Compiler evidence from the 10-profile build:

```text
PGO devirtualizing interface call op.Apply to hashRoute.Apply
```

The graph generator can also be run directly against any result directory:

```bash
python3 plot_results.py \
  --results results/<run-id> \
  --out-dir ../../docs/figures \
  --prefix go-pgo-profile-cache
```

## Serverless Mapping

| Serverless concept | Prototype artifact |
|---|---|
| Function instance executes request | `handler.nopgo -requests ...` |
| Runtime exports execution profile | `-profile-out profiles/.../invoke-N.pprof` |
| Platform stores profile | `profiles/<run>/<N>-iters/*.pprof` |
| Platform imports profile into next build | `go build -pgo=profiles/.../merged.pprof` |
| Next cold instance uses optimized artifact | `handler.pgo.5` / `handler.pgo.10` |

This differs from George because stock Go does not load profiles at process start. The cache must feed a rebuild step, so the deploy controller is part of the experiment.
