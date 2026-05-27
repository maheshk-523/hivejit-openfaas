# JAX Real-Workload Profile-Artifact Cache Proposal

## Recommendation

Use **TORAX** as the primary real JAX workload for the JAX/XLA path, with a
NumPyro NUTS inference service as the backup if TORAX packaging becomes too
heavy.

This replaces the current DaCapo-shaped tensor signatures with a real Python
program that already has the right failure mode:

```text
short JAX run -> expensive trace/compile phase -> reusable XLA compilation
artifact -> future short run avoids recompilation
```

TORAX is a differentiable tokamak core transport simulator implemented in
Python/JAX. Its own cache documentation says short runs can spend more time
tracing and compiling than executing, and recommends JAX's persistent
compilation cache to avoid recompilation across Python interpreter runs. That
is almost exactly the ProfileCache-FaaS story, except our system moves the
cache artifact through Redis or object storage so fresh serverless workers can
reuse it.

## Why TORAX Is The Best First Target

| Criterion | TORAX fit |
| --- | --- |
| Real JAX application | Open-source Python/JAX simulator, not a synthetic microkernel. |
| Published system | Described in `TORAX: A Fast and Differentiable Tokamak Transport Simulator in JAX`. |
| Cache need is explicit | TORAX docs say tracing/compilation can dominate short runs and describe persistent-cache use. |
| Workload profile is stable | Scenario/config, mesh/grid sizes, solver choice, timestep count, and model options define repeatable JAX graphs. |
| Serverless story is plausible | A pulse-design or scenario-evaluation service can receive many requests over the same family of configs while workers churn. |
| Existing prototype reuse | The current `prototypes/jax-openfaas-redis-xla` flow already imports a JAX cache before handler startup. |

The key caveat is important: JAX's persistent compilation cache saves compiled
program artifacts, not all Python-side tracing cost. The system should separate
`trace_ms`, `lower_ms`, `compile_or_load_ms`, and `execute_ms` so the claim is
precise.

## Candidate Shortlist

| Candidate | Why it could work | Risk |
| --- | --- | --- |
| TORAX | Real AI-for-science JAX simulator; documented persistent-cache need; strong narrative for short repeated runs. | Package/dependency size may be larger than the current prototype. |
| NumPyro NUTS | JAX compiles HMC/NUTS integration and tree-building into XLA kernels; fixed model/data shapes map cleanly to cache keys. | Full inference can be long enough that compile savings are less visible unless the benchmark is short. |
| Diffrax / JAX PDE or ODE solve | Scientific JAX solver with stable shapes and solver configs; easy to create repeated scenario requests. | Need to choose a representative published app, not just a toy ODE. |
| JAX-Fluids / AutoPDEx | Real PDE/CFD workloads, high compile pressure, good HPC story. | GPU and large-dependency setups can obscure the cold-start artifact result. |
| Flax/T5X/Levanter inference | Real ML stack; persistent compilation cache is configurable in training stacks. | Model download, parameter loading, and accelerator setup can dominate unless carefully controlled. |

## End-To-End System

The current implementation already has most of the system shape:

```text
populate pod
  -> configure JAX persistent cache
  -> run representative TORAX scenario
  -> export local JAX cache dir
  -> store compressed artifact in Redis/object store

serve pod
  -> pull artifact before importing app/JAX
  -> configure JAX cache dir
  -> execute first request
  -> report trace/lower/compile/load/execute timings
```

For TORAX, the request contract should be scenario-oriented rather than
tensor-oriented:

```json
{
  "scenario": "iter_baseline_small",
  "mesh": 64,
  "solver": "newton",
  "timesteps": 8,
  "physics_models": ["transport", "current_diffusion"]
}
```

The profile exported by the populate run should include:

```text
scenario name
TORAX package version / source hash
JAX and jaxlib versions
Python version
platform, backend, device topology
JAX/XLA flags
mesh size and static solver/config fields
HLO fingerprints for compiled entry points
cache file count and bytes
```

The cache key should extend the existing Redis key with the real workload
profile:

```text
source_hash + torax_version + jaxlib_version + backend + device_topology
  + xla_flags + scenario_profile_hash + artifact_schema
```

## Implementation Plan

1. Add a standalone TORAX runner under
   `prototypes/jax-torax-persistent-cache`.
   It should run one small checked-in or generated TORAX scenario in three
   processes: `baseline`, `populate`, and `reuse`.

2. Instrument the JAX stages explicitly:
   `trace_ms`, `lower_ms`, `compile_or_load_ms`, `execute_ms`,
   `cache_files`, `cache_bytes`, and `cache_hit_observed`.
   Enable JAX cache logging or cache-miss explanations in diagnostic mode.

3. Port the runner into the existing OpenFaaS/Redis shape:
   reuse `cachectl.py`, the entrypoint cache pull, `/cache/populate`,
   `/cache/metadata`, and pod-churn measurement scripts from
   `prototypes/jax-openfaas-redis-xla`.

4. Run four modes:
   `baseline`, `prepopulated-cache`, `progressive-cache`, and
   `shape/config-mismatch`.
   The mismatch control proves the artifact is profile-specific and not just
   noise.

5. Produce paper-style figures:
   first-request latency by mode, phase breakdown for invocation 1,
   pod-churn position medians, artifact size/import overhead, and a
   compile-cache hit/miss table.

## Expected Claim

The defensible claim is:

> For a real JAX application with repeated scenario profiles, a serverless
> worker can export a compact JAX/XLA compilation artifact and a future fresh
> worker can import it to reduce first-request compilation latency after pod
> churn.

Do not claim that this eliminates all JAX startup cost. Python import time,
application config construction, JAX tracing, and parameter/data loading may
remain. The figure should make that visible by showing a phase breakdown.

## Sources

- JAX persistent compilation cache:
  https://docs.jax.dev/en/latest/persistent_compilation_cache.html
- JAX ahead-of-time lowering and compilation stages:
  https://docs.jax.dev/en/latest/aot.html
- TORAX paper:
  https://arxiv.org/abs/2406.06718
- TORAX repository:
  https://github.com/google-deepmind/torax
- TORAX persistent-cache docs:
  https://torax.readthedocs.io/en/stable/cache.html
- NumPyro getting-started docs:
  https://num.pyro.ai/en/latest/getting_started.html
- NumPyro paper:
  https://arxiv.org/abs/1912.11554
- JAX-CFD repository:
  https://github.com/google/jax-cfd
- JAX MD paper:
  https://arxiv.org/abs/1912.04232
- Brax paper:
  https://arxiv.org/abs/2106.13281
