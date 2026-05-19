#!/usr/bin/env bash
set -euo pipefail

PROTO_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$PROTO_DIR/../.." && pwd)"

FUNCTION_NAME="${FUNCTION_NAME:-node-v8-cache}"
FUNCTION_NAMESPACE="${FUNCTION_NAMESPACE:-openfaas-fn}"
OPENFAAS_NAMESPACE="${OPENFAAS_NAMESPACE:-openfaas}"
OPENFAAS_GATEWAY="${OPENFAAS_GATEWAY:-http://127.0.0.1:8080}"

RUN_ID="${RUN_ID:-$(date +%Y%m%d-%H%M%S)}"
IMAGE_PREFIX="${IMAGE_PREFIX:-node-v8-cache}"
PUSH_IMAGE="${PUSH_IMAGE:-0}"
KIND_CLUSTER="${KIND_CLUSTER:-openfaas}"
REDIS_ADDR="${REDIS_ADDR:-profile-cache-redis.${FUNCTION_NAMESPACE}.svc.cluster.local:6379}"
REDIS_PASSWORD="${REDIS_PASSWORD:-}"
REDIS_DB="${REDIS_DB:-0}"
V8_CACHE_KEY_PREFIX="${V8_CACHE_KEY_PREFIX:-node-v8:${FUNCTION_NAME}}"

WORKLOAD="${WORKLOAD:-lusearch}"
WORKLOADS="${WORKLOADS:-$WORKLOAD}"
FUNCTION_COUNT="${FUNCTION_COUNT:-3000}"
ROUNDS="${ROUNDS:-10000}"
REQUEST_INVOCATIONS="${REQUEST_INVOCATIONS:-8}"
CHURN_INVOCATIONS="${CHURN_INVOCATIONS:-40}"
CHURN_SEGMENT_LENGTH="${CHURN_SEGMENT_LENGTH:-8}"
CHURN_AT="${CHURN_AT:-}"
CHURN_INVOKE_TIMEOUT="${CHURN_INVOKE_TIMEOUT:-120}"
CHURN_POST_READY_DELAY="${CHURN_POST_READY_DELAY:-0}"

ARTIFACT_ROOT="$PROTO_DIR/.runs/$RUN_ID"
RESULT_ROOT="$ARTIFACT_ROOT/results"
MANIFEST_ROOT="$ARTIFACT_ROOT/k8s"
URL="$OPENFAAS_GATEWAY/function/$FUNCTION_NAME"

mkdir -p "$RESULT_ROOT" "$MANIFEST_ROOT"

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "missing required command: $1" >&2
    exit 1
  fi
}

openfaas_password() {
  if [[ -n "${OPENFAAS_PASSWORD:-}" ]]; then
    printf "%s" "$OPENFAAS_PASSWORD"
    return
  fi
  if kubectl -n "$OPENFAAS_NAMESPACE" get secret basic-auth >/dev/null 2>&1; then
    kubectl -n "$OPENFAAS_NAMESPACE" get secret basic-auth -o jsonpath='{.data.basic-auth-password}' | base64 --decode
  fi
}

curl_gateway() {
  local password="${OPENFAAS_PASSWORD_RESOLVED:-}"
  if [[ -n "$password" ]]; then
    curl -fsS -u "admin:$password" "$@"
  else
    curl -fsS "$@"
  fi
}

build_image() {
  local image="$1"
  echo "== Build image $image =="
  docker build \
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
  local require_artifact="$4"
  local manifest="$MANIFEST_ROOT/$FUNCTION_NAME-$WORKLOAD-$mode.json"
  local cache_key="${V8_CACHE_KEY_PREFIX}:${WORKLOAD}:cache:${RUN_ID}"

  echo "== Deploy $FUNCTION_NAME workload=$WORKLOAD mode=$mode =="
  cat > "$manifest" <<JSON
{
  "apiVersion": "v1",
  "kind": "List",
  "items": [
    {
      "apiVersion": "apps/v1",
      "kind": "Deployment",
      "metadata": {
        "name": "$FUNCTION_NAME",
        "namespace": "$FUNCTION_NAMESPACE",
        "labels": {
          "faas_function": "$FUNCTION_NAME",
          "profile-cache-run": "$RUN_ID",
          "profile-cache-build": "$label"
        }
      },
      "spec": {
        "replicas": 1,
        "selector": {"matchLabels": {"faas_function": "$FUNCTION_NAME"}},
        "template": {
          "metadata": {
            "labels": {
              "faas_function": "$FUNCTION_NAME",
              "profile-cache-run": "$RUN_ID",
              "profile-cache-build": "$label"
            }
          },
          "spec": {
            "terminationGracePeriodSeconds": 10,
            "containers": [
              {
                "name": "$FUNCTION_NAME",
                "image": "$image",
                "imagePullPolicy": "IfNotPresent",
                "ports": [{"name": "http", "containerPort": 8080}],
                "env": [
                  {"name": "POD_UID", "valueFrom": {"fieldRef": {"fieldPath": "metadata.uid"}}},
                  {"name": "BUILD_LABEL", "value": "$label"},
                  {"name": "WORKLOAD", "value": "$WORKLOAD"},
                  {"name": "V8_CACHE_MODE", "value": "$mode"},
                  {"name": "V8_CACHE_KEY", "value": "$cache_key"},
                  {"name": "V8_CACHE_REQUIRE_ARTIFACT", "value": "$require_artifact"},
                  {"name": "REDIS_ADDR", "value": "$REDIS_ADDR"},
                  {"name": "REDIS_PASSWORD", "value": "$REDIS_PASSWORD"},
                  {"name": "REDIS_DB", "value": "$REDIS_DB"},
                  {"name": "FUNCTION_COUNT", "value": "$FUNCTION_COUNT"},
                  {"name": "ROUNDS", "value": "$ROUNDS"},
                  {"name": "REQUEST_INVOCATIONS", "value": "$REQUEST_INVOCATIONS"}
                ],
                "readinessProbe": {
                  "httpGet": {"path": "/healthz", "port": 8080},
                  "initialDelaySeconds": 1,
                  "periodSeconds": 2,
                  "timeoutSeconds": 2
                },
                "livenessProbe": {
                  "httpGet": {"path": "/healthz", "port": 8080},
                  "initialDelaySeconds": 2,
                  "periodSeconds": 5,
                  "timeoutSeconds": 2
                }
              }
            ]
          }
        }
      }
    },
    {
      "apiVersion": "v1",
      "kind": "Service",
      "metadata": {
        "name": "$FUNCTION_NAME",
        "namespace": "$FUNCTION_NAMESPACE",
        "labels": {
          "faas_function": "$FUNCTION_NAME",
          "profile-cache-run": "$RUN_ID",
          "profile-cache-build": "$label"
        }
      },
      "spec": {
        "selector": {"faas_function": "$FUNCTION_NAME"},
        "ports": [{"name": "http", "port": 8080, "targetPort": "http"}]
      }
    }
  ]
}
JSON
  kubectl apply -f "$manifest"
  kubectl rollout status "deployment/$FUNCTION_NAME" -n "$FUNCTION_NAMESPACE" --timeout=240s
}

run_churn() {
  local treatment="$1"
  local result_prefix="$2"
  local password="${OPENFAAS_PASSWORD_RESOLVED:-}"
  python3 "$PROTO_DIR/run_churn_bench.py" \
    --function "$FUNCTION_NAME" \
    --namespace "$FUNCTION_NAMESPACE" \
    --gateway "$OPENFAAS_GATEWAY" \
    --treatment "$treatment" \
    --workload "$WORKLOAD" \
    --function-count "$FUNCTION_COUNT" \
    --rounds "$ROUNDS" \
    --request-invocations "$REQUEST_INVOCATIONS" \
    --invocations "$CHURN_INVOCATIONS" \
    --segment-length "$CHURN_SEGMENT_LENGTH" \
    --churn-at "$CHURN_AT" \
    --invoke-timeout "$CHURN_INVOKE_TIMEOUT" \
    --post-ready-delay "$CHURN_POST_READY_DELAY" \
    --username "${OPENFAAS_USERNAME:-admin}" \
    --password "$password" \
    --csv "$RESULT_ROOT/${result_prefix}.csv" \
    --summary "$RESULT_ROOT/${result_prefix}.json"
}

require_cmd docker
require_cmd kind
require_cmd kubectl
require_cmd curl
require_cmd python3

OPENFAAS_PASSWORD_RESOLVED="$(openfaas_password || true)"
export OPENFAAS_PASSWORD_RESOLVED

IMAGE="${IMAGE_PREFIX}:${RUN_ID}"
build_image "$IMAGE"

for WORKLOAD in ${WORKLOADS//,/ }; do
  export WORKLOAD
  echo
  echo "== Workload $WORKLOAD =="

  deploy_mode "$IMAGE" "populate" "populate" "0"
  echo "== Check Redis connectivity =="
  curl_gateway "$URL/cache/ping" > "$RESULT_ROOT/${WORKLOAD}-redis-ping.json"
  echo
  echo "== Populate V8 cachedData artifact in Redis for $WORKLOAD =="
  curl_gateway \
    -X POST \
    -H "Content-Type: application/json" \
    --data "{\"workload\":\"$WORKLOAD\",\"functionCount\":$FUNCTION_COUNT,\"rounds\":$ROUNDS,\"invocations\":$REQUEST_INVOCATIONS}" \
    "$URL/cache/populate" > "$RESULT_ROOT/${WORKLOAD}-populate.json"
  echo

  deploy_mode "$IMAGE" "baseline" "baseline" "0"
  run_churn "baseline" "node-openfaas-${WORKLOAD}-baseline-pod-churn"

  deploy_mode "$IMAGE" "redis" "v8-cached-data" "1"
  run_churn "v8-cached-data" "node-openfaas-${WORKLOAD}-v8-cached-data-pod-churn"
done

echo
echo "Done."
echo "  run:      $RUN_ID"
echo "  image:    $IMAGE"
echo "  workloads:$WORKLOADS"
echo "  results:  $RESULT_ROOT"
