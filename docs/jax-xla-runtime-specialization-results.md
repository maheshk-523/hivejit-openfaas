# JAX/XLA Runtime-Specialization Results

This experiment implements the JAX/XLA domain:

```text
runtime tensor signature -> JAX trace -> XLA executable -> persistent compilation cache -> fresh-process reuse
```

The runtime profile records tensor shapes, dtypes, and static arguments for
three DaCapo-shaped tensor signatures:

- `dacapo-lusearch`
- `dacapo-h2`
- `dacapo-eclipse`

The runner performs three phases in separate Python processes:

1. Compile without a persistent cache.
2. Compile with JAX's persistent cache enabled, populating the artifact cache.
3. Start a fresh process with the same cache directory and measure
   compile-or-load time.

Run:

```bash
bash prototypes/jax-xla-runtime-specialization/run_jax_xla.sh
```

Primary outputs:

```text
prototypes/jax-xla-runtime-specialization/profiles/<run>/runtime-signatures.json
prototypes/jax-xla-runtime-specialization/artifacts/<run>/jax-cache/
prototypes/jax-xla-runtime-specialization/artifacts/<run>/hlo/
prototypes/jax-xla-runtime-specialization/results/<run>/summary.json
docs/figures/jax-xla-runtime-specialization-dacapo-compile-load.svg
docs/figures/jax-xla-runtime-specialization-dacapo-cache-speedup.svg
docs/figures/jax-xla-runtime-specialization-dacapo-latency-by-invocation.svg
```

## Local Verification

Verified through the matrix runner on May 12, 2026 with JAX CPU in a
prototype-local virtualenv. Run ID: `20260512-134502`.

| Signature | No-cache compile/load ms | Persistent-cache reuse ms | Speedup |
| --- | ---: | ---: | ---: |
| `dacapo-lusearch` | 196.68 | 30.42 | 6.47x |
| `dacapo-h2` | 58.08 | 17.09 | 3.40x |
| `dacapo-eclipse` | 84.89 | 16.58 | 5.12x |

The run produced 10 persistent cache files totaling 62,798 bytes and emitted
post-compile HLO text for each measured signature.

## Invocation Curve

Run ID: `20260512-134700`

Command:

```bash
BOOTSTRAP=0 RUN_ID=20260512-134700 ITERATIONS=16 EXECUTIONS=3 \
bash prototypes/jax-xla-runtime-specialization/run_jax_xla.sh
```

This produced:

```text
docs/figures/jax-xla-runtime-specialization-dacapo-latency-by-invocation.svg
```

The curve plots compile/load latency against invocation number for each
DaCapo-shaped JAX signature. Because the 16 points are collected inside each
measurement process, later points also show JAX's in-process executable reuse;
the `20260512-134502` table above remains the better summary for fresh-process
persistent-cache reuse.

Interpretation:

- If `persistent-cache-reuse` compile-or-load time is lower than `no-cache`,
  the fresh process is benefiting from persisted XLA artifacts.
- If reuse is similar to no-cache on a platform, the experiment still proves the
  specialization path, but the local backend/cache combination did not expose a
  measurable cache-load benefit for these signatures.
- GPU runs can extend this with XLA's per-fusion autotuning cache, adding the
  configuration-optimization category from the taxonomy.
