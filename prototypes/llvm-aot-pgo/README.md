# LLVM AOT PGO Prototype

This prototype demonstrates the strict AOT version of the pattern:

```text
Execution -> profile export -> profile import at compile time -> execution
```

It uses Clang instrumentation PGO:

1. Build an instrumented binary with `-fprofile-instr-generate`.
2. Run a representative training workload to export `.profraw`.
3. Merge the raw profile with `llvm-profdata`.
4. Rebuild with `-fprofile-instr-use`.
5. Compare baseline and PGO binaries.

## Run

```bash
bash prototypes/llvm-aot-pgo/run_pgo.sh
```

The script writes all generated binaries and profiles under
`prototypes/llvm-aot-pgo/build/`.

## Interpretation

The workload is intentionally branch-heavy and route-skewed like a serverless
handler with a dominant hot request path. PGO should help the compiler make
better inlining and layout decisions for the trained route distribution.

This prototype is not JVM/HiveJIT. It is the AOT analogue that proves the
profile lifecycle:

```text
.profraw -> .profdata -> optimized future binary
```
