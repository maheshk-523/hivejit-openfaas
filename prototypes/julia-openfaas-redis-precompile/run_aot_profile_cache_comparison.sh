#!/usr/bin/env bash
# Compare no saved profile state against AOT profile-cache sysimages.
#
# This is the Julia analogue of the HiveJIT-style comparison:
#   baseline   - normal Julia JIT warmup inside each fresh pod
#   sysimage5  - PackageCompiler sysimage built after 5 profile runs
#   sysimage10 - PackageCompiler sysimage built after 10 profile runs
#
# Outputs land under .runs/<RUN_ID>/results/:
#   <workload>-baseline.csv/json/warmup.png
#   <workload>-sysimage5.csv/json/warmup.png
#   <workload>-sysimage10.csv/json/warmup.png
#   <workload>-aot-comparison.png
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

PROTO_DIR="$(cd "$(dirname "$0")" && pwd)"
PLOT_SCRIPT="$PROTO_DIR/plot_churn.py"

FUNCTION_NAME="${FUNCTION_NAME:-julia-precompile}"
FUNCTION_NAMESPACE="${FUNCTION_NAMESPACE:-openfaas-fn}"
OPENFAAS_GATEWAY="${OPENFAAS_GATEWAY:-http://127.0.0.1:8080}"

RUN_ID="${RUN_ID:-aot-profile-cache-$(date +%Y%m%d-%H%M%S)}"
WORKLOADS="${WORKLOADS:-lusearch h2 eclipse jython fop}"
SIZE="${SIZE:-1}"
INVOCATIONS="${INVOCATIONS:-120}"
SEGMENT_LENGTH="${SEGMENT_LENGTH:-30}"
CHURN_AT="${CHURN_AT:-}"
WATCHDOG_TIMEOUT="${WATCHDOG_TIMEOUT:-180s}"
POST_READY_DELAY="${POST_READY_DELAY:-0}"
SYSIMAGE_POST_READY_DELAY="${SYSIMAGE_POST_READY_DELAY:-25}"
AOT_PROFILE_COUNTS="${AOT_PROFILE_COUNTS:-5 10}"
PROBE_INITIAL_DELAY="${PROBE_INITIAL_DELAY:-5}"

IMAGE_PREFIX="${IMAGE_PREFIX:-ttl.sh/${FUNCTION_NAME}-${USER:-user}}"
PUSH_IMAGE="${PUSH_IMAGE:-0}"
KIND_CLUSTER="${KIND_CLUSTER:-openfaas}"
DEPLOY_BACKEND="${DEPLOY_BACKEND:-direct}"
REDIS_HOST="${REDIS_HOST:-redis.openfaas-fn.svc.cluster.local}"
REDIS_PORT="${REDIS_PORT:-6379}"
SKIP_BUILD="${SKIP_BUILD:-0}"

ARTIFACT_ROOT="$PROTO_DIR/.runs/$RUN_ID"
RESULT_ROOT="$ARTIFACT_ROOT/results"
MANIFEST_ROOT="$ARTIFACT_ROOT/k8s"

read -r -a WORKLOAD_LIST <<< "$WORKLOADS"
read -r -a PROFILE_COUNT_LIST <<< "$AOT_PROFILE_COUNTS"

slugify() { printf "%s" "$1" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9._-' '-'; }
require_cmd() { command -v "$1" >/dev/null 2>&1 || { echo "missing required command: $1" >&2; exit 1; }; }

build_runtime_image() {
  local image="$1"
  [[ "$SKIP_BUILD" == "1" ]] && return
  echo "== Build baseline runtime image $image =="
  DOCKER_BUILDKIT=1 docker build --target runtime -t "$image" "$PROTO_DIR"
  [[ "$PUSH_IMAGE" == "1" ]] && docker push "$image"
  [[ -n "$KIND_CLUSTER" ]] && kind load docker-image "$image" --name "$KIND_CLUSTER"
}

build_sysimage_image() {
  local image="$1" n_profiles="$2"
  [[ "$SKIP_BUILD" == "1" ]] && return
  echo "== Build AOT sysimage image $image (profiles=$n_profiles) =="
  DOCKER_BUILDKIT=1 docker build \
    --target sysimage-builder \
    --build-arg "N_PROFILES=$n_profiles" \
    -t "$image" "$PROTO_DIR"
  [[ "$PUSH_IMAGE" == "1" ]] && docker push "$image"
  [[ -n "$KIND_CLUSTER" ]] && kind load docker-image "$image" --name "$KIND_CLUSTER"
}

deploy_direct() {
  local image="$1" mode="$2" label="$3"
  local manifest="$MANIFEST_ROOT/${FUNCTION_NAME}-${mode}.yaml"
  mkdir -p "$MANIFEST_ROOT"
  cat > "$manifest" <<YAML
apiVersion: apps/v1
kind: Deployment
metadata:
  name: $FUNCTION_NAME
  namespace: $FUNCTION_NAMESPACE
  labels:
    faas_function: $FUNCTION_NAME
spec:
  replicas: 1
  selector:
    matchLabels:
      faas_function: $FUNCTION_NAME
  template:
    metadata:
      labels:
        faas_function: $FUNCTION_NAME
    spec:
      terminationGracePeriodSeconds: 20
      containers:
        - name: $FUNCTION_NAME
          image: $image
          imagePullPolicy: IfNotPresent
          ports:
            - containerPort: 8080
          env:
            - name: POD_UID
              valueFrom:
                fieldRef:
                  fieldPath: metadata.uid
            - name: BUILD_LABEL
              value: "$label"
            - name: JULIA_CACHE_MODE
              value: "$mode"
            - name: REDIS_HOST
              value: "$REDIS_HOST"
            - name: REDIS_PORT
              value: "$REDIS_PORT"
            - name: read_timeout
              value: "$WATCHDOG_TIMEOUT"
            - name: write_timeout
              value: "$WATCHDOG_TIMEOUT"
            - name: exec_timeout
              value: "$WATCHDOG_TIMEOUT"
          livenessProbe:
            httpGet:
              path: /_/health
              port: 8080
            initialDelaySeconds: $PROBE_INITIAL_DELAY
            periodSeconds: 10
            timeoutSeconds: 5
          readinessProbe:
            httpGet:
              path: /_/health
              port: 8080
            initialDelaySeconds: $PROBE_INITIAL_DELAY
            periodSeconds: 10
            timeoutSeconds: 5
---
apiVersion: v1
kind: Service
metadata:
  name: $FUNCTION_NAME
  namespace: $FUNCTION_NAMESPACE
spec:
  selector:
    faas_function: $FUNCTION_NAME
  ports:
    - name: http
      port: 8080
      targetPort: 8080
YAML
  kubectl apply -f "$manifest"
  kubectl rollout status "deployment/$FUNCTION_NAME" -n "$FUNCTION_NAMESPACE" --timeout=300s
}

deploy_function() {
  local image="$1" mode="$2" label="$3"
  echo "== Deploy $FUNCTION_NAME mode=$mode image=$image =="
  if [[ "$DEPLOY_BACKEND" == "direct" ]]; then
    deploy_direct "$image" "$mode" "$label"
    return
  fi
  JULIA_CACHE_MODE="$mode" BUILD_LABEL="$label" JULIA_JVM_IMAGE="$image" \
    faas-cli deploy -f "$PROTO_DIR/stack.yml" --gateway "$OPENFAAS_GATEWAY"
  kubectl rollout status "deployment/$FUNCTION_NAME" -n "$FUNCTION_NAMESPACE" --timeout=300s
}

measure_workload() {
  local workload="$1" mode="$2"
  local slug csv summary plot_summary png post_ready_delay
  slug="$(slugify "$workload")-$mode"
  csv="$RESULT_ROOT/$slug.csv"
  summary="$RESULT_ROOT/$slug.json"
  plot_summary="$RESULT_ROOT/$slug-plot-summary.json"
  png="$RESULT_ROOT/$slug-warmup.png"
  post_ready_delay="$POST_READY_DELAY"
  [[ "$mode" == sysimage* ]] && post_ready_delay="$SYSIMAGE_POST_READY_DELAY"

  echo "== Measure workload=$workload mode=$mode invocations=$INVOCATIONS segment=$SEGMENT_LENGTH churn_at=${CHURN_AT:-none} post_ready_delay=$post_ready_delay =="
  local bench_args=(
    python3 "$PROTO_DIR/run_churn_bench.py"
    --function "$FUNCTION_NAME" \
    --namespace "$FUNCTION_NAMESPACE" \
    --gateway "$OPENFAAS_GATEWAY" \
    --workload "$workload" \
    --size "$SIZE" \
    --invocations "$INVOCATIONS" \
    --segment-length "$SEGMENT_LENGTH" \
    --post-ready-delay "$post_ready_delay" \
    --csv "$csv" \
    --summary "$summary"
  )
  [[ -n "$CHURN_AT" ]] && bench_args+=(--churn-at "$CHURN_AT")
  "${bench_args[@]}"

  python3 "$PLOT_SCRIPT" \
    --csv "$csv" \
    --out "$png" \
    --summary "$plot_summary" \
    --title "Real OpenFaaS Julia $workload - $mode raw latency"
}

plot_comparison() {
  local workload="$1"
  local csvs=("$RESULT_ROOT/$(slugify "$workload")-baseline.csv")
  local labels=("baseline")
  for n_profiles in "${PROFILE_COUNT_LIST[@]}"; do
    csvs+=("$RESULT_ROOT/$(slugify "$workload")-sysimage${n_profiles}.csv")
    labels+=("sysimage${n_profiles}")
  done
  python3 "$PLOT_SCRIPT" \
    --csv "${csvs[@]}" \
    --labels "${labels[@]}" \
    --out "$RESULT_ROOT/$(slugify "$workload")-aot-comparison.png" \
    --title "Real OpenFaaS Julia $workload - baseline vs AOT profile cache"

  python3 "$PLOT_SCRIPT" \
    --csv "${csvs[@]}" \
    --labels "${labels[@]}" \
    --out "$RESULT_ROOT/$(slugify "$workload")-baseline-vs-aot-openfaas-pod-churn-raw.png" \
    --title "Real OpenFaaS Julia $workload - baseline vs AOT profile cache"
}

evaluate_comparison() {
  local workload="$1"
  local csvs=("$RESULT_ROOT/$(slugify "$workload")-baseline.csv")
  local labels=("baseline")
  for n_profiles in "${PROFILE_COUNT_LIST[@]}"; do
    csvs+=("$RESULT_ROOT/$(slugify "$workload")-sysimage${n_profiles}.csv")
    labels+=("sysimage${n_profiles}")
  done

  python3 "$PROTO_DIR/evaluate_warmup_shape.py" \
    --csv "${csvs[@]}" \
    --labels "${labels[@]}" \
    --out "$RESULT_ROOT/$(slugify "$workload")-real-openfaas-shape-eval.json"
}

require_cmd docker
require_cmd kubectl
require_cmd python3
[[ "$DEPLOY_BACKEND" != "direct" ]] && require_cmd faas-cli
[[ -n "$KIND_CLUSTER" ]] && require_cmd kind

mkdir -p "$RESULT_ROOT"

RUNTIME_IMAGE="${BASELINE_IMAGE:-${IMAGE_PREFIX}:${RUN_ID}-baseline}"
build_runtime_image "$RUNTIME_IMAGE"

deploy_function "$RUNTIME_IMAGE" "baseline" "${RUN_ID}-baseline"
for workload in "${WORKLOAD_LIST[@]}"; do
  measure_workload "$workload" "baseline"
done

for n_profiles in "${PROFILE_COUNT_LIST[@]}"; do
  image_override_var="SYSIMAGE${n_profiles}_IMAGE"
  SYSIMAGE_TAG="${!image_override_var:-${IMAGE_PREFIX}:${RUN_ID}-sysimage${n_profiles}}"
  build_sysimage_image "$SYSIMAGE_TAG" "$n_profiles"
  deploy_function "$SYSIMAGE_TAG" "sysimage" "${RUN_ID}-sysimage${n_profiles}"
  for workload in "${WORKLOAD_LIST[@]}"; do
    measure_workload "$workload" "sysimage${n_profiles}"
  done
done

for workload in "${WORKLOAD_LIST[@]}"; do
  plot_comparison "$workload"
  evaluate_comparison "$workload"
done

echo
echo "Done."
echo "  run:     $RUN_ID"
echo "  results: $RESULT_ROOT"
