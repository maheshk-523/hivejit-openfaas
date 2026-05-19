# JAX/XLA Runtime-Specialization Prototype

This prototype implements the JAX/XLA domain suggested by the online
optimization taxonomy:

```text
runtime tensor signature -> JAX trace -> XLA executable -> persistent compilation cache -> fresh-process reuse
```

The exported runtime information is the set of tensor shapes, dtypes, and static
arguments observed for representative calls. JAX traces the Python function for
those signatures, and XLA compiles domain-specific tensor code. The persistent
compilation cache is the artifact that a future cold process can import.

The default signatures are DaCapo-shaped tensor kernels:

- `dacapo-lusearch`
- `dacapo-h2`
- `dacapo-eclipse`

## Taxonomy Fit

- Runtime code specialization: `jax.jit` specializes the function to observed
  shapes, dtypes, and static values.
- Domain-specific specialization: XLA applies tensor-compiler optimizations
  such as HLO rewrites, fusion, layout decisions, and backend code generation.
- Configuration/runtime tuning: on GPU, XLA can also autotune generated kernels.
  This CPU-local prototype focuses on the portable compilation-cache part.

## Run

```bash
bash prototypes/jax-xla-runtime-specialization/run_jax_xla.sh
```

Optional:

```bash
SIGNATURES="dacapo-lusearch dacapo-h2 dacapo-eclipse" \
ITERATIONS=1 EXECUTIONS=3 \
bash prototypes/jax-xla-runtime-specialization/run_jax_xla.sh
```

The script bootstraps a local `.venv` with JAX CPU if needed. To use an existing
environment:

```bash
PYTHON_BIN=/path/to/python BOOTSTRAP=0 bash prototypes/jax-xla-runtime-specialization/run_jax_xla.sh
```

## Outputs

```text
prototypes/jax-xla-runtime-specialization/profiles/<run>/runtime-signatures.json
prototypes/jax-xla-runtime-specialization/artifacts/<run>/jax-cache/
prototypes/jax-xla-runtime-specialization/artifacts/<run>/hlo/
prototypes/jax-xla-runtime-specialization/results/<run>/*.csv
prototypes/jax-xla-runtime-specialization/results/<run>/summary.json
docs/figures/jax-xla-runtime-specialization-dacapo-compile-load.svg
docs/figures/jax-xla-runtime-specialization-dacapo-cache-speedup.svg
docs/figures/jax-xla-runtime-specialization-dacapo-latency-by-invocation.svg
```

## Serverless Interpretation

Each compile phase is a separate Python process. The first phase measures a cold
compile without a persistent cache. The populate phase writes compiled artifacts
into JAX's persistent compilation cache. The reuse phase starts a new process
against the same cache directory and measures compile-or-load time for the same
runtime signatures.

This mirrors the ProfileCache-FaaS idea: a short-lived worker observes optimizer
inputs, exports a compact artifact, and a later worker imports that artifact
instead of relearning everything from scratch.

## Reading Anchors

- JAX tracing: https://docs.jax.dev/en/latest/tracing.html
- JAX JIT and static arguments: https://docs.jax.dev/en/latest/jit-compilation.html
- JAX persistent compilation cache: https://docs.jax.dev/en/latest/persistent_compilation_cache.html
- XLA overview: https://openxla.org/xla
- XLA GPU architecture/autotuning context: https://openxla.org/xla/gpu_architecture
