# JAX/Flax MNIST Persistent Compilation Cache Writeup

## Bottom line

This benchmark is a real Flax/JAX workload used to evaluate JAX persistent
compilation cache behavior. It is not a benchmark of Flax as a whole library.
The workload is a Flax Linen CNN training-step-style function running on real
MNIST image data.

The main result from `flax-mnist-real-10trial` is:

| Metric | Baseline JIT | Persistent cache hit | Result |
| --- | ---: | ---: | ---: |
| Compile/load p50 | 225.72 ms | 7.14 ms | 31.6x faster |
| Startup plus first request p50 | 337.31 ms | 124.70 ms | 2.7x faster |
| First execute p50 | 69.23 ms | 66.70 ms | roughly unchanged |

The cache primarily removes XLA compilation/load time from a cold fresh process.
It does not make the already-compiled model execution materially faster.

## Workload

Scenario: `flax-mnist-cnn-train-real`

Mismatch control: `flax-mnist-cnn-train-real-mismatch`

The workload uses the real MNIST dataset at:

```text
prototypes/jax-real-workload-cache/data/mnist.npz
```

The dataset contains 60,000 training images and 10,000 test images. Each image
is a 28x28 grayscale handwritten digit with a label from 0 to 9.

The Flax model is:

```text
Conv 32, 3x3
ReLU
Average pool
Conv 64, 3x3
ReLU
Average pool
Flatten
Dense 256
ReLU
Dense 10
```

The compiled JAX function runs:

```text
real images + real labels + Flax params
  -> Flax CNN forward pass
  -> cross-entropy loss
  -> accuracy
  -> jax.value_and_grad backward pass
  -> loss, accuracy, gradient norm
```

This exercises the normal Python/Flax/JAX path:

```text
Python code -> JAX tracing -> jaxpr -> lowering -> StableHLO/XLA compilation
  -> executable load -> first execution
```

## Measurement modes

`baseline`
: Fresh Python process with no persistent JAX compilation cache.

`persistent-cache-populate`
: Fresh Python process with persistent compilation cache enabled. This run
compiles the matching workload and writes cache files.

`persistent-cache-reuse`
: Fresh Python process that restores the previously exported cache artifact at
the same cache path before running the workload.

`mismatch-control`
: Fresh Python process that restores the same cache artifact but runs a changed
batch size. The mismatch should miss the existing cache entry and compile again.

## Final command

```bash
BOOTSTRAP=0 \
RUN_ID=flax-mnist-real-10trial \
TRIALS=10 \
EXECUTIONS=1 \
SCENARIOS='flax-mnist-cnn-train-real' \
MISMATCH_SCENARIOS='flax-mnist-cnn-train-real-mismatch' \
bash prototypes/jax-real-workload-cache/run_jax_real_workload_cache.sh
```

The final graph was generated with:

```bash
python3 scripts/plot_jax_flax_real_combined.py \
  --results-dir prototypes/jax-real-workload-cache/results/flax-mnist-real-10trial \
  --scenario flax-mnist-cnn-train-real \
  --out docs/figures/jax-flax-mnist-real-combined.png
```

## Final numbers

Baseline, no persistent cache, median over 10 trials:

```text
lower:                    42.36 ms
compile/load:             225.72 ms
first execute:             69.23 ms
startup + first request:  337.31 ms
```

Persistent cache reuse, median over 10 trials:

```text
lower:                    40.47 ms
compile/load:               7.14 ms
first execute:             66.70 ms
startup + first request:  124.70 ms
```

Mismatch control, batch size changed from 128 to 160:

```text
compile/load median:       215.74 ms
startup + first request:   348.24 ms
```

The mismatch result matters because it shows that the cache is tied to the
compiled workload profile. Changing the batch size causes a new compile instead
of reusing the old executable.

## Figure

Final combined figure:

```text
docs/figures/jax-flax-mnist-real-combined.png
```

The left line chart shows cold-start behavior. The first point is the
first-request path, while later points show execution-scale timing after the
compile-heavy first request.

The grouped bars compare median first-request, compile/load, and execution
components.

The stacked bars show one concrete iteration-1 phase breakdown: trace/lower,
compile/load, and execute.

## Correct interpretation

The defensible claim is:

> For this real Flax CNN MNIST training-step workload on CPU, JAX persistent
> compilation cache reduced median compile/load time from 225.7 ms to 7.1 ms
> and reduced median first-request latency from 337.3 ms to 124.7 ms.

Do not claim that Flax execution is 31x faster. The 31.6x number applies to the
compile/load phase. The end-to-end first-request speedup is 2.7x.

This result does not measure training convergence, GPU/TPU behavior, large
model checkpoint loading, data-loader throughput, or all possible Flax models.
It measures cold-start compilation/cache behavior for one real Flax workload.
