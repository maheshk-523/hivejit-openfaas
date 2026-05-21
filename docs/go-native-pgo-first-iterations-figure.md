# Go-Native PGO First-Iteration Figure

This figure mirrors the C# warmup-chart layout with Go-native benchmarks. It
compares a default Go build against a Go build compiled with `-pgo`.

## Workloads

The benchmark set uses only Go-native standard-library or runtime-shaped work:

| Workload | What it exercises |
| --- | --- |
| Router dispatch | Interface calls, branch skew, hashing, and route-style dispatch. |
| JSON encode/decode | `encoding/json` marshal/unmarshal with structured records. |
| Template render | `text/template` execution over repeated report rows. |
| Regexp scan | `regexp` matching and submatch extraction over log-shaped text. |
| Gzip compress+hash | `compress/gzip`, SHA-256 hashing, and decompression readback. |

## Measurement

The script creates one CPU profile from a training run, builds two binaries, and
then runs each binary as a fresh process 15 times:

| Mode | Meaning |
| --- | --- |
| Go default build | `go build` with no PGO profile. |
| Go build `-pgo` | `go build -pgo=<cpu.pprof>` using the training profile. |

Each fresh process runs the first 10 measured iterations of every workload. The
figure plots the per-iteration median across the 15 fresh runs. The dotted line
is the PGO median over the last 20% of iterations.

Go is already ahead-of-time compiled, so this should not be read as a JIT warmup
curve. The relevant question is whether PGO changes native execution latency.

## Generated Artifacts

Source run:

```text
prototypes/go-native-pgo-first-iterations/results/20260518-go-native-pgo-first-10-r15
```

Figure and summary:

```text
docs/figures/go-native-pgo-first-10-iterations.png
docs/figures/go-native-pgo-first-10-iterations-summary.json
```

## Reproduction

```bash
python3 -B scripts/plot_go_native_pgo_first_iterations.py \
  --repeats 15 \
  --run-dir prototypes/go-native-pgo-first-iterations/results/20260518-go-native-pgo-first-10-r15
```

If the run directory already exists, use a new directory or pass `--skip-collect`
to rerender from the existing CSVs.
