# JVM DaCapo OpenFaaS Churn Harness

This prototype runs real DaCapo `h2`, `lusearch`, `eclipse`, `fop`, and `jython` inside one
long-lived JVM process behind OpenFaaS. Each HTTP request calls the DaCapo
harness in-process, so HotSpot/George JIT state survives across requests until
the OpenFaaS pod is deleted. The benchmark script deletes the pod at fixed
request indices to produce the container-churn resets in a warmup plot.

This replaces the older subprocess wrapper. Starting `java -jar dacapo.jar` per
request is useful as a packaging smoke test, but it cannot show JVM warmup
across requests because every request gets a fresh JVM.

## Local DaCapo Payload

The Docker image expects a minimal build context named `dacapo_payload`:

```text
dacapo.jar
dacapo/
  dat/h2
  dat/lusearch
  dat/eclipse
  dat/fop
  dat/jython
  jar/h2
  jar/lusearch
  jar/eclipse
  jar/fop
  jar/jython
  jar/lib
```

Prepare it from the local DaCapo release zip:

```bash
python3 prototypes/jvm-openfaas-dacapo/prepare_dacapo_payload.py \
  --dacapo-zip /Users/maheshk/dacapo/dacapo-23.11-MR2-chopin.zip \
  --dacapo-jar /Users/maheshk/dacapo/dacapo.jar \
  --force
```

The generated payload lives under
`prototypes/jvm-openfaas-dacapo/.cache/dacapo-payload`, which is ignored by git.
`lusearch` data is large; expect roughly a gigabyte of extracted payload.

## Run On OpenFaaS

For a local kind/OpenFaaS setup where images are loaded into kind directly:

```bash
export OPENFAAS_GATEWAY=http://127.0.0.1:8080
export FUNCTION_NAMESPACE=openfaas-fn
export KIND_CLUSTER=openfaas
export PUSH_IMAGE=0
export DEPLOY_BACKEND=direct

BENCHMARKS="h2 lusearch eclipse fop jython" \
SIZE=small \
INVOCATIONS=60 \
SEGMENT_LENGTH=20 \
./scripts/08_run_jvm_openfaas_dacapo_churn.sh
```

For a registry workflow:

```bash
export IMAGE_PREFIX=ttl.sh/dacapo-jvm-$USER
export PUSH_IMAGE=1
```

The script writes results under:

```text
prototypes/jvm-openfaas-dacapo/.runs/<run-id>/results/
```

Each benchmark gets:

- `<benchmark>.csv`: per-invocation latency plus pod metadata.
- `<benchmark>.json`: p50/p95 summary.
- `<benchmark>-warmup.svg`: raw latency, EWMA latency, and dashed pod-restart markers.

## Manual Build

```bash
DOCKER_BUILDKIT=1 docker build \
  --build-context dacapo_payload=prototypes/jvm-openfaas-dacapo/.cache/dacapo-payload \
  -t dacapo-jvm:local \
  prototypes/jvm-openfaas-dacapo
```

The function accepts query parameters:

```text
benchmark=h2|lusearch|eclipse|fop|jython
size=small|default|large
iterations=1
threads=1
validation=none|ignore|default
pre_gc=true|false
digest_output=true|false
```

Example:

```bash
curl 'http://127.0.0.1:8080/function/dacapo-jvm/run?benchmark=lusearch&size=small&iterations=1&threads=1'
```

`validation=none` is the default for the HTTP harness because the DaCapo
stdout/stderr digest checks are not meaningful after output is captured inside a
long-lived server process. Use `validation=ignore` to run validation without
failing the HTTP request, or `validation=default` only when running a benchmark
mode whose stdout digests are known to match this wrapper.
