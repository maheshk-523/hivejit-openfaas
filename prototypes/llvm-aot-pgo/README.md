# LLVM AOT PGO Prototype

This prototype demonstrates the strict AOT version of the pattern:

```text
Execution -> profile export -> profile import at compile time -> execution
```

It uses Clang instrumentation PGO:

1. Build an instrumented binary with `-fprofile-instr-generate`.
2. Run representative training workloads to export one or more `.profraw`
   files.
3. Merge the raw profiles with `llvm-profdata`.
4. Rebuild the future artifact with `-fprofile-instr-use`.
5. Compare baseline and PGO binaries.

## Run

```bash
bash prototypes/llvm-aot-pgo/run_pgo.sh
```

By default the script trains and measures:

```text
dacapo-lusearch dacapo-h2 dacapo-eclipse dacapo-jython dacapo-fop
```

`dacapo-lusearch`, `dacapo-h2`, and `dacapo-eclipse` reuse branch-heavy routes
with different search, database, and compiler-shaped route distributions.
`dacapo-jython` is a non-JVM analogue of the DaCapo Jython benchmark: a
bytecode-interpreter dispatch loop with hot load, binary-op, call, and
exception paths. `dacapo-fop` is a non-JVM analogue of FOP: an XML/XSL-FO style
parse, layout, pagination, and render pipeline.

Useful knobs:

```bash
BENCHMARKS="dacapo-lusearch dacapo-h2 dacapo-eclipse dacapo-jython dacapo-fop" \
PROFILE_ITERS="5 10" \
TRAIN_ITERATIONS=1200000 \
MEASURE_ITERATIONS=2200000 \
bash prototypes/llvm-aot-pgo/run_pgo.sh
```

The script writes generated binaries, `.profraw` exports, `.profdata` imports,
and raw timing text under `prototypes/llvm-aot-pgo/build/<run-id>/`.

## Interpretation

The workload is intentionally branch-heavy and route-skewed like a serverless
handler with a dominant hot request path. PGO should help the compiler make
better inlining and layout decisions for the trained route distribution.

This prototype is not JVM/HiveJIT. It is the AOT analogue that proves the
profile lifecycle:

```text
.profraw -> .profdata -> optimized future binary
```

For the 5/10-profile setting, the lifecycle is:

```text
baseline execution(s) -> 5 or 10 .profraw exports -> llvm-profdata merge
-> clang -fprofile-instr-use=<merged.profdata> -> next execution
```
