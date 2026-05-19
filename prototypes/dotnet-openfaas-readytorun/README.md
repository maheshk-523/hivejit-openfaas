# C#/.NET OpenFaaS ReadyToRun Prototype

This prototype runs the C#/.NET workload behind OpenFaaS and compares three
self-contained Linux function images:

```text
IL/JIT function image -> OpenFaaS gateway latency
ReadyToRun function image -> OpenFaaS gateway latency
NativeAOT function image -> OpenFaaS gateway latency
```

It is not the full static-PGO MIBC loop. It measures deployable C# AOT
artifact paths under the same gateway style as the Go/OpenFaaS run:
ReadyToRun is partial AOT that still keeps the .NET runtime, while NativeAOT
builds a native Linux binary in a Docker SDK stage.
On OpenFaaS Community Edition, deploying a new function may require a public
image reference. The default script keeps images local (`PUSH_IMAGE=0`) and
loads them into kind; pushing to an external registry should only be done when
the code export is explicitly approved.

Run with a local kind OpenFaaS cluster and a local .NET SDK:

```bash
DOTNET_BIN=/private/tmp/dotnet-sdk/dotnet \
PUSH_IMAGE=0 \
KIND_CLUSTER=openfaas \
DEPLOY_BACKEND=direct \
IMAGE_PREFIX=dotnet-openfaas-r2r \
bash prototypes/dotnet-openfaas-readytorun/run_openfaas_readytorun.sh
```

Use `VARIANTS="il r2r"` to skip NativeAOT, or `VARIANTS="nativeaot"` to test
only the full-AOT Docker build path.

Each run writes CSV, JSON summaries, and SVGs under `.runs/<run-id>/results`.

For real pod-churn raw traces, run the churn benchmark. This invokes OpenFaaS
for every plotted request and restarts the Kubernetes pod at irregular request
indices:

```bash
SCENARIOS="serve-hot serve-mixed" \
bash prototypes/dotnet-openfaas-readytorun/run_real_openwhisk_trace.sh
```

The generated `*-il-vs-aot-openfaas-pod-churn-raw.png` files are the real-data
graphs to use for results.
