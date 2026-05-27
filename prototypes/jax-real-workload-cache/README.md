# JAX Real-Workload Persistent Cache Prototype

This prototype is the end-to-end JAX version of the profile-artifact cache
idea. The final benchmark path uses a real Flax CNN training-step workload on
real MNIST image data, so the cache result is measured against a normal
Python/Flax/JAX program rather than a DaCapo-shaped tensor signature.

It includes self-contained JAX scientific kernels and Flax model workloads:

```text
scenario/config profile
  -> JAX trace/lower/compile
  -> JAX persistent compilation cache
  -> compressed artifact in a local object-store directory
  -> fresh Python process restores the artifact at the same cache mount path
  -> first request compile/load timing drops if the cache hits
```

The default miniapp models coupled radial transport channels with finite-volume
operators, iterative solver steps, static scenario fields, and an optional
JAX-compiled surrogate correction. The PyHPC-style scenarios model geophysical
array kernels. The final Flax scenario, `flax-mnist-cnn-train-real`, loads
checked-in MNIST images from `data/mnist.npz` and compiles a Flax Linen CNN
forward/loss/backward pass with `jax.value_and_grad`.

## Run

```bash
bash prototypes/jax-real-workload-cache/run_jax_real_workload_cache.sh
```

Useful knobs:

```bash
TRIALS=5 \
EXECUTIONS=3 \
SCENARIOS="torax-pulse-64 torax-mlsurrogate-64 torax-control-96" \
bash prototypes/jax-real-workload-cache/run_jax_real_workload_cache.sh
```

Final real-data Flax cold-start run:

```bash
BOOTSTRAP=1 \
RUN_ID=flax-mnist-real-10trial \
TRIALS=10 \
EXECUTIONS=1 \
SCENARIOS="flax-mnist-cnn-train-real" \
MISMATCH_SCENARIOS="flax-mnist-cnn-train-real-mismatch" \
bash prototypes/jax-real-workload-cache/run_jax_real_workload_cache.sh
```

If you already have a Python environment with JAX:

```bash
PYTHON_BIN=/path/to/python BOOTSTRAP=0 \
bash prototypes/jax-real-workload-cache/run_jax_real_workload_cache.sh
```

## Outputs

Each run writes:

```text
profiles/<run>/scenario-profile.json
artifacts/<run>/object-store/<cache-key>.tar.gz
artifacts/<run>/stable-jax-cache/
artifacts/<run>/hlo/
results/<run>/baseline.csv
results/<run>/persistent-cache-populate.csv
results/<run>/persistent-cache-reuse.csv
results/<run>/mismatch-control.csv
results/<run>/summary.json
```

Figures are written under `docs/figures/`:

```text
jax-real-workload-cache-phase-breakdown.svg
jax-real-workload-cache-compile-load.svg
jax-real-workload-cache-speedup.svg
jax-flax-mnist-real-combined.png
jax-flax-mnist-real-bars.png
jax-flax-mnist-real-clean.png
```

## Modes

| Mode | Meaning |
| --- | --- |
| `baseline` | Fresh Python process, no persistent JAX compilation cache. |
| `persistent-cache-populate` | Fresh process compiles the selected scenario profile and writes JAX cache files. |
| `persistent-cache-reuse` | Fresh process restores the compressed cache artifact before compilation. |
| `mismatch-control` | Fresh process restores the same artifact but runs a changed scenario profile, demonstrating profile-specific cache behavior. |

## Metrics

The CSV separates:

```text
artifact_import_ms
trace_ms
lower_ms
compile_or_load_ms
first_execute_ms
handler_ms
startup_plus_first_request_ms
cache_files_after
cache_bytes_after
archive_bytes
```

This matters because JAX's persistent compilation cache stores compiled program
artifacts. It can reduce the XLA compile/load phase, but Python import time,
scenario construction, tracing, and lowering may still remain visible. By
default `lower_ms` includes JAX tracing plus lowering because that is the stable
persistent-cache path for the current JAX release; pass `--split-trace` to
`workload.py measure` only when diagnosing tracing behavior.

The local runner restores into the same cache directory path used during
population. This mirrors the OpenFaaS design where every pod mounts the artifact
at the same in-container path, for example `/profiles/jax-cache`.
