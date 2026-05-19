# Python OpenFaaS Redis Scale Verifier

This project verifies the Python profile-specialization cache at a larger
OpenFaaS scale than the single-function lifecycle runner.

It reuses the existing Python specialization function from
`prototypes/python-profile-specialization`:

```text
populate pod -> profile generic Python routes -> generate specialized.py -> Redis
saved pod startup -> pull specialized.py from Redis -> first request imports artifact
baseline pod startup -> no saved artifact -> generic interpreter path
```

The scale verifier deploys many one-replica OpenFaaS-compatible functions per
benchmark and treatment. Each measurement wave deletes all pods for one
benchmark/treatment, waits for fresh pods, then sends request 1..N to every
function through the OpenFaaS gateway. This gives a large sample of fresh pod
lifecycles while preserving the "request in fresh pod" x-axis used in the paper
style figure.

## Run

Use the same OpenFaaS/kind setup as the existing Redis prototypes. For a local
kind cluster:

```bash
export OPENFAAS_GATEWAY=http://127.0.0.1:8080
export FUNCTION_NAMESPACE=openfaas-fn
export KIND_CLUSTER=openfaas
export PUSH_IMAGE=0
export INSTALL_REDIS=1

cd /Users/maheshk/Documents/New\ project\ 5
./scripts/10_run_python_openfaas_redis_scale.sh
```

The default run is intentionally bigger than the lifecycle smoke test:

```text
benchmarks:        dacapo-lusearch dacapo-h2 dacapo-eclipse dacapo-jython dacapo-fop
shards:            24 functions per benchmark/treatment
waves:             4 fresh-pod waves
requests per pod:  8
```

That is 96 fresh pods per benchmark/treatment and 7,680 gateway requests across
the five benchmarks. Increase the sample size with:

```bash
SHARDS=48 WAVES=8 REQUESTS_PER_POD=8 \
BENCHMARKS="dacapo-lusearch dacapo-h2 dacapo-eclipse dacapo-jython dacapo-fop" \
./scripts/10_run_python_openfaas_redis_scale.sh
```

Useful knobs:

```bash
REQUESTS=12000                 # work inside each /work invocation
PROFILE_REQUESTS=36000         # work used to build the specialization artifact
PROFILE_ITERS=3                # exported profiles per benchmark
CONCURRENCY=48                 # concurrent gateway calls per request position
FUNCTION_PREFIX=py-spec-scale  # OpenFaaS function name prefix
SKIP_BUILD=1                   # reuse IMAGE
SKIP_POPULATE=1                # reuse Redis artifacts for RUN_ID/artifact prefix
SKIP_DEPLOY=1                  # reuse existing deployed matrix
CLEANUP_AT_END=1               # delete generated functions after measurement
```

If your OpenFaaS gateway requires basic auth and the standard `basic-auth`
secret exists in the `openfaas` namespace, the run script loads it automatically.
You can also set `OPENFAAS_USERNAME` and `OPENFAAS_PASSWORD` directly.

## Outputs

Each run writes raw artifacts under:

```text
prototypes/python-openfaas-redis-scale/.runs/<run-id>/
```

Important files:

```text
results/large-scale.csv       # every gateway invocation
results/summary.json          # median-position wins and saved percentages
results/populate-*.json       # Redis artifact export metadata
k8s/python-scale-*.json       # generated Kubernetes manifests
```

The paper-style aggregate figure is written to:

```text
docs/figures/python-openfaas-redis-scale-verification.svg
docs/figures/python-openfaas-redis-scale-verification-summary.json
```

If `rsvg-convert` is installed, the run script also writes a PNG beside the SVG.

## What To Check

For a successful saved-artifact run:

- `status` is `200` for both treatments.
- `used_artifact=true` for saved rows.
- `cache_imported=true` and `artifact_found=true` for saved rows.
- `checksum` values match between baseline and saved rows for the same
  benchmark/request seed.
- Saved medians should win most request positions, especially the first request
  after each fresh pod starts.

The summary reports:

```text
median_position_wins
cold_saved_pct
hot_saved_pct
samples_per_request
```

These are the values that correspond to the chart annotation in the supplied
OpenFaaS Python profile-specialization cache figure.

## Cleanup

The generated manifest paths are printed at the end of a run. To remove the
scale matrix manually:

```bash
kubectl delete -f prototypes/python-openfaas-redis-scale/.runs/<run-id>/k8s/python-scale-matrix.json
kubectl delete -f prototypes/python-openfaas-redis-scale/.runs/<run-id>/k8s/python-scale-populate.json
```
