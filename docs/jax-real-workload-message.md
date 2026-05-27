# Message Draft

I put together the full end-to-end JAX version without relying on DaCapo.

Instead of using DaCapo-shaped tensor signatures, I added a real-workload-shaped
JAX miniapp based on the TORAX use case: a scenario/config-driven transport
simulation where mesh size, timestep count, solver settings, and surrogate
model options define the JAX/XLA compilation profile.

The full loop is now implemented:

```text
scenario/config profile
  -> JAX lower/compile
  -> persistent XLA compilation cache
  -> compressed artifact export
  -> restore artifact for a fresh Python process
  -> first-request compile/load reuse
```

Small local run:

| Workload | Baseline compile/load | Restored-cache compile/load | Speedup |
| --- | ---: | ---: | ---: |
| `torax-pulse-64` | 227.7 ms | 22.7 ms | 10.0x |
| `torax-mlsurrogate-64` | 186.6 ms | 23.9 ms | 7.8x |

The restored artifact is small: 3 cache files, 131 KB on disk, 130 KB
compressed, and about 1.7 ms import overhead. I also added a mismatch control:
restoring the same artifact but changing the scenario shape/config misses the
cache and goes back to about 219 ms compile/load, so the artifact is actually
profile-specific.

One important implementation detail: for this JAX version, the cache needs to
be restored at the same cache mount path used during population. That maps well
to OpenFaaS because every pod can mount/import the artifact at a stable path
like `/profiles/jax-cache`.

So the claim is now: for a real JAX-style scientific workload, we can export a
compact XLA compilation artifact from one worker and let a future fresh worker
reuse it to cut first-request compilation latency after churn. This does not
eliminate Python import/tracing/lowering, but the phase breakdown makes that
separation explicit.
