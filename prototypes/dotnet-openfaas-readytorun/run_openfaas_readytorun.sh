#!/usr/bin/env bash
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

PROTO_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$PROTO_DIR/../.." && pwd)"

FUNCTION_NAME="${FUNCTION_NAME:-dotnet-r2r}"
FUNCTION_NAMESPACE="${FUNCTION_NAMESPACE:-openfaas-fn}"
OPENFAAS_NAMESPACE="${OPENFAAS_NAMESPACE:-openfaas}"
OPENFAAS_GATEWAY="${OPENFAAS_GATEWAY:-http://127.0.0.1:8080}"

RUN_ID="${RUN_ID:-$(date +%Y%m%d-%H%M%S)}"
DOTNET_BIN="${DOTNET_BIN:-dotnet}"
DOTNET_CLI_HOME="${DOTNET_CLI_HOME:-/private/tmp/dotnet-home}"
NUGET_PACKAGES="${NUGET_PACKAGES:-/private/tmp/nuget-packages}"
RID="${RID:-linux-musl-arm64}"
IMAGE_PREFIX="${IMAGE_PREFIX:-dotnet-openfaas-r2r}"
PUSH_IMAGE="${PUSH_IMAGE:-0}"
KIND_CLUSTER="${KIND_CLUSTER:-openfaas}"
OF_WATCHDOG_VERSION="${OF_WATCHDOG_VERSION:-0.9.16}"
DEPLOY_BACKEND="${DEPLOY_BACKEND:-direct}"
SKIP_BUILD="${SKIP_BUILD:-0}"
SKIP_PUBLISH="${SKIP_PUBLISH:-$SKIP_BUILD}"

MEASURE_REQUESTS="${MEASURE_REQUESTS:-80}"
MEASURE_WARMUP="${MEASURE_WARMUP:-10}"
MEASURE_CONCURRENCY="${MEASURE_CONCURRENCY:-1}"
MEASURE_MODE="${MEASURE_MODE:-batch}"
CHURN_INVOCATIONS="${CHURN_INVOCATIONS:-2000}"
SEGMENT_LENGTH="${SEGMENT_LENGTH:-0}"
CHURN_AT="${CHURN_AT:-1,112,478,800,1044,1283,1679,1790}"
POST_READY_DELAY="${POST_READY_DELAY:-0}"
HANDLER_ITERATIONS="${HANDLER_ITERATIONS:-250000}"
SCENARIOS="${SCENARIOS:-serve-hot serve-mixed}"
VARIANTS="${VARIANTS:-il r2r nativeaot}"

BUILD_ROOT="$PROTO_DIR/build/$RUN_ID"
RESULT_ROOT="$PROTO_DIR/.runs/$RUN_ID/results"
PROJECT="$PROTO_DIR/DotnetOpenFaas.csproj"
URL="$OPENFAAS_GATEWAY/function/$FUNCTION_NAME"

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "missing required command: $1" >&2
    exit 1
  fi
}

dotnet_cmd() {
  env \
    DOTNET_CLI_HOME="$DOTNET_CLI_HOME" \
    NUGET_PACKAGES="$NUGET_PACKAGES" \
    DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1 \
    DOTNET_CLI_TELEMETRY_OPTOUT=1 \
    DOTNET_NOLOGO=1 \
    DOTNET_GENERATE_ASPNET_CERTIFICATE=false \
    "$DOTNET_BIN" "$@"
}

login_openfaas() {
  if [[ -n "${OPENFAAS_PASSWORD:-}" ]]; then
    printf "%s" "$OPENFAAS_PASSWORD" | faas-cli login --username "${OPENFAAS_USERNAME:-admin}" --password-stdin --gateway "$OPENFAAS_GATEWAY"
    return
  fi

  if kubectl -n "$OPENFAAS_NAMESPACE" get secret basic-auth >/dev/null 2>&1; then
    local password
    password="$(kubectl -n "$OPENFAAS_NAMESPACE" get secret basic-auth -o jsonpath='{.data.basic-auth-password}' | base64 --decode)"
    printf "%s" "$password" | faas-cli login --username admin --password-stdin --gateway "$OPENFAAS_GATEWAY"
  else
    echo "OpenFaaS basic-auth secret not found; assuming faas-cli is already logged in or auth is disabled."
  fi
}

publish_variant() {
  local label="$1"
  local ready_to_run="$2"
  local out_dir="$BUILD_ROOT/$label"

  mkdir -p "$out_dir"
  echo "== Publish $label self-contained RID=$RID ReadyToRun=$ready_to_run =="
  dotnet_cmd publish "$PROJECT" -c Release -r "$RID" --self-contained true -o "$out_dir" \
    -p:PublishReadyToRun="$ready_to_run" \
    -p:PublishSingleFile=true \
    -p:UseAppHost=true \
    -p:InvariantGlobalization=true
}

build_load_deploy() {
  local label="$1"
  local image="${IMAGE_PREFIX}:${RUN_ID}-${label}"
  local publish_dir="build/$RUN_ID/$label"
  local image_override_var=""

  case "$label" in
    il) image_override_var="IL_IMAGE" ;;
    r2r) image_override_var="R2R_IMAGE" ;;
    nativeaot) image_override_var="NATIVEAOT_IMAGE" ;;
  esac
  if [[ -n "$image_override_var" && -n "${!image_override_var:-}" ]]; then
    image="${!image_override_var}"
  fi

  if [[ "$SKIP_BUILD" == "1" ]]; then
    echo "== Reuse image $image =="
  else
    echo "== Build image $image =="
    if [[ "$label" == "nativeaot" ]]; then
      docker build \
        --target nativeaot-runtime \
        --build-arg "BUILD_LABEL=$label" \
        --build-arg "RID=$RID" \
        --build-arg "OF_WATCHDOG_VERSION=$OF_WATCHDOG_VERSION" \
        -t "$image" "$PROTO_DIR"
    else
      docker build \
        --target runtime \
        --build-arg "PUBLISH_DIR=$publish_dir" \
        --build-arg "BUILD_LABEL=$label" \
        --build-arg "OF_WATCHDOG_VERSION=$OF_WATCHDOG_VERSION" \
        -t "$image" "$PROTO_DIR"
    fi
  fi

  if [[ "$PUSH_IMAGE" == "1" ]]; then
    echo "== Push image $image =="
    docker push "$image"
  fi
  if [[ -n "$KIND_CLUSTER" ]]; then
    echo "== Load image $image into kind cluster $KIND_CLUSTER =="
    kind load docker-image "$image" --name "$KIND_CLUSTER"
  fi

  echo "== Deploy $FUNCTION_NAME with $label =="
  if [[ "$DEPLOY_BACKEND" == "direct" ]]; then
    deploy_direct "$image" "$label"
    return
  fi

  export OPENFAAS_GATEWAY DOTNET_OPENFAAS_IMAGE="$image" BUILD_LABEL="$label"
  faas-cli deploy -f "$PROTO_DIR/stack.yml" --gateway "$OPENFAAS_GATEWAY"
  kubectl label "deployment/$FUNCTION_NAME" -n "$FUNCTION_NAMESPACE" \
    com.openfaas.scale.min=1 \
    com.openfaas.scale.max=1 \
    com.openfaas.scale.zero=false \
    --overwrite
  kubectl scale "deployment/$FUNCTION_NAME" -n "$FUNCTION_NAMESPACE" --replicas=1
  kubectl rollout status "deployment/$FUNCTION_NAME" -n "$FUNCTION_NAMESPACE" --timeout=240s
}

deploy_direct() {
  local image="$1"
  local label="$2"
  local manifest="$BUILD_ROOT/k8s/$FUNCTION_NAME-$label.yaml"
  mkdir -p "$BUILD_ROOT/k8s"
  cat > "$manifest" <<YAML
apiVersion: apps/v1
kind: Deployment
metadata:
  name: $FUNCTION_NAME
  namespace: $FUNCTION_NAMESPACE
  labels:
    faas_function: $FUNCTION_NAME
    com.openfaas.scale.min: "1"
    com.openfaas.scale.max: "1"
    com.openfaas.scale.zero: "false"
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
            - name: http
              containerPort: 8080
          env:
            - name: POD_UID
              valueFrom:
                fieldRef:
                  fieldPath: metadata.uid
            - name: BUILD_LABEL
              value: "$label"
            - name: DOTNET_SYSTEM_GLOBALIZATION_INVARIANT
              value: "1"
            - name: read_timeout
              value: "120s"
            - name: write_timeout
              value: "120s"
            - name: exec_timeout
              value: "120s"
          livenessProbe:
            httpGet:
              path: /_/health
              port: 8080
            initialDelaySeconds: 3
            periodSeconds: 5
            timeoutSeconds: 3
          readinessProbe:
            httpGet:
              path: /_/health
              port: 8080
            initialDelaySeconds: 3
            periodSeconds: 5
            timeoutSeconds: 3
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
  kubectl rollout status "deployment/$FUNCTION_NAME" -n "$FUNCTION_NAMESPACE" --timeout=240s
}

measure() {
  local build_label="$1"
  local scenario="$2"
  local out_prefix="dotnet-openfaas-${build_label}-${scenario}"

  python3 "$ROOT_DIR/scripts/http_invoke_latency.py" \
    --url "$URL/work" \
    --method POST \
    --header "Content-Type: application/json" \
    --body "{\"scenario\":\"$scenario\",\"iterations\":$HANDLER_ITERATIONS}" \
    --requests "$MEASURE_REQUESTS" \
    --warmup "$MEASURE_WARMUP" \
    --concurrency "$MEASURE_CONCURRENCY" \
    --timeout 120 \
    --label "$out_prefix" \
    --csv "$RESULT_ROOT/${out_prefix}.csv" \
    --summary "$RESULT_ROOT/${out_prefix}.json" \
    --svg "$RESULT_ROOT/${out_prefix}.svg"
}

measure_churn() {
  local build_label="$1"
  local scenario="$2"
  local out_prefix="dotnet-openfaas-${build_label}-${scenario}"

  python3 "$PROTO_DIR/run_churn_bench.py" \
    --function "$FUNCTION_NAME" \
    --namespace "$FUNCTION_NAMESPACE" \
    --gateway "$OPENFAAS_GATEWAY" \
    --scenario "$scenario" \
    --iterations "$HANDLER_ITERATIONS" \
    --invocations "$CHURN_INVOCATIONS" \
    --segment-length "$SEGMENT_LENGTH" \
    --churn-at "$CHURN_AT" \
    --post-ready-delay "$POST_READY_DELAY" \
    --mode "$build_label" \
    --csv "$RESULT_ROOT/${out_prefix}-churn.csv" \
    --summary "$RESULT_ROOT/${out_prefix}-churn.json"

  env MPLBACKEND=Agg MPLCONFIGDIR="$PROTO_DIR/tmp/mplconfig" XDG_CACHE_HOME="$PROTO_DIR/tmp/fontconfig" \
    python3 "$PROTO_DIR/plot_openwhisk_churn.py" \
      --csv "$RESULT_ROOT/${out_prefix}-churn.csv" \
      --out "$RESULT_ROOT/${out_prefix}-openfaas-pod-churn-raw.png" \
      --summary "$RESULT_ROOT/${out_prefix}-plot-summary.json" \
      --title "Real OpenFaaS C#/.NET $scenario - $build_label raw latency"
}

plot_churn_comparison() {
  local scenario="$1"
  local csvs=()
  local labels=()
  for label in $VARIANTS; do
    csvs+=("$RESULT_ROOT/dotnet-openfaas-${label}-${scenario}-churn.csv")
    labels+=("$label")
  done

  env MPLBACKEND=Agg MPLCONFIGDIR="$PROTO_DIR/tmp/mplconfig" XDG_CACHE_HOME="$PROTO_DIR/tmp/fontconfig" \
    python3 "$PROTO_DIR/plot_openwhisk_churn.py" \
      --csv "${csvs[@]}" \
      --labels "${labels[@]}" \
      --out "$RESULT_ROOT/${scenario}-il-vs-aot-openfaas-pod-churn-raw.png" \
      --title "Real OpenFaaS C#/.NET $scenario - IL vs ReadyToRun vs NativeAOT"
}

require_cmd docker
require_cmd kubectl
[[ -n "$KIND_CLUSTER" ]] && require_cmd kind
require_cmd python3
require_cmd "$DOTNET_BIN"
[[ "$DEPLOY_BACKEND" != "direct" ]] && require_cmd faas-cli

mkdir -p \
  "$BUILD_ROOT" \
  "$RESULT_ROOT" \
  "$DOTNET_CLI_HOME" \
  "$NUGET_PACKAGES" \
  "$PROTO_DIR/tmp/mplconfig" \
  "$PROTO_DIR/tmp/fontconfig/fontconfig"

[[ "$DEPLOY_BACKEND" != "direct" ]] && login_openfaas
if [[ "$SKIP_PUBLISH" == "1" ]]; then
  echo "== Skip dotnet publish; reusing prebuilt images =="
else
  for label in $VARIANTS; do
    case "$label" in
      il) publish_variant "il" "false" ;;
      r2r) publish_variant "r2r" "true" ;;
      nativeaot) ;;
      *)
        echo "unknown variant: $label (expected il, r2r, nativeaot)" >&2
        exit 2
        ;;
    esac
  done
fi

for label in $VARIANTS; do
  build_load_deploy "$label"
  for scenario in $SCENARIOS; do
    echo "== Measure $label $scenario =="
    if [[ "$MEASURE_MODE" == "churn" ]]; then
      measure_churn "$label" "$scenario"
    else
      measure "$label" "$scenario"
    fi
  done
done

if [[ "$MEASURE_MODE" == "churn" ]]; then
  for scenario in $SCENARIOS; do
    plot_churn_comparison "$scenario"
  done
fi

echo
echo "Done."
echo "  run:      $RUN_ID"
echo "  results:  $RESULT_ROOT"
