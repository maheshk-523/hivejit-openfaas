#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

RUN_ID="${RUN_ID:-$(date +%Y%m%d-%H%M%S)}"
BOOTSTRAP="${BOOTSTRAP:-1}"
TRIALS="${TRIALS:-3}"
EXECUTIONS="${EXECUTIONS:-2}"
SCENARIOS="${SCENARIOS:-torax-pulse-64 torax-mlsurrogate-64}"
MISMATCH_SCENARIOS="${MISMATCH_SCENARIOS:-torax-pulse-64-mismatch}"
FIGURE_DIR="${FIGURE_DIR:-../../docs/figures}"
FIGURE_PREFIX="${FIGURE_PREFIX:-jax-real-workload-cache}"
PYTHON_BIN="${PYTHON_BIN:-}"

PROFILE_DIR="profiles/$RUN_ID"
RESULT_DIR="results/$RUN_ID"
ARTIFACT_DIR="artifacts/$RUN_ID"
STORE_DIR="$ARTIFACT_DIR/object-store"
STABLE_CACHE="$ARTIFACT_DIR/stable-jax-cache"
PROFILE_JSON="$PROFILE_DIR/scenario-profile.json"
MISMATCH_PROFILE_JSON="$PROFILE_DIR/mismatch-profile.json"
EXPORT_META="$RESULT_DIR/export-meta.json"

ensure_python() {
  if [[ -n "$PYTHON_BIN" ]]; then
    return
  fi

  if [[ -x ".venv/bin/python" ]]; then
    PYTHON_BIN=".venv/bin/python"
    return
  fi

  if [[ -x "../jax-xla-runtime-specialization/.venv/bin/python" ]]; then
    PYTHON_BIN="../jax-xla-runtime-specialization/.venv/bin/python"
    return
  fi

  if [[ "$BOOTSTRAP" != "1" ]]; then
    PYTHON_BIN="python3"
    return
  fi

  if ! command -v uv >/dev/null 2>&1; then
    echo "uv is required to bootstrap JAX. Set PYTHON_BIN to an environment with jax installed." >&2
    exit 2
  fi

  base_python="${BASE_PYTHON:-/Users/maheshk/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3}"
  if [[ ! -x "$base_python" ]]; then
    base_python="python3"
  fi

  uv venv --python "$base_python" .venv
  PYTHON_BIN=".venv/bin/python"
  uv pip install --python "$PYTHON_BIN" "jax[cpu]"
}

require_jax() {
  local import_check='import jax, jaxlib, numpy'
  local packages=("jax[cpu]")
  if [[ " $SCENARIOS $MISMATCH_SCENARIOS " == *"flax-"* ]]; then
    import_check='import jax, jaxlib, numpy, flax'
    packages=("jax[cpu]" "flax")
  fi

  if "$PYTHON_BIN" -c "$import_check" >/dev/null 2>&1; then
    return
  fi

  if [[ "$BOOTSTRAP" == "1" ]]; then
    uv pip install --python "$PYTHON_BIN" "${packages[@]}"
  fi

  if ! "$PYTHON_BIN" -c "$import_check" >/dev/null 2>&1; then
    echo "Required Python modules are not installed for $PYTHON_BIN. Rerun with BOOTSTRAP=1 or set PYTHON_BIN." >&2
    exit 2
  fi
}

ensure_python
require_jax

mkdir -p "$PROFILE_DIR" "$RESULT_DIR" "$ARTIFACT_DIR" "$STORE_DIR" "$FIGURE_DIR"
rm -f "$RESULT_DIR/baseline.csv" \
  "$RESULT_DIR/persistent-cache-populate.csv" \
  "$RESULT_DIR/persistent-cache-reuse.csv" \
  "$RESULT_DIR/mismatch-control.csv"

echo "== Build real JAX scenario profile =="
read -r -a SCENARIO_LIST <<< "$SCENARIOS"
profile_cmd=("$PYTHON_BIN" workload.py profile --out "$PROFILE_JSON")
for scenario in "${SCENARIO_LIST[@]}"; do
  profile_cmd+=(--scenario "$scenario")
done
"${profile_cmd[@]}"

read -r -a MISMATCH_LIST <<< "$MISMATCH_SCENARIOS"
mismatch_cmd=("$PYTHON_BIN" workload.py profile --out "$MISMATCH_PROFILE_JSON")
for scenario in "${MISMATCH_LIST[@]}"; do
  mismatch_cmd+=(--scenario "$scenario")
done
"${mismatch_cmd[@]}"

CACHE_KEY="$("$PYTHON_BIN" workload.py cache-key --profile "$PROFILE_JSON")"
echo "cache key: $CACHE_KEY"

echo "== Fresh-process baseline trials =="
for ((trial = 1; trial <= TRIALS; trial++)); do
  "$PYTHON_BIN" workload.py measure \
    --profile "$PROFILE_JSON" \
    --label baseline \
    --iteration "$trial" \
    --executions "$EXECUTIONS" \
    --hlo-dir "$ARTIFACT_DIR/hlo/baseline-$trial" \
    --csv "$RESULT_DIR/baseline.csv" \
    --append
done

echo "== Populate persistent compilation cache =="
rm -rf "$STABLE_CACHE"
"$PYTHON_BIN" workload.py measure \
  --profile "$PROFILE_JSON" \
  --label persistent-cache-populate \
  --cache-dir "$STABLE_CACHE" \
  --iteration 1 \
  --executions "$EXECUTIONS" \
  --hlo-dir "$ARTIFACT_DIR/hlo/populate" \
  --csv "$RESULT_DIR/persistent-cache-populate.csv"

"$PYTHON_BIN" workload.py export-cache \
  --cache-dir "$STABLE_CACHE" \
  --store-dir "$STORE_DIR" \
  --key "$CACHE_KEY" \
  --metadata "$EXPORT_META"

echo "== Fresh-process restored-cache trials =="
for ((trial = 1; trial <= TRIALS; trial++)); do
  import_meta="$RESULT_DIR/reuse-import-$trial.json"
  rm -rf "$STABLE_CACHE"
  "$PYTHON_BIN" workload.py import-cache \
    --cache-dir "$STABLE_CACHE" \
    --store-dir "$STORE_DIR" \
    --key "$CACHE_KEY" \
    --metadata "$import_meta" \
    --require
  "$PYTHON_BIN" workload.py measure \
    --profile "$PROFILE_JSON" \
    --label persistent-cache-reuse \
    --cache-dir "$STABLE_CACHE" \
    --iteration "$trial" \
    --executions "$EXECUTIONS" \
    --import-meta "$import_meta" \
    --hlo-dir "$ARTIFACT_DIR/hlo/reuse-$trial" \
    --csv "$RESULT_DIR/persistent-cache-reuse.csv" \
    --append
done

echo "== Mismatch control with same restored artifact =="
for ((trial = 1; trial <= TRIALS; trial++)); do
  mismatch_import_meta="$RESULT_DIR/mismatch-import-$trial.json"
  rm -rf "$STABLE_CACHE"
  "$PYTHON_BIN" workload.py import-cache \
    --cache-dir "$STABLE_CACHE" \
    --store-dir "$STORE_DIR" \
    --key "$CACHE_KEY" \
    --metadata "$mismatch_import_meta" \
    --require
  "$PYTHON_BIN" workload.py measure \
    --profile "$MISMATCH_PROFILE_JSON" \
    --label mismatch-control \
    --cache-dir "$STABLE_CACHE" \
    --iteration "$trial" \
    --executions "$EXECUTIONS" \
    --import-meta "$mismatch_import_meta" \
    --hlo-dir "$ARTIFACT_DIR/hlo/mismatch-$trial" \
    --csv "$RESULT_DIR/mismatch-control.csv" \
    --append
done

echo "== Summarize =="
"$PYTHON_BIN" summarize_results.py \
  --input "baseline=$RESULT_DIR/baseline.csv" \
  --input "persistent-cache-populate=$RESULT_DIR/persistent-cache-populate.csv" \
  --input "persistent-cache-reuse=$RESULT_DIR/persistent-cache-reuse.csv" \
  --input "mismatch-control=$RESULT_DIR/mismatch-control.csv" \
  --summary "$RESULT_DIR/summary.json" \
  --phase-svg "$FIGURE_DIR/$FIGURE_PREFIX-phase-breakdown.svg" \
  --compile-svg "$FIGURE_DIR/$FIGURE_PREFIX-compile-load.svg" \
  --speedup-svg "$FIGURE_DIR/$FIGURE_PREFIX-speedup.svg"

echo
echo "Artifacts:"
echo "  profile:  $PROFILE_JSON"
echo "  store:    $STORE_DIR"
echo "  cache:    $STABLE_CACHE"
echo "  results:  $RESULT_DIR"
echo "  figures:  $FIGURE_DIR/$FIGURE_PREFIX-*.svg"
