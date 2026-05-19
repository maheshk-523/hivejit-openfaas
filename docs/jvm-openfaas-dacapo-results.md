# Original JVM DaCapo OpenFaaS Results

Run date: 2026-05-13 America/Los_Angeles.

This run invoked the existing OpenFaaS `hivejit-fn` function through the local
gateway port-forward and ran DaCapo `lusearch` on the original JVM in
`maheshk523/hivejit-openfaas-test:fix13`. The wrapper used the local matching
DaCapo evaluation harness, `evaluation-git-4e3de06d`, because the release jar
inside the deployed image did not match the image's source-built `lusearch`
payload.

The UCLA benchmark reference checked for this run was
<https://github.com/ucla-progsoftsys/dacapobench>, a fork of the upstream DaCapo
benchmark suite. Upstream release context is at
<https://github.com/dacapobench/dacapobench/releases/tag/v23.11-MR2-chopin> and
<https://www.dacapobench.org/>.

## Successful OpenFaaS Runs

| Benchmark | Requests | Warmup | Mean ms | p50 ms | p95 ms | Max ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| lusearch small | 10 | 2 | 2085.7 | 2089.9 | 2113.3 | 2125.0 |
| lusearch default | 5 | 1 | 10143.2 | 10112.6 | 10664.6 | 10772.1 |

Graphs:

- [Latency curves](figures/jvm-openfaas-dacapo-lusearch-latency-curves.svg)
- [p50/p95 bars](figures/jvm-openfaas-dacapo-lusearch-p50-p95.svg)
- [Summary JSON](figures/jvm-openfaas-dacapo-lusearch-summary.json)

Raw CSV and per-series JSON are under
`prototypes/jvm-openfaas-dacapo/results/20260514-openfaas-jvm-lusearch/`.

## Exclusions

`lusearch large` was attempted but exceeded the current OpenFaaS gateway timeout
and returned HTTP 502 after about 60 seconds. It is not included in the graphs.

Other DaCapo benchmarks such as `fop`, `pmd`, and `zxing` are not graphed here
because the local deployed image only contains the `lusearch` data and jar
payloads. Running those requires adding their matching DaCapo `dat` and `jar`
payloads from a full DaCapo distribution or building the needed payloads from
source.
