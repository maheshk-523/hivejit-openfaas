#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_ID="${RUN_ID:-$(date +%Y%m%d-%H%M%S)}"
BUILD="${BUILD:-$ROOT/build/$RUN_ID}"
SRC="$ROOT/handler.c"
CLANG=(xcrun clang)
PROFDATA=(xcrun llvm-profdata)

BENCHMARKS="${BENCHMARKS:-dacapo-lusearch dacapo-h2 dacapo-eclipse dacapo-jython dacapo-fop}"
PROFILE_ITERS="${PROFILE_ITERS:-5 10}"
TRAIN_ITERATIONS="${TRAIN_ITERATIONS:-1200000}"
MEASURE_ITERATIONS="${MEASURE_ITERATIONS:-2200000}"
FIGURE_DIR="${FIGURE_DIR:-$ROOT/../../docs/figures}"
PLOT_RESULTS="${PLOT_RESULTS:-1}"

mkdir -p "$BUILD"

BASE="$BUILD/handler-base"
INSTR="$BUILD/handler-instrumented"
PROFILE_ROOT="$BUILD/profiles"
RESULT_ROOT="$BUILD/results"

mkdir -p "$PROFILE_ROOT" "$RESULT_ROOT"

slugify() {
  printf "%s" "$1" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9._-' '-'
}

max_profile_iter() {
  local max=0
  local count
  for count in $PROFILE_ITERS; do
    if (( count > max )); then
      max="$count"
    fi
  done
  echo "$max"
}

echo "== Build baseline"
"${CLANG[@]}" -O3 "$SRC" -o "$BASE"

echo "== Build instrumented"
"${CLANG[@]}" -O3 -fprofile-instr-generate "$SRC" -o "$INSTR"

MAX_PROFILE_ITERS="$(max_profile_iter)"

for benchmark in $BENCHMARKS; do
  slug="$(slugify "$benchmark")"
  profile_dir="$PROFILE_ROOT/$slug"
  result_dir="$RESULT_ROOT/$slug"
  mkdir -p "$profile_dir" "$result_dir"

  echo "== Baseline measurement: $benchmark"
  "$BASE" "$benchmark" "$MEASURE_ITERATIONS" > "$result_dir/baseline.txt" || true
  cat "$result_dir/baseline.txt"

  echo "== Train and export $MAX_PROFILE_ITERS raw profiles: $benchmark"
  for iter in $(seq 1 "$MAX_PROFILE_ITERS"); do
    raw="$profile_dir/invoke-${iter}.profraw"
    LLVM_PROFILE_FILE="$raw" "$INSTR" "$benchmark" "$TRAIN_ITERATIONS" > "$profile_dir/train-${iter}.txt" || true
  done

  for iter_count in $PROFILE_ITERS; do
    data="$profile_dir/${iter_count}-profiles.profdata"
    pgo="$BUILD/handler-pgo-${slug}-${iter_count}"
    inputs=()
    for iter in $(seq 1 "$iter_count"); do
      inputs+=("$profile_dir/invoke-${iter}.profraw")
    done

    echo "== Merge $iter_count profiles: $benchmark"
    "${PROFDATA[@]}" merge -output="$data" "${inputs[@]}"

    echo "== Build PGO binary from $iter_count profiles: $benchmark"
    "${CLANG[@]}" -O3 -fprofile-instr-use="$data" "$SRC" -o "$pgo"

    echo "== PGO measurement: $benchmark profiles=$iter_count"
    "$pgo" "$benchmark" "$MEASURE_ITERATIONS" > "$result_dir/pgo-${iter_count}.txt" || true
    cat "$result_dir/pgo-${iter_count}.txt"
  done
done

echo "== Artifacts"
find "$BUILD" -maxdepth 3 -type f | sort

if [[ "$PLOT_RESULTS" == "1" ]]; then
  echo "== Render figures"
  python3 "$ROOT/plot_results.py" \
    --results-root "$RESULT_ROOT" \
    --out "$FIGURE_DIR/llvm-aot-pgo-all-results.png" \
    --summary "$FIGURE_DIR/llvm-aot-pgo-all-results-summary.json"
fi
