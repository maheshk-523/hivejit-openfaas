# Node/V8 Artifact Cache Prototype

This prototype demonstrates a local version of:

```text
Execution -> artifact export -> artifact import -> execution
```

It uses `vm.Script.createCachedData()` to export V8 code-cache bytes after a
representative invocation. A later fresh Node process imports those bytes through
the `cachedData` option.

This is a compile-cache baseline, not full JIT profile reuse. It does not export
V8 FeedbackVectors, inline-cache feedback, optimized code, heap state, or
JavaScript-observable state. That limitation is useful: it marks the gap between
what can be prototyped with public Node APIs and what a deeper V8/HiveJIT-style
runtime change would need.

## Run

```bash
node prototypes/node-v8-artifact-cache/bench.js --runs 8
```

The benchmark runs fresh Node processes for each cold start:

1. `none`: compile and run without an artifact.
2. `export`: compile, run, and save V8 cached data.
3. `import`: compile with saved cached data and run.

Results are written to `prototypes/node-v8-artifact-cache/results/last.json`.

## Useful Single Runs

```bash
node prototypes/node-v8-artifact-cache/runtime.js --mode export --json
node prototypes/node-v8-artifact-cache/runtime.js --mode import --json
node prototypes/node-v8-artifact-cache/runtime.js --mode none --json
```

## Expected Interpretation

Look at:

```text
compileMs
executeMs
totalMs
cachedDataRejected
artifactBytes
```

If `cachedDataRejected` is `true`, the artifact was invalid for this source,
V8 version, architecture, or runtime configuration.
