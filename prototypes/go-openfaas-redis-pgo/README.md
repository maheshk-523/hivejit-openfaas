# Go OpenFaaS Redis PGO Prototype

This prototype mirrors the JVM/OpenFaaS profile-cache shape for Go:

```text
OpenFaaS nopgo Go function -> warm traffic -> CPU pprof -> Redis profile cache
-> go tool pprof merge -> Redis merged profile -> go build -pgo -> redeploy PGO image
```

Go does not load a profile into an already-built binary, so Redis stores the warm-profile
artifacts and the controller rebuilds the next OpenFaaS image with `go build -pgo`.

## Run

Set the same OpenFaaS environment used by the JVM scripts, plus an image prefix your
cluster can pull:

```bash
export OPENFAAS_GATEWAY=http://127.0.0.1:8080
export OPENFAAS_NAMESPACE=openfaas
export FUNCTION_NAMESPACE=openfaas-fn
export IMAGE_PREFIX=ttl.sh/go-pgo-redis-$USER
```

For a local kind cluster, avoid pushing images and load them directly into the cluster:

```bash
export IMAGE_PREFIX=go-pgo-redis
export PUSH_IMAGE=0
export KIND_CLUSTER=openfaas
```

If you do not already have the Redis profile-cache service in the function namespace:

```bash
export INSTALL_REDIS=1
```

Then run:

```bash
cd /Users/maheshk/Documents/New\ project\ 5/prototypes/go-openfaas-redis-pgo
./run_openfaas_redis_pgo.sh
```

Useful knobs:

```bash
PROFILE_ITERS="5 10 20" \
BENCHMARKS="router dacapo-lusearch dacapo-eclipse dacapo-h2" \
PROFILE_SECONDS=20 \
PROFILE_LOAD_REQUESTS=120 \
MEASURE_REQUESTS=80 \
HANDLER_REQUESTS=350000 \
./run_openfaas_redis_pgo.sh
```

`BENCHMARKS` accepts `router` plus Go-native DaCapo-shaped aliases:
`dacapo-lusearch`, `dacapo-eclipse`, and `dacapo-h2`. These are not the JVM
DaCapo jar workloads; they keep CPU time inside the Go binary so `runtime/pprof`
and `go build -pgo` measure the Go profile-cache mechanism instead of a Go
wrapper waiting for a Java subprocess.

## What The Function Exposes

- `POST /work`: runs the selected Go workload and returns JSON timing/checksum data.
  Accepts JSON like `{"benchmark":"dacapo-lusearch","requests":350000}`.
- `GET /profile/capture?seconds=20&key=...`: captures a CPU profile while warm traffic runs and stores the pprof bytes in Redis.
- `GET /profile/fetch?key=...`: fetches Redis profile bytes through the function gateway.
- `POST /profile/put?key=...`: stores merged profile bytes in Redis.
- `GET /profile/ping`: validates Redis connectivity from inside the function pod.

Keep the profile endpoints behind your local gateway/debug environment. They are for the
experiment, not a public function surface.

## Outputs

Each run writes under `.runs/<run-id>/`. With one benchmark, outputs are written
directly under `profiles/` and `results/`; with multiple benchmarks, each benchmark
gets its own subdirectory.

- `profiles/raw/invoke-N.pprof`: raw warm profiles captured through OpenFaaS and Redis.
- `profiles/<N>-profiles/merged.pprof`: merged Go PGO profile.
- `results/go-openfaas-nopgo.*`: baseline latency CSV, summary JSON, and SVG.
- `results/go-openfaas-pgo-N.*`: PGO latency CSV, summary JSON, and SVG.

Redis keys use this shape by default:

```text
go-pgo:go-pgo-redis:raw:<run-id>:<benchmark>:<N>
go-pgo:go-pgo-redis:merged:<run-id>:<benchmark>:<N>
```
