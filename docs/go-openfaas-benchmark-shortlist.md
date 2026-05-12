# Go OpenFaaS Warm-Profile Benchmark Shortlist

Goal: prove the OpenFaaS version of the Go profile-cache loop:

```text
nopgo OpenFaaS function -> warm traffic -> CPU pprof export -> profile cache merge
-> go build -pgo=merged.pprof -> redeploy -> compare nopgo vs pgo
```

Use `of-watchdog` HTTP mode, not the classic fork-per-request watchdog. The warm-profile
experiment needs a long-lived Go process that can serve traffic while `net/http/pprof` or
`runtime/pprof` captures representative CPU samples.

## Recommended Benchmarks

### 1. Skewed HTTP Router + JSON Handler

Use this as the first benchmark.

Shape:

- One OpenFaaS Go HTTP function with several routes or operation types.
- A Zipf-like mix, for example 80% hot route, 15% medium route, 5% cold route.
- Each request decodes JSON, validates fields, dispatches through an interface, and encodes JSON.

Why it is strong:

- It is close to the existing local Go PGO prototype.
- It gives the compiler hot call edges that can drive inlining and devirtualization.
- It maps cleanly to ordinary API/serverless traffic.
- It is deterministic and has no external service dependency.

Measure:

- `nopgo` vs `pgo-5`, `pgo-10`, optionally `pgo-20` profile budgets.
- Warm latency through OpenFaaS gateway.
- Cold-ish latency after pod restart.
- Compiler evidence from `-gcflags=all=-m=2` or PGO debug output when useful.

The implemented Go runners also expose DaCapo-shaped aliases for broader CPU
coverage: `dacapo-lusearch`, `dacapo-eclipse`, and `dacapo-h2`. These are
Go-native analogues that keep the hot code inside the Go binary. They are useful
for Go PGO profile-cache testing, but they are not substitutes for the actual
JVM DaCapo programs when evaluating George/HiveJIT.

### 2. Markdown Render Function

Use this as the external baseline benchmark.

Shape:

- `POST /render` accepts Markdown and returns HTML.
- Use a real Go Markdown parser, such as the same CommonMark-style workload used in the Go PGO blog example.
- Drive requests with a fixed corpus: README-sized, medium article, and large document.

Why it is strong:

- The official Go PGO blog uses a Markdown HTTP service as its example.
- It is realistic serverless work: request body in, CPU-heavy transform, response body out.
- It is dependency-heavy enough for whole-program PGO to matter beyond one hand-written loop.
- It is still easy to package as a single OpenFaaS function.

Measure:

- Profile with the same document distribution used for evaluation.
- Add one intentionally mismatched profile run to show why representative warm profiles matter.
- Compare p50/p95 and total CPU samples for a fixed number of requests.

### 3. Pure-Go Image Thumbnail Function

Use this as the realism benchmark if you want a third workload.

Shape:

- `POST /thumbnail?size=...` accepts a JPEG or PNG and returns a resized JPEG/PNG.
- Prefer pure Go image libraries for the first version.
- Use a small fixed image corpus with repeated hot sizes and a few cold sizes.

Why it is useful:

- Image transforms are a common serverless workload.
- It broadens the study beyond API parsing and document rendering.
- Repeated resize sizes make warm profiles meaningful.

Caveat:

- Avoid libvips/C-backed implementations for the PGO experiment unless the goal is only
  application-level realism. If most CPU time is in C, Go compiler PGO will have little
  room to improve the hot code.

## OpenFaaS Experiment Loop

For each benchmark:

1. Build and deploy `nopgo` with `go build -pgo=off`.
2. Pin the function to one warm replica for profiling, or at least collect pod-specific profiles.
3. Send warm traffic for a fixed distribution and duration.
4. Capture several CPU profiles from the warm Go process.
5. Merge profiles with `go tool pprof -proto profiles/*.pprof > merged.pprof`.
6. Rebuild with `go build -pgo=merged.pprof`.
7. Redeploy the PGO image.
8. Compare gateway latency and pod-level CPU for the same fixed request mix.

Keep profile collection internal to the cluster or behind a temporary debug gate. Do not expose
`/debug/pprof` publicly in a normal OpenFaaS deployment.

## Avoid

- Tiny Fibonacci, sort-only, or single-function microbenchmarks. They do not represent the
  whole function binary well.
- I/O-dominated functions such as URL fetchers or database lookups for the first Go PGO result.
- C-backed image pipelines for the core PGO claim.
- Classic watchdog mode for warm-profile collection, because it forks a process per request.

## Sources

- Go PGO documentation: https://go.dev/doc/pgo
- Go PGO Markdown service example: https://go.dev/blog/pgo
- Go PGO-compatible profiling tools: https://go.dev/wiki/PGO-Tools
- OpenFaaS watchdog and `of-watchdog` HTTP mode: https://docs.openfaas.com/architecture/watchdog/
- OpenFaaS templates, including Go HTTP templates: https://docs.openfaas.com/cli/templates/
- SeBS serverless benchmark suite for workload inspiration: https://github.com/spcl/serverless-benchmarks
