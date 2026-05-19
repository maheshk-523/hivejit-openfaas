# Julia OpenFaaS Redis Precompile-Cache Warmup Experiment

## What This Shows

Julia has the same warmup problem as the JVM in serverless environments.
Every fresh container must JIT-compile method specialisations from scratch,
causing a latency spike on the first few requests that is visible in the
per-invocation graph.  This prototype demonstrates that caching Julia's
**precompile trace** in Redis and replaying it at pod startup eliminates most
of that spike — producing the same graph shape as the OpenWhisk/JVM warmup
paper but for a completely different language and compiler.

```
Cold pod (no cache) → LLVM JIT on first call → high first-request latency
Cold pod (Redis cache) → include(precompile.jl) at startup → near-warm first request
```

## Runtime Optimisation Path

| Stage | JVM / HiveJIT | Julia |
|-------|---------------|-------|
| Compiler | HotSpot tiered JIT | LLVM via Julia's JIT |
| Runtime info | MethodData, type profiles, branch counts | Method specialisation types from `--trace-compile` |
| Artifact | MethodData blob | `precompile(f, (T,…))` statement file |
| Cache store | Redis | Redis |
| Cache load | MethodData import → skip lower tiers | `include(precompile.jl)` → eager LLVM compilation |
| Serverless system | OpenFaaS | OpenFaaS |

## DaCapo-Analog Workloads

| DaCapo benchmark | Julia analog | What it exercises |
|-----------------|--------------|-------------------|
| `lusearch` | `run_lusearch` | Boolean inverted-index search over a generated string corpus |
| `h2` | `run_h2` | In-memory key-value store with CRUD, scan, and range-sum operations |
| `eclipse` | `run_eclipse` | `Meta.parse` on Julia expression templates + AST node/depth/call counting |

These workloads are generated in-process — no DaCapo `.jar` or data files
are needed.  They exercise enough generic functions and type dispatch that
Julia's first-call LLVM compilation produces a visible latency bump.

## Experiment Phases

```
Phase 0 – baseline
  Deploy JULIA_CACHE_MODE=baseline.
  Each pod restart → full JIT warmup (visible spike).

Phase 1 – populate
  Deploy JULIA_CACHE_MODE=populate (Julia --trace-compile active).
  Run POPULATE_INVOCATIONS warm requests for EACH workload on the SAME pod
  (no restart between workloads) to accumulate a combined trace.
  Hit /_/cache/push → handler calls cachectl.py push → Redis stores trace.

Phase 2 – redis
  Deploy JULIA_CACHE_MODE=redis.
  entrypoint.sh: cachectl.py pull → /tmp/julia-precompile.jl
  handler.jl: include(precompile.jl) before HTTP server starts.
  Each pod restart → precompile trace replayed → LLVM compilation done
  before first HTTP request → smaller warmup spike.

Phase 3 – AOT profile-cache comparison
  Build PackageCompiler sysimages after 5 and 10 profile runs.
  Deploy JULIA_CACHE_MODE=sysimage.
  Compare baseline vs sysimage5 vs sysimage10 with the same pod churn cadence.
  This isolates the Julia compilation benefit from the OpenFaaS/container
  cold path in the end-to-end latency.
```

## Quick Start (local kind + OpenFaaS)

```bash
export OPENFAAS_GATEWAY=http://127.0.0.1:8080
export FUNCTION_NAMESPACE=openfaas-fn
export KIND_CLUSTER=openfaas
export PUSH_IMAGE=0
export DEPLOY_BACKEND=direct

WORKLOADS="lusearch h2 eclipse" \
SIZE=1 \
INVOCATIONS=60 \
SEGMENT_LENGTH=20 \
bash prototypes/julia-openfaas-redis-precompile/run_openfaas_redis_julia_precompile.sh
```

Or via the shortcut script:

```bash
bash scripts/09_run_julia_openfaas_redis_precompile.sh
```

To compare baseline against AOT profile caching with 5 and 10 profiles:

```bash
export OPENFAAS_GATEWAY=http://127.0.0.1:8080
export FUNCTION_NAMESPACE=openfaas-fn
export KIND_CLUSTER=openfaas
export PUSH_IMAGE=0
export DEPLOY_BACKEND=direct

WORKLOADS="lusearch h2 eclipse" \
SIZE=1 \
INVOCATIONS=120 \
SEGMENT_LENGTH=30 \
AOT_PROFILE_COUNTS="5 10" \
bash prototypes/julia-openfaas-redis-precompile/run_aot_profile_cache_comparison.sh
```

For a real run with the same request scale and irregular churn positions as the
reference serverless warmup figure, use the real trace wrapper. This invokes
OpenFaaS for every row in the CSV and restarts the Kubernetes pod at each
dashed-line position:

```bash
WORKLOADS="lusearch h2 eclipse" \
bash prototypes/julia-openfaas-redis-precompile/run_real_openwhisk_trace.sh
```

The generated `*-baseline-vs-aot-openfaas-pod-churn-raw.png` files are the
real-data graphs to use for results.

## Manual Docker Build

```bash
DOCKER_BUILDKIT=1 docker build \
  -t julia-precompile:local \
  prototypes/julia-openfaas-redis-precompile
```

## HTTP Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /run?workload=lusearch\|h2\|eclipse&size=1\|2\|3` | Run a DaCapo-analog workload |
| `GET /_/health` | Health check; returns JSON with mode and uptime |
| `GET /_/cache/push` | Export `--trace-compile` file to Redis (populate mode) |

## Output Files

Results land under `.runs/<RUN_ID>/results/`:

```
<workload>-baseline.csv          per-invocation latency rows
<workload>-baseline.json         p50/p95 summary
<workload>-baseline-warmup.png   raw latency with pod-restart markers
<workload>-redis.csv
<workload>-redis.json
<workload>-redis-warmup.png
<workload>-sysimage5.csv
<workload>-sysimage5.json
<workload>-sysimage5-warmup.png
<workload>-sysimage10.csv
<workload>-sysimage10.json
<workload>-sysimage10-warmup.png
<workload>-aot-comparison.png
<workload>-baseline-vs-aot-openfaas-pod-churn-raw.png
<workload>-real-openwhisk-shape-eval.json
```

The plot layout is identical to the JVM DaCapo churn plots so both can be
placed side-by-side in a paper. By default the PNGs show raw per-request
latency only; smoothed lines are opt-in.

`evaluate_warmup_shape.py` checks whether a trace has enough requests, enough
churn points, and a multi-request raw decay tail after each churn. This prevents
a single cold spike plus flat steady state from being mistaken for the reference
behavior.

## Interpreting the Graph

- **Blue line** – raw per-invocation HTTP latency
- **Dashed blue vertical lines** – pod restarts (container churn points)

In the baseline run, each dashed line is followed by a latency spike as
Julia JIT-compiles HTTP.jl request handling and the workload methods.
In the redis-cache run, the spike is much smaller because the precompile
trace has already compiled those methods at startup.

## Why Julia Is the Right Comparison Point

The images from the PrismX survey (section 7.2) categorise Julia-style JIT
as *"Type Specialisation for interpreted languages"* (refs [14,15,57] in the
paper).  Julia's JIT is method-dispatch-driven: it compiles a new LLVM
function body for each unique combination of argument types.  That is
structurally identical to HotSpot's type-specialised compilation, so the
serverless warmup curve and the cache benefit transfer directly.  A side-by-side
graph of `lusearch` on Julia vs `lusearch` on the JVM would show the same
warmup shape, confirming that the profile-artifact-cache idea is not
JVM-specific.
