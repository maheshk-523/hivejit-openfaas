#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

REQUESTS="${REQUESTS:-350000}"
PROFILE_REQUESTS="${PROFILE_REQUESTS:-900000}"
INVOKES="${INVOKES:-20}"
PROFILE_ITERS="${PROFILE_ITERS:-5 10}"
BENCHMARKS="${BENCHMARKS:-router}"
RUN_ID="${RUN_ID:-$(date +%Y%m%d-%H%M%S)}"

BUILD_DIR="build"
PROFILE_ROOT_BASE="profiles/$RUN_ID"
RESULT_DIR_BASE="results/$RUN_ID"
FIGURE_DIR="${FIGURE_DIR:-../../docs/figures}"
PLOT_RESULTS="${PLOT_RESULTS:-1}"

export GOCACHE="${GOCACHE:-$PWD/.cache/go-build}"

read -r -a BENCHMARK_LIST <<< "$BENCHMARKS"
if (( ${#BENCHMARK_LIST[@]} == 0 )); then
  echo "BENCHMARKS must contain at least one benchmark" >&2
  exit 1
fi

slugify() {
  printf "%s" "$1" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9._-' '-'
}

mkdir -p "$BUILD_DIR" "$PROFILE_ROOT_BASE" "$RESULT_DIR_BASE" "$GOCACHE"

echo "== Build baseline handler and tools =="
go build -buildvcs=false -trimpath -pgo=off -o "$BUILD_DIR/handler.nopgo" .
go build -buildvcs=false -trimpath -o "$BUILD_DIR/runner" ./cmd/runner
go build -buildvcs=false -trimpath -o "$BUILD_DIR/summarize" ./cmd/summarize
go version -m "$BUILD_DIR/handler.nopgo" > "$RESULT_DIR_BASE/buildinfo-nopgo.txt"

for benchmark in "${BENCHMARK_LIST[@]}"; do
  benchmark_slug="$(slugify "$benchmark")"
  PROFILE_ROOT="$PROFILE_ROOT_BASE"
  RESULT_DIR="$RESULT_DIR_BASE"
  figure_prefix="go-pgo-profile-cache"
  if (( ${#BENCHMARK_LIST[@]} > 1 )); then
    PROFILE_ROOT="$PROFILE_ROOT_BASE/$benchmark_slug"
    RESULT_DIR="$RESULT_DIR_BASE/$benchmark_slug"
    figure_prefix="go-pgo-profile-cache-$benchmark_slug"
  fi
  mkdir -p "$PROFILE_ROOT" "$RESULT_DIR"

  echo "== Measure baseline cold invocations ($benchmark) =="
  "$BUILD_DIR/runner" \
    -bin "$BUILD_DIR/handler.nopgo" \
    -label "go-nopgo" \
    -benchmark "$benchmark" \
    -iterations "$INVOKES" \
    -requests "$REQUESTS" \
    -csv "$RESULT_DIR/go-nopgo.csv"

  for iter_count in $PROFILE_ITERS; do
    profile_dir="$PROFILE_ROOT/${iter_count}-iters"
    mkdir -p "$profile_dir"

    echo "== Export profiles from $iter_count baseline invocations ($benchmark) =="
    for i in $(seq 1 "$iter_count"); do
      GOMAXPROCS=1 "$BUILD_DIR/handler.nopgo" \
        -requests "$PROFILE_REQUESTS" \
        -seed "$i" \
        -benchmark "$benchmark" \
        -profile-out "$profile_dir/invoke-$i.pprof" \
        -json > "$profile_dir/invoke-$i.json"
    done

    echo "== Merge profile cache for $iter_count invocations ($benchmark) =="
    merged_profile="$profile_dir/merged.pprof"
    merged_tmp="$profile_dir/merged.pprof.tmp"
    rm -f "$merged_profile" "$merged_tmp"
    profile_inputs=("$profile_dir"/invoke-*.pprof)
    if [[ ! -e "${profile_inputs[0]}" ]]; then
      echo "No raw profiles found in $profile_dir" >&2
      exit 1
    fi
    go tool pprof -proto "${profile_inputs[@]}" > "$merged_tmp"
    if [[ ! -s "$merged_tmp" ]]; then
      echo "Merged profile is empty: $merged_tmp" >&2
      exit 1
    fi
    mv "$merged_tmp" "$merged_profile"

    echo "== Build AOT Go binary with imported profile cache ($benchmark) =="
    pgo_bin="$BUILD_DIR/handler.pgo.${benchmark_slug}.${iter_count}"
    go build -buildvcs=false -trimpath -pgo="$merged_profile" -o "$pgo_bin" .
    go version -m "$pgo_bin" > "$RESULT_DIR/buildinfo-pgo-${iter_count}.txt"

    echo "== Measure PGO cold invocations for $iter_count-profile build ($benchmark) =="
    "$BUILD_DIR/runner" \
      -bin "$pgo_bin" \
      -label "go-pgo-${iter_count}" \
      -benchmark "$benchmark" \
      -iterations "$INVOKES" \
      -requests "$REQUESTS" \
      -csv "$RESULT_DIR/go-pgo-${iter_count}.csv"
  done

  echo "== Summary ($benchmark) =="
  "$BUILD_DIR/summarize" -out "$RESULT_DIR/summary.csv" "$RESULT_DIR"/*.csv

  if [[ "$PLOT_RESULTS" == "1" ]]; then
    echo "== Render figures ($benchmark) =="
    python3 plot_results.py \
      --results "$RESULT_DIR" \
      --out-dir "$FIGURE_DIR" \
      --prefix "$figure_prefix"
  fi
done

echo
echo "Artifacts:"
echo "  profiles: $PROFILE_ROOT_BASE"
echo "  results:  $RESULT_DIR_BASE"
if [[ "$PLOT_RESULTS" == "1" ]]; then
  echo "  figures:  $FIGURE_DIR/go-pgo-profile-cache*.svg"
fi
