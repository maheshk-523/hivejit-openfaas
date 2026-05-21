# JAX-Native XLA First-Iteration Figure

This figure is the JAX/XLA analog of the C# warmup comparison, using
JAX-native kernels that exercise common XLA lowering paths.

## Workloads

The benchmark set is intentionally aligned with common JAX/XLA usage:

| Workload | Why it belongs in a JAX comparison |
| --- | --- |
| Dense matmul | Core linear algebra path used by neural networks and scientific code. |
| MLP forward | Typical fused dense-layer inference pattern. |
| Scaled dot-product attention | Transformer-style tensor contraction plus softmax. |
| Conv2D feature map | CNN/image-style convolution lowered through XLA. |
| `lax.scan` recurrent loop | JAX-native staged loop/control-flow workload. |

## What Was Measured

The script compares two fresh-process states:

| Mode | Meaning |
| --- | --- |
| JIT, no persistent cache | A fresh Python process compiles the JAX function without a persistent XLA cache. |
| JIT + saved XLA cache | A populate process first compiles the same kernels into JAX's persistent compilation cache; later fresh Python processes reuse that saved cache. |

For each mode, the script runs five fresh Python processes. Each process runs
the first 10 iterations of every kernel. The plot shows the median at each
iteration across those fresh-process repeats.

The primary figure plots `compile/load + execute`, which is closest to a
per-iteration benchmark latency. A second figure plots only `compile/load` to
isolate the cache effect.

## Generated Artifacts

Source run:

```text
prototypes/jax-native-xla-first-requests/results/20260518-jax-native-first-requests-r5
```

Figures and summaries:

```text
docs/figures/jax-native-xla-first-requests-medians.png
docs/figures/jax-native-xla-first-requests-medians-summary.json
docs/figures/jax-native-xla-first-compile-medians.png
docs/figures/jax-native-xla-first-compile-medians-summary.json
```

## Reproduction

```bash
python3 -B scripts/plot_jax_xla_first_requests.py \
  --repeats 5 \
  --run-dir prototypes/jax-native-xla-first-requests/results/20260518-jax-native-first-requests-r5
```

If the run directory already exists, rerun with a new directory name. The script
uses `JAX_PYTHON` when set; otherwise it uses the existing local JAX virtualenv
under `prototypes/jax-xla-runtime-specialization/.venv`.
