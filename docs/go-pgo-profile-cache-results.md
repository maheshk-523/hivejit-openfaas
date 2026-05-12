# Go PGO Profile-Cache Results

This note captures the graphable version of the Go serverless profile-cache prototype.

## System Loop

```text
baseline cold execution -> pprof export -> profile merge -> go build -pgo -> optimized cold execution
```

The Go version is an AOT profile-import system. Unlike the George JVM prototype, stock Go does not load a profile at process startup. The serverless controller has to rebuild the next function artifact with the merged profile.

## Figures

![Go PGO cold invocation curves](figures/go-pgo-profile-cache-invocation-curves.svg)

![Go PGO p50 p95 latency](figures/go-pgo-profile-cache-p50-p95.svg)

![Go PGO profile budget improvement](figures/go-pgo-profile-cache-profile-budget-improvement.svg)

## Latest Graph Run

Run id: `go-pgo-graphs-20260511`

| build | n | mean wall ms | p50 wall ms | p95 wall ms |
|---|---:|---:|---:|---:|
| No PGO | 40 | 131.057 | 113.981 | 200.212 |
| PGO, 5 profiles | 40 | 108.796 | 103.365 | 122.443 |
| PGO, 10 profiles | 40 | 113.733 | 105.839 | 159.453 |

In this run, both profile-guided builds improve median and tail latency versus the no-PGO binary. The five-profile build is strongest here, which is a useful caveat: more profile samples are not automatically better unless they are representative of the same workload mix.

## DaCapo-Shaped Go Workloads

Run id: `go-dacapo-quick-20260511`

Command:

```bash
BENCHMARKS="dacapo-lusearch dacapo-eclipse dacapo-h2" \
INVOKES=8 REQUESTS=120000 PROFILE_REQUESTS=300000 PROFILE_ITERS="3 5" \
./run_profile_cache.sh
```

These are Go-native workloads shaped after the DaCapo categories, not the JVM
DaCapo jars. They are valid for testing Go profile export/import because the CPU
work stays inside the Go binary.

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

The median improved slightly for all three quick workloads. The mean and p95
were worse because each short series had a large first-process outlier in the PGO
binary; use a larger `INVOKES` value before treating tail numbers as stable.

Figures:

- [lusearch invocation curve](figures/go-pgo-profile-cache-dacapo-lusearch-invocation-curves.svg)
- [eclipse invocation curve](figures/go-pgo-profile-cache-dacapo-eclipse-invocation-curves.svg)
- [h2 invocation curve](figures/go-pgo-profile-cache-dacapo-h2-invocation-curves.svg)

## Reading The Graphs

- `No PGO` is the baseline Go binary compiled with `-pgo=off`.
- `PGO, 5 profiles` exports five baseline CPU profiles, merges them, and rebuilds the handler with `go build -pgo`.
- `PGO, 10 profiles` repeats the same cache/import flow with ten profiling invocations.
- Each invocation in the curve graph is a separate process, which approximates serverless cold function execution.

The useful takeaway is not just that one bar is lower. The important end-to-end result is that the system exports execution evidence from short-lived function instances, persists it outside the instance, and feeds it into a later optimized artifact.
