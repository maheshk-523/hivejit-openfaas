# Non-JVM Profile Reuse Domains

The current JVM/George work proves profile persistence inside an OpenFaaS-style lifecycle. The strongest non-JVM follow-ups are domains where a profile exported from one execution can be imported into the next compile or runtime artifact.

## 1. Go Serverless Handlers

Fit: exact for profile-guided AOT rebuilds.

Loop:

```text
baseline execution -> CPU pprof export -> merge profile cache -> go build -pgo -> next execution
```

Why it is useful:

- Go uses CPU pprof profiles directly as PGO input.
- The build is still a normal static Go build, so it maps cleanly to serverless image rebuilds.
- The Go documentation explicitly describes the iterative production loop: build, collect profiles, rebuild with profile, repeat.

Local prototype:

```bash
cd /Users/maheshk/Documents/New\ project\ 5/prototypes/go-pgo-cache-demo
./run_profile_cache.sh
```

## 2. Native C/C++ Services on LLVM

Fit: exact for profile-guided AOT rebuilds.

Loop:

```text
instrumented execution -> .profraw export -> llvm-profdata merge -> clang -fprofile-use -> next execution
```

Why it is useful:

- Clang has a standard instrumentation PGO loop: build with `-fprofile-generate`, run representative inputs, merge raw profiles, rebuild with `-fprofile-use`.
- This is a good second domain for CPU-heavy serverless functions such as image transforms, compression, parsing, routing engines, or scoring services.
- The local machine has Apple Clang and `xcrun llvm-profdata`, so this can be prototyped here without installing another runtime.

Prototype shape:

```text
function.c or function.rs
run_profile_cache.sh
profiles/<run>/invoke-*.profraw
profiles/<run>/merged.profdata
build/function.pgo
results/*.csv
```

## 3. Rust Services

Fit: exact for profile-guided AOT rebuilds.

Loop:

```text
instrumented execution -> .profraw export -> llvm-profdata merge -> rustc -Cprofile-use -> next execution
```

Why it is useful:

- Rust exposes an LLVM-backed PGO workflow through `rustc -Cprofile-generate` and `-Cprofile-use`.
- It is a distinct serverless language/domain from C/C++ even though the profile machinery is LLVM-based.
- It is a good target for compute-heavy serverless functions where cold starts matter less than per-request CPU once the function begins.

Local status: `rustc` is not installed on this machine, so this should be the third prototype once Rust is available.

## 4. .NET API Functions

Fit: good for cold-start/AOT study, partial for persistent profile import.

Loop candidates:

```text
execution -> dynamic PGO/tiered JIT within process
publish -> ReadyToRun or NativeAOT -> next cold execution
```

Why it is useful:

- ReadyToRun is a stock AOT deployment mode that reduces first-use JIT work and is directly relevant to serverless cold starts.
- Dynamic PGO in modern .NET optimizes hot types and paths during tiered compilation.
- Persistent static PGO exists historically in .NET Framework MPGO and internally around CoreCLR/crossgen2, but the stock modern SDK path is less clean than Go or LLVM. Treat .NET as a cold-start/AOT comparison first, and only pursue static profile import if the project can accept runtime-specific tooling.

## Reading Anchors

- Go PGO: https://go.dev/doc/pgo
- Clang PGO: https://clang.llvm.org/docs/UsersManual.html#profile-guided-optimization
- Rust PGO: https://doc.rust-lang.org/rustc/profile-guided-optimization.html
- .NET ReadyToRun: https://learn.microsoft.com/en-us/dotnet/core/deploying/ready-to-run
- .NET dynamic PGO config: https://learn.microsoft.com/en-us/dotnet/core/runtime-config/compilation
- DaCapo paper/citation: https://dacapobench.sourceforge.net/cite.html
- Tratt meta-tracing paper: https://kclpure.kcl.ac.uk/portal/en/publications/the-impact-of-meta-tracing-on-vm-design-and-implementation
- Tratt 2026 JIT blog: https://tratt.net/laurie/blog/2026/retrofitting_jit_compilers_into_c_interpreters.html
