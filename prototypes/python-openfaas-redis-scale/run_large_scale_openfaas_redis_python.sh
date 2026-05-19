#!/usr/bin/env bash
set -euo pipefail

SCALE_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCALE_DIR/../.." && pwd)"
PY_PROTO_DIR="$ROOT_DIR/prototypes/python-profile-specialization"

RUN_ID="${RUN_ID:-$(date +%Y%m%d-%H%M%S)}"
FUNCTION_PREFIX="${FUNCTION_PREFIX:-py-spec-scale}"
FUNCTION_NAMESPACE="${FUNCTION_NAMESPACE:-openfaas-fn}"
OPENFAAS_GATEWAY="${OPENFAAS_GATEWAY:-http://127.0.0.1:8080}"
OPENFAAS_USERNAME="${OPENFAAS_USERNAME:-admin}"

BENCHMARKS="${BENCHMARKS:-dacapo-lusearch dacapo-h2 dacapo-eclipse dacapo-jython dacapo-fop}"
SHARDS="${SHARDS:-24}"
WAVES="${WAVES:-4}"
REQUESTS_PER_POD="${REQUESTS_PER_POD:-8}"
WARMUP_REQUESTS="${WARMUP_REQUESTS:-3}"
CONCURRENCY="${CONCURRENCY:-$SHARDS}"

REQUESTS="${REQUESTS:-12000}"
PROFILE_REQUESTS="${PROFILE_REQUESTS:-36000}"
PROFILE_ITERS="${PROFILE_ITERS:-3}"
WATCHDOG_TIMEOUT="${WATCHDOG_TIMEOUT:-300s}"
INVOKE_TIMEOUT="${INVOKE_TIMEOUT:-180}"
READY_TIMEOUT="${READY_TIMEOUT:-300}"
DELETE_TIMEOUT="${DELETE_TIMEOUT:-180}"
GRACE_PERIOD="${GRACE_PERIOD:-5}"

IMAGE_PREFIX="${IMAGE_PREFIX:-python-openfaas-redis-scale}"
BASE_IMAGE="${BASE_IMAGE:-jax-xla-redis:20260513-warmup}"
IMAGE="${IMAGE:-${IMAGE_PREFIX}:${RUN_ID}}"
PUSH_IMAGE="${PUSH_IMAGE:-0}"
KIND_CLUSTER="${KIND_CLUSTER:-openfaas}"
SKIP_BUILD="${SKIP_BUILD:-0}"
INSTALL_REDIS="${INSTALL_REDIS:-0}"
SKIP_POPULATE="${SKIP_POPULATE:-0}"
SKIP_DEPLOY="${SKIP_DEPLOY:-0}"
CLEANUP_AT_END="${CLEANUP_AT_END:-0}"

REDIS_ADDR="${REDIS_ADDR:-profile-cache-redis.${FUNCTION_NAMESPACE}.svc.cluster.local:6379}"
REDIS_PASSWORD="${REDIS_PASSWORD:-}"
REDIS_DB="${REDIS_DB:-0}"
REDIS_TIMEOUT="${REDIS_TIMEOUT:-10s}"
ARTIFACT_PREFIX="${ARTIFACT_PREFIX:-python-profile-scale:${FUNCTION_PREFIX}}"

RUN_ROOT="$SCALE_DIR/.runs/$RUN_ID"
RESULT_ROOT="$RUN_ROOT/results"
MANIFEST_ROOT="$RUN_ROOT/k8s"
FIGURE_DIR="$ROOT_DIR/docs/figures"
CSV_PATH="$RESULT_ROOT/large-scale.csv"
SUMMARY_PATH="$RESULT_ROOT/summary.json"
FIGURE_SVG="$FIGURE_DIR/python-openfaas-redis-scale-verification.svg"
FIGURE_JSON="$FIGURE_DIR/python-openfaas-redis-scale-verification-summary.json"

read -r -a BENCHMARK_LIST <<< "$BENCHMARKS"

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "missing required command: $1" >&2
    exit 1
  fi
}

load_openfaas_password() {
  if [[ -n "${OPENFAAS_PASSWORD:-}" ]]; then
    return
  fi
  if kubectl -n openfaas get secret basic-auth >/dev/null 2>&1; then
    OPENFAAS_PASSWORD="$(kubectl -n openfaas get secret basic-auth -o jsonpath='{.data.basic-auth-password}' | base64 --decode)"
    export OPENFAAS_PASSWORD
  fi
}

build_image() {
  echo "== Build Python/OpenFaaS scale image $IMAGE =="
  docker build \
    -f "$PY_PROTO_DIR/Dockerfile.openfaas" \
    --build-arg "BASE_IMAGE=$BASE_IMAGE" \
    --build-arg "BUILD_LABEL=$RUN_ID" \
    -t "$IMAGE" "$PY_PROTO_DIR"

  if [[ "$PUSH_IMAGE" == "1" ]]; then
    docker push "$IMAGE"
  fi
  if [[ -n "$KIND_CLUSTER" ]]; then
    kind load docker-image "$IMAGE" --name "$KIND_CLUSTER"
  fi
}

require_cmd kubectl
require_cmd python3
if [[ "$SKIP_BUILD" != "1" ]]; then
  require_cmd docker
  if [[ -n "$KIND_CLUSTER" ]]; then
    require_cmd kind
  fi
fi

mkdir -p "$RESULT_ROOT" "$MANIFEST_ROOT" "$FIGURE_DIR"
load_openfaas_password

if [[ "$INSTALL_REDIS" == "1" ]]; then
  echo "== Install Redis artifact cache in $FUNCTION_NAMESPACE =="
  kubectl apply -n "$FUNCTION_NAMESPACE" -f "$SCALE_DIR/k8s/redis.yaml"
  kubectl rollout status deployment/profile-cache-redis -n "$FUNCTION_NAMESPACE" --timeout=180s
fi

if [[ "$SKIP_BUILD" == "1" ]]; then
  echo "== Reuse Python/OpenFaaS image $IMAGE =="
else
  build_image
fi

runner_args=(
  --run-id "$RUN_ID"
  --image "$IMAGE"
  --function-prefix "$FUNCTION_PREFIX"
  --namespace "$FUNCTION_NAMESPACE"
  --gateway "$OPENFAAS_GATEWAY"
  --benchmarks "${BENCHMARK_LIST[@]}"
  --shards "$SHARDS"
  --waves "$WAVES"
  --requests-per-pod "$REQUESTS_PER_POD"
  --warmup-requests "$WARMUP_REQUESTS"
  --work-requests "$REQUESTS"
  --profile-requests "$PROFILE_REQUESTS"
  --profile-iters "$PROFILE_ITERS"
  --concurrency "$CONCURRENCY"
  --manifest-dir "$MANIFEST_ROOT"
  --out-dir "$RESULT_ROOT"
  --artifact-prefix "$ARTIFACT_PREFIX"
  --redis-addr "$REDIS_ADDR"
  --redis-password "$REDIS_PASSWORD"
  --redis-db "$REDIS_DB"
  --redis-timeout "$REDIS_TIMEOUT"
  --watchdog-timeout "$WATCHDOG_TIMEOUT"
  --invoke-timeout "$INVOKE_TIMEOUT"
  --ready-timeout "$READY_TIMEOUT"
  --delete-timeout "$DELETE_TIMEOUT"
  --grace-period "$GRACE_PERIOD"
  --username "$OPENFAAS_USERNAME"
  --password "${OPENFAAS_PASSWORD:-}"
)

if [[ "$SKIP_POPULATE" == "1" ]]; then
  runner_args+=(--skip-populate)
fi
if [[ "$SKIP_DEPLOY" == "1" ]]; then
  runner_args+=(--skip-deploy)
fi
if [[ "$CLEANUP_AT_END" == "1" ]]; then
  runner_args+=(--cleanup-at-end)
fi

python3 "$SCALE_DIR/large_scale_runner.py" "${runner_args[@]}"

python3 "$SCALE_DIR/plot_large_scale.py" \
  --csv "$CSV_PATH" \
  --svg "$FIGURE_SVG" \
  --json "$FIGURE_JSON" \
  --warmup-requests "$WARMUP_REQUESTS" \
  --benchmarks "${BENCHMARK_LIST[@]}"

if command -v rsvg-convert >/dev/null 2>&1; then
  rsvg-convert "$FIGURE_SVG" -o "${FIGURE_SVG%.svg}.png"
fi

echo
echo "Done."
echo "  run:       $RUN_ID"
echo "  image:     $IMAGE"
echo "  csv:       $CSV_PATH"
echo "  summary:   $SUMMARY_PATH"
echo "  figure:    $FIGURE_SVG"
echo "  manifests: $MANIFEST_ROOT"
