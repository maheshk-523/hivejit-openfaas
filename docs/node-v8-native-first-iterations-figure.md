# Node/V8 Native First-Iteration Figure

This replaces the Go PGO graph for the C#/JVM-style warmup comparison.

Go was not the right runtime model for that figure. Go PGO is a static rebuild
optimization: the runtime does not tier from interpreter to JIT to optimized
code while the first requests arrive. That means a Go first-10-iteration plot
can measure binary-level PGO effects, but it should not be used as an analogue
for JVM or .NET tiered JIT warmup.

Node/V8 is a closer fit for this graph because it has runtime source
parse/compile cost, bytecode cachedData, and JIT warmup across repeated calls in
a fresh process. The comparison is still named carefully: V8 cachedData is a
public bytecode/code-cache artifact, not a full optimized-code or feedback-vector
checkpoint.

## Artifacts

- Figure: `docs/figures/node-v8-native-first-10-iterations.png`
- Summary: `docs/figures/node-v8-native-first-10-iterations-summary.json`
- Raw measurements: `prototypes/node-v8-native-first-iterations/results/node-v8-native-first-iterations/measurements.csv`
- Harness: `prototypes/node-v8-native-first-iterations/run_once.js`
- Plot driver: `scripts/plot_node_v8_native_first_iterations.py`

## Workloads

The workloads are native JavaScript/Node workloads, not DaCapo labels:

- `router-dispatch`: generated route/rule dispatch over thousands of functions.
- `json-codec`: JSON parse, transform, filter, and stringify.
- `regex-tokenizer`: JavaScript-source tokenization with regular expressions.
- `template-render`: HTML string rendering and escaping.
- `query-aggregate`: filter, bucket, sort, and aggregate over records.

Each workload runs from a generated JavaScript source file with 3,200 generated
helper functions so first-request source loading is large enough to be visible.

## Variants

- `source JIT/load`: a fresh process compiles the source with `vm.Script` on the
  first iteration, then reuses the in-process handler for later iterations.
- `V8 cachedData`: the same source is loaded with a cachedData blob generated
  before running the workload.
- `V8 cachedData + trained`: the cachedData blob is generated after warmup calls
  have forced more hot-path functions to be compiled.

## Method

The plot follows the earlier C# layout:

- 5 workload panels in a 2x3 grid.
- X-axis is the first 10 iterations in a fresh Node process.
- Each point is the median across 15 fresh Node processes.
- Iteration 1 includes source compile or cachedData load plus execution.
- Later iterations measure execution after the handler is already loaded in that
  process.
- Dashed horizontal lines show steady medians for each variant.
- Steady is computed as the median of each repeat's last 20% of 50 iterations,
  then the median across repeats.

## Current Result

The result matches the expected V8 shape:

- cachedData mostly helps iteration 1, because it removes much of the source
  parse/compile/load cost.
- steady-state improvement is workload-dependent and usually small.
- `router-dispatch` is the exception in this run because the trained cachedData
  captures many hot generated functions, lowering both iteration 1 and steady
  latency.

This is the reason the graph should be described as a Node/V8 code-cache
experiment, not as Go PGO and not as a full JVM/.NET profile checkpoint.
