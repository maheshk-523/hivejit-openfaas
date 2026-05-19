#!/usr/bin/env bash
set -euo pipefail

PROTO_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$PROTO_DIR/../.." && pwd)"

FUNCTION_NAME="${FUNCTION_NAME:-jax-xla-redis}"
FUNCTION_NAMESPACE="${FUNCTION_NAMESPACE:-openfaas-fn}"
OPENFAAS_NAMESPACE="${OPENFAAS_NAMESPACE:-openfaas}"
OPENFAAS_GATEWAY="${OPENFAAS_GATEWAY:-http://127.0.0.1:8080}"

RUN_ID="${RUN_ID:-$(date +%Y%m%d-%H%M%S)}"
SIGNATURES="${SIGNATURES:-dacapo-lusearch dacapo-h2 dacapo-fop dacapo-jython dacapo-eclipse}"
COLD_STARTS="${COLD_STARTS:-10}"
EXECUTIONS="${EXECUTIONS:-3}"
COMPILE_VARIANTS="${COMPILE_VARIANTS:-1}"
VARIANT_SCHEDULE="${VARIANT_SCHEDULE:-}"
WATCHDOG_TIMEOUT="${WATCHDOG_TIMEOUT:-300s}"
MEASURE_MODE="${MEASURE_MODE:-cold-start}"
POD_CHURN_INVOCATIONS="${POD_CHURN_INVOCATIONS:-64}"
POD_CHURN_SEGMENT_LENGTH="${POD_CHURN_SEGMENT_LENGTH:-8}"
POD_CHURN_AT="${POD_CHURN_AT:-}"
POD_CHURN_POST_READY_DELAY="${POD_CHURN_POST_READY_DELAY:-0}"

IMAGE_PREFIX="${IMAGE_PREFIX:-ttl.sh/${FUNCTION_NAME}-${USER:-user}}"
PUSH_IMAGE="${PUSH_IMAGE:-1}"
KIND_CLUSTER="${KIND_CLUSTER:-}"
SKIP_BUILD="${SKIP_BUILD:-0}"
INSTALL_REDIS="${INSTALL_REDIS:-0}"
OF_WATCHDOG_VERSION="${OF_WATCHDOG_VERSION:-0.9.16}"
JAX_PACKAGE="${JAX_PACKAGE:-jax[cpu]}"
DEPLOY_BACKEND="${DEPLOY_BACKEND:-faas-cli}"

REDIS_ADDR="${REDIS_ADDR:-profile-cache-redis.${FUNCTION_NAMESPACE}.svc.cluster.local:6379}"
REDIS_PASSWORD="${REDIS_PASSWORD:-}"
REDIS_DB="${REDIS_DB:-0}"
REDIS_TIMEOUT="${REDIS_TIMEOUT:-10s}"
JAX_CACHE_KEY_PREFIX="${JAX_CACHE_KEY_PREFIX:-jax-xla:${FUNCTION_NAME}}"
JAX_CACHE_DIR="${JAX_CACHE_DIR:-/profiles/jax-cache}"
JAX_CACHE_IMPORT_META="${JAX_CACHE_IMPORT_META:-/profiles/jax-cache-import.json}"
JAX_CACHE_EXPORT_META="${JAX_CACHE_EXPORT_META:-/profiles/jax-cache-export.json}"

ARTIFACT_ROOT="$PROTO_DIR/.runs/$RUN_ID"
RESULT_ROOT_BASE="$ARTIFACT_ROOT/results"
MANIFEST_ROOT="$ARTIFACT_ROOT/k8s"
URL="$OPENFAAS_GATEWAY/function/$FUNCTION_NAME"

if [[ "$FUNCTION_NAME" != "jax-xla-redis" ]]; then
  echo "FUNCTION_NAME must be jax-xla-redis unless stack.yml is updated too." >&2
  exit 2
fi

read -r -a SIGNATURE_LIST <<< "$SIGNATURES"
if (( ${#SIGNATURE_LIST[@]} == 0 )); then
  echo "SIGNATURES must contain at least one signature" >&2
  exit 1
fi

slugify() {
  printf "%s" "$1" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9._-' '-'
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "missing required command: $1" >&2
    exit 1
  fi
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

build_image() {
  local image="$1"
  echo "== Build JAX/OpenFaaS image $image =="
  docker build \
    --build-arg "OF_WATCHDOG_VERSION=$OF_WATCHDOG_VERSION" \
    --build-arg "JAX_PACKAGE=$JAX_PACKAGE" \
    --build-arg "BUILD_LABEL=$RUN_ID" \
    -t "$image" "$PROTO_DIR"

  if [[ "$PUSH_IMAGE" == "1" ]]; then
    echo "== Push image $image =="
    docker push "$image"
  fi
  if [[ -n "$KIND_CLUSTER" ]]; then
    echo "== Load image $image into kind cluster $KIND_CLUSTER =="
    kind load docker-image "$image" --name "$KIND_CLUSTER"
  fi
}

deploy_mode() {
  local image="$1"
  local mode="$2"
  local label="$3"
  local signature="$4"
  local cache_key="$5"
  local require_artifact="$6"

  echo "== Deploy $FUNCTION_NAME mode=$mode signature=$signature =="
  export OPENFAAS_GATEWAY JAX_XLA_IMAGE="$image" BUILD_LABEL="$label" EXECUTIONS COMPILE_VARIANTS VARIANT_SCHEDULE WATCHDOG_TIMEOUT
  export JAX_CACHE_MODE="$mode" JAX_CACHE_KEY="$cache_key" JAX_CACHE_DIR JAX_CACHE_IMPORT_META JAX_CACHE_EXPORT_META
  export JAX_CACHE_REQUIRE_ARTIFACT="$require_artifact" REDIS_ADDR REDIS_PASSWORD REDIS_DB REDIS_TIMEOUT SIGNATURE="$signature"
  if [[ "$DEPLOY_BACKEND" == "direct" ]]; then
    deploy_direct "$image" "$mode" "$label" "$signature" "$cache_key" "$require_artifact"
  else
    faas-cli deploy -f "$PROTO_DIR/stack.yml" --gateway "$OPENFAAS_GATEWAY"

    kubectl patch "deployment/$FUNCTION_NAME" -n "$FUNCTION_NAMESPACE" --patch-file "$PROTO_DIR/k8s/function-patch.yaml"
    kubectl label "deployment/$FUNCTION_NAME" -n "$FUNCTION_NAMESPACE" \
      com.openfaas.scale.min=1 \
      com.openfaas.scale.max=1 \
      com.openfaas.scale.zero=false \
      --overwrite
    kubectl scale "deployment/$FUNCTION_NAME" -n "$FUNCTION_NAMESPACE" --replicas=1
    kubectl rollout status "deployment/$FUNCTION_NAME" -n "$FUNCTION_NAMESPACE" --timeout=300s
  fi
}

deploy_direct() {
  local image="$1"
  local mode="$2"
  local label="$3"
  local signature="$4"
  local cache_key="$5"
  local require_artifact="$6"
  local manifest="$MANIFEST_ROOT/$FUNCTION_NAME-$mode.yaml"

  mkdir -p "$MANIFEST_ROOT"
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
  annotations:
    prometheus.io.scrape: "false"
spec:
  replicas: 1
  selector:
    matchLabels:
      faas_function: $FUNCTION_NAME
  template:
    metadata:
      labels:
        faas_function: $FUNCTION_NAME
      annotations:
        prometheus.io.scrape: "false"
    spec:
      terminationGracePeriodSeconds: 20
      volumes:
        - name: jax-cache
          emptyDir: {}
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
            - name: EXECUTIONS
              value: "$EXECUTIONS"
            - name: COMPILE_VARIANTS
              value: "$COMPILE_VARIANTS"
            - name: VARIANT_SCHEDULE
              value: "$VARIANT_SCHEDULE"
            - name: JAX_CACHE_MODE
              value: "$mode"
            - name: JAX_CACHE_KEY
              value: "$cache_key"
            - name: JAX_CACHE_DIR
              value: "$JAX_CACHE_DIR"
            - name: JAX_CACHE_IMPORT_META
              value: "$JAX_CACHE_IMPORT_META"
            - name: JAX_CACHE_EXPORT_META
              value: "$JAX_CACHE_EXPORT_META"
            - name: JAX_CACHE_REQUIRE_ARTIFACT
              value: "$require_artifact"
            - name: REDIS_ADDR
              value: "$REDIS_ADDR"
            - name: REDIS_PASSWORD
              value: "$REDIS_PASSWORD"
            - name: REDIS_DB
              value: "$REDIS_DB"
            - name: REDIS_TIMEOUT
              value: "$REDIS_TIMEOUT"
            - name: SIGNATURE
              value: "$signature"
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
            initialDelaySeconds: 2
            periodSeconds: 3
            timeoutSeconds: 2
          readinessProbe:
            httpGet:
              path: /_/health
              port: 8080
            initialDelaySeconds: 2
            periodSeconds: 3
            timeoutSeconds: 2
          volumeMounts:
            - name: jax-cache
              mountPath: /profiles
---
apiVersion: v1
kind: Service
metadata:
  name: $FUNCTION_NAME
  namespace: $FUNCTION_NAMESPACE
  annotations:
    prometheus.io.scrape: "false"
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

populate_cache() {
  local signature="$1"
  local result_root="$2"
  mkdir -p "$result_root"

  echo "== Check Redis connectivity from function ($signature) =="
  curl -fsS "$URL/cache/ping" > "$result_root/redis-ping.json"
  echo

  echo "== Populate JAX persistent cache and store artifact in Redis ($signature) =="
  curl -fsS \
    -X POST \
    -H "Content-Type: application/json" \
    --data "{\"signature\":\"$signature\",\"executions\":$EXECUTIONS,\"compile_variants\":$COMPILE_VARIANTS,\"variant_schedule\":\"$VARIANT_SCHEDULE\"}" \
    "$URL/cache/populate" > "$result_root/populate.json"
}

measure_mode() {
  local signature="$1"
  local label="$2"
  local result_root="$3"

  if [[ "$MEASURE_MODE" == "pod-churn" ]]; then
    local bench_args=(
      python3 "$PROTO_DIR/measure_pod_churn.py"
      --function "$FUNCTION_NAME"
      --namespace "$FUNCTION_NAMESPACE"
      --gateway "$OPENFAAS_GATEWAY"
      --signature "$signature"
      --label "$label"
      --invocations "$POD_CHURN_INVOCATIONS"
      --segment-length "$POD_CHURN_SEGMENT_LENGTH"
      --post-ready-delay "$POD_CHURN_POST_READY_DELAY"
      --executions "$EXECUTIONS"
      --compile-variants "$COMPILE_VARIANTS"
      --variant-schedule "$VARIANT_SCHEDULE"
      --csv "$result_root/${label}.csv"
      --summary "$result_root/${label}.json"
    )
    [[ -n "$POD_CHURN_AT" ]] && bench_args+=(--churn-at "$POD_CHURN_AT")
    "${bench_args[@]}"
  elif [[ "$MEASURE_MODE" == "cold-start" ]]; then
    python3 "$PROTO_DIR/measure_cold_starts.py" \
      --function "$FUNCTION_NAME" \
      --namespace "$FUNCTION_NAMESPACE" \
      --gateway "$OPENFAAS_GATEWAY" \
      --signature "$signature" \
      --label "$label" \
      --trials "$COLD_STARTS" \
      --executions "$EXECUTIONS" \
      --compile-variants "$COMPILE_VARIANTS" \
      --variant-schedule "$VARIANT_SCHEDULE" \
      --csv "$result_root/${label}.csv" \
      --summary "$result_root/${label}.json"
  else
    echo "unknown MEASURE_MODE=$MEASURE_MODE (expected cold-start or pod-churn)" >&2
    exit 2
  fi
}

require_cmd docker
if [[ "$DEPLOY_BACKEND" != "direct" ]]; then
  require_cmd faas-cli
fi
require_cmd kubectl
require_cmd curl
require_cmd python3
if [[ -n "$KIND_CLUSTER" ]]; then
  require_cmd kind
fi

mkdir -p "$RESULT_ROOT_BASE"

if [[ "$INSTALL_REDIS" == "1" ]]; then
  echo "== Install Redis profile cache in namespace $FUNCTION_NAMESPACE =="
  kubectl apply -n "$FUNCTION_NAMESPACE" -f "$PROTO_DIR/k8s/redis.yaml"
  kubectl rollout status deployment/profile-cache-redis -n "$FUNCTION_NAMESPACE" --timeout=180s
fi

if [[ "$DEPLOY_BACKEND" != "direct" ]]; then
  login_openfaas
fi

IMAGE="${IMAGE:-${IMAGE_PREFIX}:${RUN_ID}}"
if [[ "$SKIP_BUILD" == "1" ]]; then
  echo "== Reuse existing JAX/OpenFaaS image $IMAGE =="
else
  build_image "$IMAGE"
fi

for signature in "${SIGNATURE_LIST[@]}"; do
  signature_slug="$(slugify "$signature")"
  result_root="$RESULT_ROOT_BASE/$signature_slug"
  cache_key="${JAX_CACHE_KEY_PREFIX}:cache:${RUN_ID}:${signature_slug}"
  mkdir -p "$result_root"

  deploy_mode "$IMAGE" "populate" "populate-${signature_slug}" "$signature" "$cache_key" "0"
  populate_cache "$signature" "$result_root"

  deploy_mode "$IMAGE" "baseline" "baseline-${signature_slug}" "$signature" "$cache_key" "0"
  measure_mode "$signature" "baseline" "$result_root"

  deploy_mode "$IMAGE" "redis" "redis-cache-${signature_slug}" "$signature" "$cache_key" "1"
  measure_mode "$signature" "redis-cache" "$result_root"

  python3 "$PROTO_DIR/summarize_openfaas_results.py" \
    --input "baseline=$result_root/baseline.csv" \
    --input "redis-cache=$result_root/redis-cache.csv" \
    --summary "$result_root/summary.json" \
    --svg "$result_root/cold-start-summary.svg"
done

echo
echo "Done."
echo "  run:      $RUN_ID"
echo "  image:    $IMAGE"
echo "  results:  $RESULT_ROOT_BASE"
