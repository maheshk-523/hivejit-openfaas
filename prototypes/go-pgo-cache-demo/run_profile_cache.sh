#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

REQUESTS="${REQUESTS:-350000}"
PROFILE_REQUESTS="${PROFILE_REQUESTS:-900000}"
INVOKES="${INVOKES:-20}"
PROFILE_ITERS="${PROFILE_ITERS:-5 10}"
RUN_ID="${RUN_ID:-$(date +%Y%m%d-%H%M%S)}"

BUILD_DIR="build"
PROFILE_ROOT="profiles/$RUN_ID"
RESULT_DIR="results/$RUN_ID"
FIGURE_DIR="${FIGURE_DIR:-../../docs/figures}"
PLOT_RESULTS="${PLOT_RESULTS:-1}"

export GOCACHE="${GOCACHE:-$PWD/.cache/go-build}"

mkdir -p "$BUILD_DIR" "$PROFILE_ROOT" "$RESULT_DIR" "$GOCACHE"

echo "== Build baseline handler and tools =="
go build -buildvcs=false -trimpath -pgo=off -o "$BUILD_DIR/handler.nopgo" .
go build -buildvcs=false -trimpath -o "$BUILD_DIR/runner" ./cmd/runner
go build -buildvcs=false -trimpath -o "$BUILD_DIR/summarize" ./cmd/summarize
go version -m "$BUILD_DIR/handler.nopgo" > "$RESULT_DIR/buildinfo-nopgo.txt"

echo "== Measure baseline cold invocations =="
"$BUILD_DIR/runner" \
  -bin "$BUILD_DIR/handler.nopgo" \
  -label "go-nopgo" \
  -iterations "$INVOKES" \
  -requests "$REQUESTS" \
  -csv "$RESULT_DIR/go-nopgo.csv"

for iter_count in $PROFILE_ITERS; do
  profile_dir="$PROFILE_ROOT/${iter_count}-iters"
  mkdir -p "$profile_dir"

  echo "== Export profiles from $iter_count baseline invocations =="
  for i in $(seq 1 "$iter_count"); do
    GOMAXPROCS=1 "$BUILD_DIR/handler.nopgo" \
      -requests "$PROFILE_REQUESTS" \
      -seed "$i" \
      -profile-out "$profile_dir/invoke-$i.pprof" \
      -json > "$profile_dir/invoke-$i.json"
  done

  echo "== Merge profile cache for $iter_count invocations =="
  go tool pprof -proto "$profile_dir"/*.pprof > "$profile_dir/merged.pprof"

  echo "== Build AOT Go binary with imported profile cache =="
  pgo_bin="$BUILD_DIR/handler.pgo.${iter_count}"
  go build -buildvcs=false -trimpath -pgo="$profile_dir/merged.pprof" -o "$pgo_bin" .
  go version -m "$pgo_bin" > "$RESULT_DIR/buildinfo-pgo-${iter_count}.txt"

  echo "== Measure PGO cold invocations for $iter_count-profile build =="
  "$BUILD_DIR/runner" \
    -bin "$pgo_bin" \
    -label "go-pgo-${iter_count}" \
    -iterations "$INVOKES" \
    -requests "$REQUESTS" \
    -csv "$RESULT_DIR/go-pgo-${iter_count}.csv"
done

echo "== Summary =="
"$BUILD_DIR/summarize" -out "$RESULT_DIR/summary.csv" "$RESULT_DIR"/*.csv

if [[ "$PLOT_RESULTS" == "1" ]]; then
  echo "== Render figures =="
  python3 plot_results.py \
    --results "$RESULT_DIR" \
    --out-dir "$FIGURE_DIR" \
    --prefix "go-pgo-profile-cache"
fi

echo
echo "Artifacts:"
echo "  profiles: $profile_dir"
echo "  results:  $RESULT_DIR"
if [[ "$PLOT_RESULTS" == "1" ]]; then
  echo "  figures:  $FIGURE_DIR/go-pgo-profile-cache-*.svg"
fi
