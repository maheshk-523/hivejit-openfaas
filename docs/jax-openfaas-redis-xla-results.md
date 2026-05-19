# JAX/XLA OpenFaaS Redis Cold-Start Results

## Five-Signature Pod-Churn Run

Run ID: `real-jax-xla-openfaas-pod-churn-five-20260518`

This run uses real OpenFaaS routing, Redis artifact import, and repeated pod
churn. Each signature has 64 invocations per mode, with a fresh pod at
invocations `1, 9, 17, 25, 33, 41, 49, 57`, giving eight median positions per
fresh pod.

Raw outputs:

```text
prototypes/jax-openfaas-redis-xla/.runs/real-jax-xla-openfaas-pod-churn-five-20260518/results/
```

Figures:

![JAX/XLA pod-churn invocation traces](figures/real-jax-xla-openfaas-pod-churn-invocation-traces.png)

![JAX/XLA pod-churn position medians](figures/real-jax-xla-openfaas-pod-churn-position-medians.png)

| Signature | Cold median baseline -> Redis cache | Compile/load p95 baseline -> Redis cache |
| --- | ---: | ---: |
| `dacapo-lusearch` | 481.7 ms -> 337.8 ms | 106.8 ms -> 25.4 ms |
| `dacapo-h2` | 459.0 ms -> 396.1 ms | 111.3 ms -> 35.7 ms |
| `dacapo-fop` | 470.0 ms -> 342.7 ms | 130.6 ms -> 29.9 ms |
| `dacapo-jython` | 474.8 ms -> 320.3 ms | 128.4 ms -> 31.9 ms |
| `dacapo-eclipse` | 488.8 ms -> 329.9 ms | 144.0 ms -> 25.6 ms |

This is the current JAX/XLA result to use for the non-JVM profile/artifact-cache
claim. The saved Redis artifact consistently cuts the first-request
compile/load peak after pod churn. Hot same-pod requests are expected to flatten
because both modes reuse an in-process compiled executable after the first
request.

Run ID: `20260512-142648`

This run measured the OpenFaaS/Redis version of the JAX/XLA persistent-cache
experiment across three DaCapo-shaped signatures:

- `dacapo-lusearch`
- `dacapo-h2`
- `dacapo-eclipse`

Environment notes:

- Local `kind` cluster named `openfaas`, running under Colima.
- OpenFaaS gateway on `http://127.0.0.1:8080`.
- Redis deployed in `openfaas-fn` as `profile-cache-redis`.
- Function image was built locally and loaded into kind:
  `jax-xla-redis:20260512-142648`.
- Deployment used `DEPLOY_BACKEND=direct` to avoid pushing workspace code to a
  public registry while still exposing the function through the OpenFaaS gateway.
- Each measurement used 5 cold-start trials. The harness deleted the function
  pod before each measured request, waited for the replacement pod to become
  Ready, then invoked `/work`.

Command shape:

```bash
OPENFAAS_GATEWAY=http://127.0.0.1:8080 \
OPENFAAS_NAMESPACE=openfaas \
FUNCTION_NAMESPACE=openfaas-fn \
IMAGE_PREFIX=jax-xla-redis \
PUSH_IMAGE=0 \
KIND_CLUSTER=openfaas \
INSTALL_REDIS=1 \
DEPLOY_BACKEND=direct \
SIGNATURES="dacapo-lusearch dacapo-h2 dacapo-eclipse" \
COLD_STARTS=5 \
EXECUTIONS=3 \
WATCHDOG_TIMEOUT=300s \
bash scripts/07_run_jax_openfaas_redis_xla.sh
```

## Summary

| Signature | Mode | Trials | HTTP p50 ms | HTTP p95 ms | JAX compile/load p50 ms | JAX compile/load p95 ms | Redis import p50 ms | Statuses |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `dacapo-lusearch` | Baseline | 5 | 455.0 | 650.0 | 123.4 | 135.3 | 0.0 | `200: 5` |
| `dacapo-lusearch` | Redis cache | 5 | 353.3 | 549.9 | 30.2 | 48.2 | 6.5 | `200: 5` |
| `dacapo-h2` | Baseline | 5 | 472.9 | 575.9 | 127.2 | 173.5 | 0.0 | `200: 5` |
| `dacapo-h2` | Redis cache | 5 | 466.6 | 1254.1 | 33.2 | 196.6 | 8.8 | `200: 5` |
| `dacapo-eclipse` | Baseline | 5 | 519.8 | 619.1 | 185.5 | 229.3 | 0.0 | `200: 5` |
| `dacapo-eclipse` | Redis cache | 5 | 432.3 | 644.2 | 39.4 | 52.1 | 7.8 | `200: 5` |

## Interpretation

Median JAX compile/load time improved for all three signatures:

| Signature | Compile/load p50 speedup | Compile/load p50 saved | HTTP p50 speedup | HTTP p50 saved |
| --- | ---: | ---: | ---: | ---: |
| `dacapo-lusearch` | 4.08x | 93.1 ms | 1.29x | 101.7 ms |
| `dacapo-h2` | 3.83x | 94.0 ms | 1.01x | 6.3 ms |
| `dacapo-eclipse` | 4.71x | 146.1 ms | 1.20x | 87.5 ms |

The `dacapo-h2` Redis-cache p95 has a single large outlier in only five trials:
one request measured 1254.1 ms HTTP latency and 196.6 ms JAX compile/load time.
The p50 still shows the expected cache benefit. Use a larger `COLD_STARTS` value
before treating p95 as stable.

## Baseline vs Pre-Populated Cache Curves

![HTTP latency by invocation](figures/jax-openfaas-redis-xla-http-latency-by-invocation.svg)

![JAX compile/load latency by invocation](figures/jax-openfaas-redis-xla-compile-load-by-invocation.svg)

These curves compare two steady modes: baseline cold starts with no persistent
cache, and Redis-cache cold starts after the cache was pre-populated. The
invocation number is a repeated-trial index, not a progressive learning chain.

## Progressive Cold-Start Chain

Run ID: `20260512-151100-progressive`

This is the chain-shaped run: invocation 1 starts with no Redis artifact, the
pod exports the JAX cache after the request, and each later fresh pod imports
the artifact written by the previous invocation. The same run directory also
contains a matching 8-invocation baseline line where every fresh pod runs with
`JAX_CACHE_MODE=baseline` and never imports Redis.

![Progressive HTTP latency by invocation](figures/jax-openfaas-redis-xla-progressive-http-latency-by-invocation.svg)

![Progressive JAX compile/load latency by invocation](figures/jax-openfaas-redis-xla-progressive-compile-load-by-invocation.svg)

The two graphs above start their timer after the replacement pod is Ready. They
isolate request handling and JAX compile/load behavior, but they intentionally
exclude the pod-start peak.

These graphs include the pod-start boundary:

![End-to-end cold-start latency by invocation](figures/jax-openfaas-redis-xla-progressive-cold-start-total-by-invocation.svg)

![Pod restart-to-ready latency by invocation](figures/jax-openfaas-redis-xla-progressive-pod-restart-by-invocation.svg)

| Signature | Baseline median HTTP ms | Progressive inv. 1 HTTP ms | Progressive inv. 2-8 median HTTP ms | Baseline median compile/load ms | Progressive inv. 1 compile/load ms | Progressive inv. 2-8 median compile/load ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `dacapo-lusearch` | 419.8 | 592.3 | 275.9 | 108.5 | 146.8 | 23.3 |
| `dacapo-h2` | 392.3 | 749.6 | 313.9 | 105.8 | 205.0 | 27.9 |
| `dacapo-eclipse` | 500.7 | 629.8 | 313.3 | 169.4 | 186.1 | 27.8 |

| Signature | Baseline total cold-start median ms | Progressive total cold-start invocation 1 ms | Progressive total cold-start median ms |
| --- | ---: | ---: | ---: |
| `dacapo-lusearch` | 3011.6 | 6731.3 | 5115.5 |
| `dacapo-h2` | 3249.8 | 6962.5 | 5139.8 |
| `dacapo-eclipse` | 5769.3 | 3680.0 | 3261.5 |

## Warmup-Style Single-Pod Curves

Run ID: `20260513-warmup`

This is the graph shape used in the JVM/OpenFaaS examples: restart once, wait
for one fresh pod, then send 20 sequential requests to that same pod. Invocation
1 is the first request in the fresh pod; invocations 2-20 are warm requests
inside the same process.

![JAX/OpenFaaS lusearch warmup](figures/jax-openfaas-redis-xla-warmup-dacapo-lusearch.svg)

![JAX/OpenFaaS h2 warmup](figures/jax-openfaas-redis-xla-warmup-dacapo-h2.svg)

![JAX/OpenFaaS eclipse warmup](figures/jax-openfaas-redis-xla-warmup-dacapo-eclipse.svg)

The full warmup plots above include the first cold/warmup request, so the warm
requests are visually compressed near the bottom. These warm-start-only zooms
start at invocation 2 and use a tight y-axis:

![JAX/OpenFaaS lusearch warm-start zoom](figures/jax-openfaas-redis-xla-warm-starts-dacapo-lusearch.svg)

![JAX/OpenFaaS h2 warm-start zoom](figures/jax-openfaas-redis-xla-warm-starts-dacapo-h2.svg)

![JAX/OpenFaaS eclipse warm-start zoom](figures/jax-openfaas-redis-xla-warm-starts-dacapo-eclipse.svg)

The clearest presentation is a split view: the top panel shows the cold
first-request peak and Redis-cache reduction, while the bottom panel zooms the
warm starts.

![JAX/OpenFaaS lusearch cold/warm split](figures/jax-openfaas-redis-xla-cold-warm-split-dacapo-lusearch.svg)

![JAX/OpenFaaS h2 cold/warm split](figures/jax-openfaas-redis-xla-cold-warm-split-dacapo-h2.svg)

![JAX/OpenFaaS eclipse cold/warm split](figures/jax-openfaas-redis-xla-cold-warm-split-dacapo-eclipse.svg)

| Signature | Mode | Invocation 1 latency ms | Warm invocation median ms | Invocation 1 compile/load ms |
| --- | --- | ---: | ---: | ---: |
| `dacapo-lusearch` | Baseline | 573.6 | 10.7 | 119.4 |
| `dacapo-lusearch` | Redis cache | 428.0 | 11.4 | 28.4 |
| `dacapo-h2` | Baseline | 621.4 | 5.7 | 127.3 |
| `dacapo-h2` | Redis cache | 518.1 | 9.1 | 36.7 |
| `dacapo-eclipse` | Baseline | 693.0 | 9.0 | 191.3 |
| `dacapo-eclipse` | Redis cache | 460.6 | 8.9 | 29.5 |

Use the split figures for the cold/warm-start comparison. They show two
separate effects:

- Redis/XLA persistent cache reduces the first request in a fresh pod: 25.4%
  for `dacapo-lusearch`, 16.6% for `dacapo-h2`, and 33.5% for
  `dacapo-eclipse`.
- Same-process JIT warmup makes invocations 2-20 much faster than invocation 1:
  baseline cold-to-warm drops by 53.6x for `dacapo-lusearch`, 109.2x for
  `dacapo-h2`, and 77.0x for `dacapo-eclipse`.

The Redis/XLA cache is a compilation-cache experiment, not a profile-guided
steady-state optimizer. The expected claim is that it lowers the cold
first-request compile/load peak. Warm invocations should be roughly flat and
similar across modes once both pods already have an in-process compiled JAX
executable. If the required claim is a warm steady-state improvement from a
cached profile, use the OpenFaaS Go PGO experiment instead of this JAX/XLA
persistent-cache run.

## Paper-Style Warmup Plots

These are the plots to use when the point is the cold -> warm -> hot shape.
They use a log y-axis like the Conference'17 figure, label the phase bands, and
draw the first-request cold spike explicitly. Each plot compares against the
`No saved warm state` baseline. The `Saved XLA warm state` line means the fresh
pod imports the Redis-backed XLA persistent cache instead of starting with no
saved compiler state.

![JAX/OpenFaaS lusearch paper warmup](figures/jax-openfaas-paper-warmup-dacapo-lusearch.svg)

![JAX/OpenFaaS h2 paper warmup](figures/jax-openfaas-paper-warmup-dacapo-h2.svg)

![JAX/OpenFaaS eclipse paper warmup](figures/jax-openfaas-paper-warmup-dacapo-eclipse.svg)

| Signature | Cold-to-hot latency ratio |
| --- | ---: |
| `dacapo-lusearch` | 55.0x |
| `dacapo-h2` | 111.8x |
| `dacapo-eclipse` | 77.0x |

These figures should not be used to claim a hot-path improvement from Redis/XLA
state. They show the correct comparison to not saving state, but the benefit is
on the cold compile/load peak. For hot-path saved-state improvement, use the Go
PGO profile-cache figures.

## Artifacts

Raw outputs:

```text
prototypes/jax-openfaas-redis-xla/.runs/20260512-142648/results/dacapo-lusearch/
prototypes/jax-openfaas-redis-xla/.runs/20260512-142648/results/dacapo-h2/
prototypes/jax-openfaas-redis-xla/.runs/20260512-142648/results/dacapo-eclipse/
prototypes/jax-openfaas-redis-xla/.runs/20260512-151100-progressive/results/dacapo-lusearch/
prototypes/jax-openfaas-redis-xla/.runs/20260512-151100-progressive/results/dacapo-h2/
prototypes/jax-openfaas-redis-xla/.runs/20260512-151100-progressive/results/dacapo-eclipse/
prototypes/jax-openfaas-redis-xla/.runs/20260513-warmup/results/dacapo-lusearch/
prototypes/jax-openfaas-redis-xla/.runs/20260513-warmup/results/dacapo-h2/
prototypes/jax-openfaas-redis-xla/.runs/20260513-warmup/results/dacapo-eclipse/
```

Each directory contains `baseline.csv`, `redis-cache.csv`, per-mode JSON
summaries, a combined `summary.json`, and `cold-start-summary.svg`.
