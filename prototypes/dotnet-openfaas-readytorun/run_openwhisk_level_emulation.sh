#!/usr/bin/env bash
# Generate C#/.NET OpenWhisk-level raw warmup plots.
set -euo pipefail

PROTO_DIR="$(cd "$(dirname "$0")" && pwd)"
RUN_ID="${RUN_ID:-dotnet-openwhisk-level-$(date +%Y%m%d-%H%M%S)}"
SCENARIOS="${SCENARIOS:-serve-hot serve-mixed}"
MODES="${MODES:-il r2r nativeaot}"
INVOCATIONS="${INVOCATIONS:-2000}"
CHURN_AT="${CHURN_AT:-openwhisk}"

RESULT_ROOT="$PROTO_DIR/.runs/$RUN_ID/results"
mkdir -p "$RESULT_ROOT" "$PROTO_DIR/tmp/mplconfig" "$PROTO_DIR/tmp/fontconfig/fontconfig"

read -r -a SCENARIO_LIST <<< "$SCENARIOS"
read -r -a MODE_LIST <<< "$MODES"

seed_for() {
  local scenario="$1" mode="$2"
  python3 - "$scenario" "$mode" <<'PY'
import hashlib
import sys
print(int(hashlib.sha256(f"{sys.argv[1]}:{sys.argv[2]}".encode()).hexdigest()[:8], 16))
PY
}

for scenario in "${SCENARIO_LIST[@]}"; do
  csvs=()
  labels=()
  for mode in "${MODE_LIST[@]}"; do
    csv="$RESULT_ROOT/${scenario}-${mode}.csv"
    csvs+=("$csv")
    labels+=("$mode")

    python3 "$PROTO_DIR/generate_openwhisk_trace.py" \
      --scenario "$scenario" \
      --mode "$mode" \
      --invocations "$INVOCATIONS" \
      --churn-at "$CHURN_AT" \
      --out "$csv" \
      --seed "$(seed_for "$scenario" "$mode")"

    env MPLBACKEND=Agg MPLCONFIGDIR="$PROTO_DIR/tmp/mplconfig" XDG_CACHE_HOME="$PROTO_DIR/tmp/fontconfig" \
      python3 "$PROTO_DIR/plot_openwhisk_churn.py" \
        --csv "$csv" \
        --out "$RESULT_ROOT/${scenario}-${mode}-openwhisk-raw.png" \
        --summary "$RESULT_ROOT/${scenario}-${mode}-plot-summary.json" \
        --title "Emulated OpenWhisk-level C#/.NET $scenario - $mode raw latency"
  done

  env MPLBACKEND=Agg MPLCONFIGDIR="$PROTO_DIR/tmp/mplconfig" XDG_CACHE_HOME="$PROTO_DIR/tmp/fontconfig" \
    python3 "$PROTO_DIR/plot_openwhisk_churn.py" \
      --csv "${csvs[@]}" \
      --labels "${labels[@]}" \
      --out "$RESULT_ROOT/${scenario}-il-vs-aot-openwhisk-raw.png" \
      --title "Emulated OpenWhisk-level C#/.NET $scenario - IL vs ReadyToRun vs NativeAOT"
done

echo
echo "Done."
echo "  run:     $RUN_ID"
echo "  results: $RESULT_ROOT"
