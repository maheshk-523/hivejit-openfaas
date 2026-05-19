#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

RUN_ID="${RUN_ID:-$(date +%Y%m%d-%H%M%S)}"
BOOTSTRAP="${BOOTSTRAP:-1}"
ITERATIONS="${ITERATIONS:-1}"
EXECUTIONS="${EXECUTIONS:-3}"
FIGURE_DIR="${FIGURE_DIR:-../../docs/figures}"
FIGURE_PREFIX="${FIGURE_PREFIX:-jax-xla-runtime-specialization-dacapo}"
SIGNATURES="${SIGNATURES:-dacapo-lusearch dacapo-h2 dacapo-eclipse}"
PYTHON_BIN="${PYTHON_BIN:-}"

PROFILE_DIR="profiles/$RUN_ID"
RESULT_DIR="results/$RUN_ID"
ARTIFACT_DIR="artifacts/$RUN_ID"
CACHE_DIR="$ARTIFACT_DIR/jax-cache"
HLO_DIR="$ARTIFACT_DIR/hlo"
PROFILE_JSON="$PROFILE_DIR/runtime-signatures.json"

ensure_python() {
  if [[ -n "$PYTHON_BIN" ]]; then
    return
  fi

  if [[ -x ".venv/bin/python" ]]; then
    PYTHON_BIN=".venv/bin/python"
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
  if "$PYTHON_BIN" -c 'import jax, jaxlib, numpy' >/dev/null 2>&1; then
    return
  fi

  if [[ "$BOOTSTRAP" == "1" ]]; then
    uv pip install --python "$PYTHON_BIN" "jax[cpu]"
  fi

  if ! "$PYTHON_BIN" -c 'import jax, jaxlib, numpy' >/dev/null 2>&1; then
    echo "JAX is not installed for $PYTHON_BIN. Rerun with BOOTSTRAP=1 or set PYTHON_BIN." >&2
    exit 2
  fi
}

ensure_python
require_jax

mkdir -p "$PROFILE_DIR" "$RESULT_DIR" "$ARTIFACT_DIR" "$CACHE_DIR" "$HLO_DIR" "$FIGURE_DIR"

echo "== JAX/XLA runtime profile export =="
read -r -a SIGNATURE_LIST <<< "$SIGNATURES"
profile_cmd=("$PYTHON_BIN" workload.py profile --out "$PROFILE_JSON")
for signature in "${SIGNATURE_LIST[@]}"; do
  profile_cmd+=(--signature "$signature")
done
"${profile_cmd[@]}"

echo "== Compile with no persistent cache =="
"$PYTHON_BIN" workload.py compile \
  --profile "$PROFILE_JSON" \
  --label "no-cache" \
  --iterations "$ITERATIONS" \
  --executions "$EXECUTIONS" \
  --hlo-dir "$HLO_DIR/no-cache" \
  --csv "$RESULT_DIR/no-cache.csv"

echo "== Populate persistent compilation cache =="
"$PYTHON_BIN" workload.py compile \
  --profile "$PROFILE_JSON" \
  --label "persistent-cache-populate" \
  --cache-dir "$CACHE_DIR" \
  --iterations "$ITERATIONS" \
  --executions "$EXECUTIONS" \
  --hlo-dir "$HLO_DIR/populate" \
  --csv "$RESULT_DIR/persistent-cache-populate.csv"

echo "== Reuse persistent compilation cache from a fresh process =="
"$PYTHON_BIN" workload.py compile \
  --profile "$PROFILE_JSON" \
  --label "persistent-cache-reuse" \
  --cache-dir "$CACHE_DIR" \
  --iterations "$ITERATIONS" \
  --executions "$EXECUTIONS" \
  --hlo-dir "$HLO_DIR/reuse" \
  --csv "$RESULT_DIR/persistent-cache-reuse.csv"

echo "== Summarize =="
"$PYTHON_BIN" summarize_results.py \
  --input "no-cache=$RESULT_DIR/no-cache.csv" \
  --input "persistent-cache-populate=$RESULT_DIR/persistent-cache-populate.csv" \
  --input "persistent-cache-reuse=$RESULT_DIR/persistent-cache-reuse.csv" \
  --summary "$RESULT_DIR/summary.json" \
  --svg "$FIGURE_DIR/$FIGURE_PREFIX-compile-load.svg" \
  --speedup-svg "$FIGURE_DIR/$FIGURE_PREFIX-cache-speedup.svg" \
  --invocation-svg "$FIGURE_DIR/$FIGURE_PREFIX-latency-by-invocation.svg"

echo
echo "Artifacts:"
echo "  profile:  $PROFILE_JSON"
echo "  cache:    $CACHE_DIR"
echo "  HLO:      $HLO_DIR"
echo "  results:  $RESULT_DIR"
echo "  figures:  $FIGURE_DIR/$FIGURE_PREFIX-*.svg"
