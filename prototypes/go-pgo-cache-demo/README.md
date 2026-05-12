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

A quick multi-benchmark run on this machine used:

```bash
RUN_ID=go-dacapo-quick-20260511 \
BENCHMARKS="dacapo-lusearch dacapo-eclipse dacapo-h2" \
INVOKES=8 REQUESTS=120000 PROFILE_REQUESTS=300000 PROFILE_ITERS="3 5" \
./run_profile_cache.sh
```

The p50 result improved for each workload, while the short 8-invocation run still showed
first-process outliers in the p95/mean columns:

| benchmark | build | n | mean wall ms | p50 wall ms | p95 wall ms |
|---|---|---:|---:|---:|---:|
| dacapo-lusearch | No PGO | 8 | 59.071 | 53.492 | 82.605 |
| dacapo-lusearch | PGO, 3 profiles | 8 | 68.801 | 50.632 | 145.617 |
| dacapo-lusearch | PGO, 5 profiles | 8 | 70.734 | 50.731 | 155.230 |
| dacapo-eclipse | No PGO | 8 | 64.703 | 61.212 | 79.914 |
| dacapo-eclipse | PGO, 3 profiles | 8 | 78.600 | 59.576 | 158.738 |
| dacapo-eclipse | PGO, 5 profiles | 8 | 78.775 | 60.180 | 156.868 |
| dacapo-h2 | No PGO | 8 | 46.965 | 46.881 | 47.457 |
| dacapo-h2 | PGO, 3 profiles | 8 | 63.353 | 45.301 | 139.471 |
| dacapo-h2 | PGO, 5 profiles | 8 | 64.959 | 45.084 | 148.303 |

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
