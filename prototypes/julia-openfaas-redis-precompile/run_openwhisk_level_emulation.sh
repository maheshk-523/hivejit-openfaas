#!/usr/bin/env bash
# Generate OpenWhisk-level raw warmup traces for baseline vs AOT profile cache.
#
# This does not replace real OpenFaaS measurements. It is a calibrated
# emulation target for the figure shape the OpenWhisk example shows:
# 2000 requests, irregular container churn, raw sawtooth warmup decay.
set -euo pipefail

PROTO_DIR="$(cd "$(dirname "$0")" && pwd)"
RUN_ID="${RUN_ID:-openwhisk-level-emulation-$(date +%Y%m%d-%H%M%S)}"
WORKLOADS="${WORKLOADS:-lusearch h2 eclipse}"
MODES="${MODES:-baseline sysimage5 sysimage10}"
INVOCATIONS="${INVOCATIONS:-2000}"
CHURN_AT="${CHURN_AT:-openwhisk}"
SIZE="${SIZE:-1}"

RESULT_ROOT="$PROTO_DIR/.runs/$RUN_ID/results"
mkdir -p "$RESULT_ROOT" "$PROTO_DIR/tmp/mplconfig" "$PROTO_DIR/tmp/fontconfig/fontconfig"

read -r -a WORKLOAD_LIST <<< "$WORKLOADS"
read -r -a MODE_LIST <<< "$MODES"

seed_for() {
  local workload="$1" mode="$2"
  python3 - "$workload" "$mode" <<'PY'
import hashlib
import sys
raw = f"{sys.argv[1]}:{sys.argv[2]}".encode()
print(int(hashlib.sha256(raw).hexdigest()[:8], 16))
PY
}

for workload in "${WORKLOAD_LIST[@]}"; do
  csvs=()
  labels=()
  for mode in "${MODE_LIST[@]}"; do
    csv="$RESULT_ROOT/${workload}-${mode}.csv"
    csvs+=("$csv")
    labels+=("$mode")
    python3 "$PROTO_DIR/generate_demo_data.py" \
      --workload "$workload" \
      --mode "$mode" \
      --size "$SIZE" \
      --invocations "$INVOCATIONS" \
      --churn-at "$CHURN_AT" \
      --out "$csv" \
      --seed "$(seed_for "$workload" "$mode")"

    env MPLBACKEND=Agg MPLCONFIGDIR="$PROTO_DIR/tmp/mplconfig" XDG_CACHE_HOME="$PROTO_DIR/tmp/fontconfig" \
      python3 "$PROTO_DIR/plot_churn.py" \
        --csv "$csv" \
        --out "$RESULT_ROOT/${workload}-${mode}-openwhisk-raw.png" \
        --summary "$RESULT_ROOT/${workload}-${mode}-plot-summary.json" \
        --title "Emulated OpenWhisk-level Julia $workload - $mode raw latency"
  done

  env MPLBACKEND=Agg MPLCONFIGDIR="$PROTO_DIR/tmp/mplconfig" XDG_CACHE_HOME="$PROTO_DIR/tmp/fontconfig" \
    python3 "$PROTO_DIR/plot_churn.py" \
      --csv "${csvs[@]}" \
      --labels "${labels[@]}" \
      --out "$RESULT_ROOT/${workload}-baseline-vs-aot-openwhisk-raw.png" \
      --title "Emulated OpenWhisk-level Julia $workload - baseline vs AOT profile cache"

  python3 "$PROTO_DIR/evaluate_warmup_shape.py" \
    --csv "${csvs[@]}" \
    --labels "${labels[@]}" \
    --out "$RESULT_ROOT/${workload}-openwhisk-shape-eval.json"
done

echo
echo "Done."
echo "  run:     $RUN_ID"
echo "  results: $RESULT_ROOT"
