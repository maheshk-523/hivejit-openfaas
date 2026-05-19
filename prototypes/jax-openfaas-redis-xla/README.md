# JAX/XLA OpenFaaS Redis Cold-Start Prototype

This prototype puts the local JAX/XLA persistent-cache experiment behind an
OpenFaaS function and uses Redis as the cross-pod artifact store:

```text
OpenFaaS baseline pod -> JAX trace/compile -> XLA executable
OpenFaaS populate pod -> JAX persistent cache -> tar.gz artifact -> Redis
OpenFaaS redis pod -> Redis artifact -> local JAX cache -> first request compile/load
```

It is intended to measure first-request behavior after a fresh OpenFaaS pod
starts, comparing:

- `baseline`: no JAX persistent compilation cache configured.
- `redis-cache`: a fresh pod pre-pulls a Redis-backed cache artifact before the
  Python/JAX handler starts.

The tensor signatures and kernels match
`prototypes/jax-xla-runtime-specialization`.

## Run

Use the same local OpenFaaS/kind setup as the JVM `openfaas-setup` demo or the
existing Go OpenFaaS Redis prototype. For a local kind cluster where images are
loaded directly:

```bash
export OPENFAAS_GATEWAY=http://127.0.0.1:8080
export OPENFAAS_NAMESPACE=openfaas
export FUNCTION_NAMESPACE=openfaas-fn
export IMAGE_PREFIX=jax-xla-redis
export PUSH_IMAGE=0
export KIND_CLUSTER=openfaas
export INSTALL_REDIS=1
export DEPLOY_BACKEND=direct

cd /Users/maheshk/Documents/New\ project\ 5
./scripts/07_run_jax_openfaas_redis_xla.sh
```

`DEPLOY_BACKEND=direct` creates the OpenFaaS-compatible Kubernetes Deployment
and Service directly, using the local image loaded into kind. This avoids the
OpenFaaS CE public-image restriction while still routing requests through the
OpenFaaS gateway.

If you are using the Redis service from
`https://github.com/ucla-progsoftsys/openfaas-setup`, leave `INSTALL_REDIS=0`
and point at that service instead:

```bash
export REDIS_ADDR=redis.openfaas.svc.cluster.local:6379
```

For a public registry workflow:

```bash
export IMAGE_PREFIX=ttl.sh/jax-xla-redis-$USER
export PUSH_IMAGE=1
```

Useful knobs:

```bash
SIGNATURES="dacapo-lusearch dacapo-h2 dacapo-fop dacapo-jython dacapo-eclipse" \
COLD_STARTS=20 \
EXECUTIONS=3 \
JAX_PACKAGE="jax[cpu]" \
./scripts/07_run_jax_openfaas_redis_xla.sh
```

For OpenFaaS pod-churn traces with repeated cold, warmup, and hot positions:

```bash
MEASURE_MODE=pod-churn \
SIGNATURES="dacapo-lusearch dacapo-h2 dacapo-fop dacapo-jython dacapo-eclipse" \
POD_CHURN_INVOCATIONS=64 \
POD_CHURN_SEGMENT_LENGTH=8 \
EXECUTIONS=3 \
./scripts/07_run_jax_openfaas_redis_xla.sh
```

## What The Function Exposes

- `POST /work`: compiles/loads and executes one selected JAX signature.
- `POST /cache/populate`: compiles the selected signature with a persistent
  cache enabled, archives the cache directory, and stores it in Redis.
- `GET /cache/ping`: validates Redis connectivity from inside the function pod.
- `GET /cache/metadata`: reports local cache and Redis import/export metadata.
- `GET /profile`: returns the runtime signature profile used by the workload.

The Docker entrypoint runs `cachectl.py pull` before `fwatchdog` starts the
Python handler. In `redis-cache` mode this means the Redis artifact is restored
before JAX is imported or configured.

## Outputs

Each run writes under `.runs/<run-id>/results/<signature>/`:

```text
populate.json
baseline.csv
baseline.json
redis-cache.csv
redis-cache.json
summary.json
cold-start-summary.svg
```

The CSV includes both coarse and inner timings:

- `restart_ms`: pod delete to replacement pod Ready.
- `http_latency_ms`: full first request through the OpenFaaS gateway.
- `handler_ms`: handler-side request time.
- `import_ms`: Redis artifact fetch/extract time from the entrypoint metadata.
- `compile_or_load_ms`: JAX lower/compile call duration.

Use `http_latency_ms` for user-visible first-request latency and
`compile_or_load_ms` to isolate whether XLA cache reuse is helping inside the
handler.
