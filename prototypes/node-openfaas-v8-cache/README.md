# Node/V8 OpenFaaS Redis Artifact Cache

This prototype targets the same pod-churn shape as the .NET OpenFaaS runs, but
uses a V8 runtime artifact:

```text
baseline execution -> V8 cachedData export -> Redis artifact -> fresh pod import -> execution
```

The baseline compiles a generated serverless handler source in every fresh pod.
The cached variant imports a Redis-backed V8 `cachedData` bytecode artifact
before compiling the same source. This is a public Node/V8 artifact cache, not a
full optimized-code or feedback-vector checkpoint.

Run from the repository root with the local OpenFaaS gateway port-forwarded:

```bash
RUN_ID=real-node-v8-openfaas-pod-churn-$(date +%Y%m%d) \
OPENFAAS_GATEWAY=http://127.0.0.1:8080 \
FUNCTION_NAMESPACE=openfaas-fn \
OPENFAAS_NAMESPACE=openfaas \
IMAGE_PREFIX=node-v8-cache \
PUSH_IMAGE=0 \
KIND_CLUSTER=openfaas \
./prototypes/node-openfaas-v8-cache/run_openfaas_v8_cache.sh
```

The generated source size is controlled with `FUNCTION_COUNT`; runtime work is
controlled with `ROUNDS` and `REQUEST_INVOCATIONS`.

For the five DaCapo-shaped workload evaluation used by the .NET comparison:

```bash
RUN_ID=real-node-v8-openfaas-pod-churn-five-$(date +%Y%m%d) \
WORKLOADS="lusearch h2 fop jython eclipse" \
FUNCTION_COUNT=20000 \
ROUNDS=1000 \
REQUEST_INVOCATIONS=2 \
CHURN_INVOCATIONS=40 \
CHURN_SEGMENT_LENGTH=8 \
./prototypes/node-openfaas-v8-cache/run_openfaas_v8_cache.sh
```
