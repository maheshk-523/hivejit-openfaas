# Python Profile-Specialization Results

This note captures the graphable Python/domain-specific specialization
prototype.

## System Loop

```text
generic cold execution -> route/query profile export -> generated specialization artifact -> future cold execution
```

Unlike Go PGO, this prototype does not rebuild a native binary. Unlike warm
starts, it does not keep a Python process alive. The reusable artifact is a
generated Python module specialized from runtime-observed route and query-shape
profiles.

## Run

```bash
cd prototypes/python-profile-specialization
BENCHMARKS="dacapo-lusearch dacapo-h2 dacapo-eclipse" \
REQUESTS=12000 PROFILE_REQUESTS=36000 INVOKES=16 PROFILE_ITERS="3" \
./run_profile_cache.sh
```

## Figures

After running, the generated figures are:

- `docs/figures/python-profile-specialization-dacapo-lusearch-invocation-curves.svg`
- `docs/figures/python-profile-specialization-dacapo-lusearch-p50-p95.svg`
- `docs/figures/python-profile-specialization-dacapo-lusearch-profile-specialization-improvement.svg`
- `docs/figures/python-profile-specialization-dacapo-h2-invocation-curves.svg`
- `docs/figures/python-profile-specialization-dacapo-h2-p50-p95.svg`
- `docs/figures/python-profile-specialization-dacapo-h2-profile-specialization-improvement.svg`
- `docs/figures/python-profile-specialization-dacapo-eclipse-invocation-curves.svg`
- `docs/figures/python-profile-specialization-dacapo-eclipse-p50-p95.svg`
- `docs/figures/python-profile-specialization-dacapo-eclipse-profile-specialization-improvement.svg`

Strict saved-state comparison figures from the runs below:

- `docs/figures/python-profile-specialization-strict-dacapo-lusearch-invocation-curves.png`
- `docs/figures/python-profile-specialization-strict-dacapo-h2-invocation-curves.png`
- `docs/figures/python-profile-specialization-strict-dacapo-eclipse-invocation-curves.png`
- `docs/figures/python-profile-specialization-paper-warmup-dacapo-lusearch.png`
- `docs/figures/python-profile-specialization-paper-warmup-dacapo-h2.png`
- `docs/figures/python-profile-specialization-paper-warmup-dacapo-eclipse.png`

## Strict Saved-State Evidence

These runs compare the generated specialization artifact against not saving warm
state. Each point is a fresh Python process.

| benchmark | run id | no saved state p50 | saved artifact p50 | point-by-point wins |
| --- | --- | ---: | ---: | ---: |
| dacapo-lusearch | `20260512-130927` | 207.0 ms | 183.6 ms | 16/16 |
| dacapo-h2 | `20260512-130927` | 314.4 ms | 269.0 ms | 16/16 |
| dacapo-eclipse | `20260513-eclipse-strict-100k` | 2077.9 ms | 1718.1 ms | 8/8 |

## Pod Lifecycle Evidence

These plots show the missing serverless lifecycle view: each group is a
long-lived function pod, the first request in each group pays cold-start cost,
the next requests are warmup, and the rest are hot in-pod requests. The cold
request includes measured Python process/import startup plus a fixed 350 ms
OpenFaaS-style platform startup component applied to both lines.

| benchmark | run path | cold median, no saved -> saved | hot median, no saved -> saved | wins |
| --- | --- | ---: | ---: | ---: |
| dacapo-lusearch | `20260513-lifecycle/dacapo-lusearch` | 558.4 ms -> 535.2 ms | 183.6 ms -> 165.0 ms | 30/30 |
| dacapo-h2 | `20260513-lifecycle/dacapo-h2` | 677.0 ms -> 621.2 ms | 301.3 ms -> 248.6 ms | 30/30 |
| dacapo-eclipse | `20260513-lifecycle/dacapo-eclipse-50k` | 1360.9 ms -> 1209.7 ms | 1077.4 ms -> 846.5 ms | 16/16 |

Lifecycle figures:

- `docs/figures/python-profile-specialization-lifecycle-dacapo-lusearch.png`
- `docs/figures/python-profile-specialization-lifecycle-dacapo-h2.png`
- `docs/figures/python-profile-specialization-lifecycle-dacapo-eclipse.png`

## Real OpenFaaS/Redis Lifecycle Evidence

These runs use the OpenFaaS gateway and real function pod replacement. The
runner deletes the current function pod, waits for a new ready pod, then sends a
fixed number of requests to that pod through the gateway. The first request in
each pod group is the cold request; the following requests show warmup and hot
in-pod behavior. The clearest figures plot the median latency at each
request-in-pod position across repeated real pod restarts, which removes
one-off gateway/scheduler outliers without changing the underlying CSV data.

The baseline is "not saving warm state": every fresh pod starts with the generic
handler. The optimized run first populates Redis with a generated specialization
artifact, then each fresh pod imports that artifact before serving requests.

| benchmark | run id | cold median, no saved -> saved | hot median, no saved -> saved | median-position wins |
| --- | --- | ---: | ---: | ---: |
| dacapo-lusearch | `20260513-openfaas-python-clear-median` | 1202.5 ms -> 1088.3 ms | 1115.2 ms -> 1046.7 ms | 8/8 |
| dacapo-h2 | `20260513-openfaas-python-h2` | 352.0 ms -> 276.5 ms | 307.8 ms -> 228.5 ms | 8/8 |
| dacapo-eclipse | `20260513-openfaas-python-eclipse-clear` | 2172.2 ms -> 1911.2 ms | 2114.3 ms -> 1887.1 ms | 8/8 |

OpenFaaS lifecycle figures:

- `docs/figures/python-openfaas-profile-specialization-lifecycle-dacapo-lusearch.png`
- `docs/figures/python-openfaas-profile-specialization-lifecycle-dacapo-h2.png`
- `docs/figures/python-openfaas-profile-specialization-lifecycle-dacapo-eclipse.png`
- `docs/figures/python-openfaas-profile-specialization-lifecycle-median-dacapo-lusearch.png`
- `docs/figures/python-openfaas-profile-specialization-lifecycle-median-dacapo-h2.png`
- `docs/figures/python-openfaas-profile-specialization-lifecycle-median-dacapo-eclipse.png`

The saved-artifact median line is below the no-saved-state median line at every
request position for all three workloads. Raw per-request CSVs are still kept,
but those traces can show OpenFaaS/kind scheduling outliers that obscure the
repeatable profile-specialization effect.

## Latest Run

Run id: `20260512-133511`

Command:

```bash
RUN_ID=20260512-133511 \
BENCHMARKS="dacapo-lusearch dacapo-h2 dacapo-eclipse" \
REQUESTS=12000 PROFILE_REQUESTS=36000 INVOKES=16 PROFILE_ITERS="3" \
bash prototypes/python-profile-specialization/run_profile_cache.sh
```

| benchmark | build | n | mean wall ms | p50 wall ms | p95 wall ms |
| --- | --- | ---: | ---: | ---: | ---: |
| dacapo-lusearch | Generic | 16 | 269.357 | 266.795 | 305.315 |
| dacapo-lusearch | Specialized, 3 profiles | 16 | 256.295 | 255.138 | 275.672 |
| dacapo-h2 | Generic | 16 | 425.461 | 422.862 | 442.147 |
| dacapo-h2 | Specialized, 3 profiles | 16 | 348.926 | 346.723 | 366.972 |
| dacapo-eclipse | Generic | 16 | 331.947 | 332.296 | 351.541 |
| dacapo-eclipse | Specialized, 3 profiles | 16 | 315.933 | 312.260 | 354.107 |

The profile-specialized artifact improved cold-process p50 by about 4.4% on
`dacapo-lusearch`, 18.0% on `dacapo-h2`, and 6.0% on `dacapo-eclipse`. p95
improved by about 9.7% for `dacapo-lusearch` and 17.0% for `dacapo-h2`; the
`dacapo-eclipse` p95 was essentially flat/slightly worse in this run (-0.7%).

## Interpretation

This is a domain-specific specialization result, not a stock Python JIT result.
The generic handler uses interpreted route/query operators. The profile artifact
records observed route frequencies, and `profile_codegen.py` emits direct code
ordered by the hot profile. Future cold invocations import that artifact and
avoid the generic dispatch path.

Use this as the third comparison when the requirement is: a non-JVM serverless
system where runtime information is exported from one execution and imported by
future cold executions as an optimizer artifact.
