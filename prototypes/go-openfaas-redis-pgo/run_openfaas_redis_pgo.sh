#!/usr/bin/env bash
set -euo pipefail

PROTO_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$PROTO_DIR/../.." && pwd)"

FUNCTION_NAME="go-pgo-redis"
FUNCTION_NAMESPACE="${FUNCTION_NAMESPACE:-openfaas-fn}"
OPENFAAS_NAMESPACE="${OPENFAAS_NAMESPACE:-openfaas}"
OPENFAAS_GATEWAY="${OPENFAAS_GATEWAY:-http://127.0.0.1:8080}"

RUN_ID="${RUN_ID:-$(date +%Y%m%d-%H%M%S)}"
PROFILE_ITERS="${PROFILE_ITERS:-5 10}"
PROFILE_SECONDS="${PROFILE_SECONDS:-20}"
PROFILE_LOAD_REQUESTS="${PROFILE_LOAD_REQUESTS:-120}"
PROFILE_CONCURRENCY="${PROFILE_CONCURRENCY:-4}"
MEASURE_REQUESTS="${MEASURE_REQUESTS:-80}"
MEASURE_WARMUP="${MEASURE_WARMUP:-10}"
MEASURE_CONCURRENCY="${MEASURE_CONCURRENCY:-1}"
HANDLER_REQUESTS="${HANDLER_REQUESTS:-350000}"
BENCHMARKS="${BENCHMARKS:-router}"
RUN_POD_CHURN="${RUN_POD_CHURN:-0}"
CHURN_INVOCATIONS="${CHURN_INVOCATIONS:-40}"
CHURN_SEGMENT_LENGTH="${CHURN_SEGMENT_LENGTH:-8}"
CHURN_AT="${CHURN_AT:-}"
CHURN_POST_READY_DELAY="${CHURN_POST_READY_DELAY:-0}"
CHURN_INVOKE_TIMEOUT="${CHURN_INVOKE_TIMEOUT:-120}"

IMAGE_PREFIX="${IMAGE_PREFIX:-ttl.sh/${FUNCTION_NAME}-${USER:-user}}"
PUSH_IMAGE="${PUSH_IMAGE:-1}"
KIND_CLUSTER="${KIND_CLUSTER:-}"
INSTALL_REDIS="${INSTALL_REDIS:-0}"
OF_WATCHDOG_VERSION="${OF_WATCHDOG_VERSION:-0.9.16}"

REDIS_ADDR="${REDIS_ADDR:-profile-cache-redis.${FUNCTION_NAMESPACE}.svc.cluster.local:6379}"
REDIS_PASSWORD="${REDIS_PASSWORD:-}"
REDIS_DB="${REDIS_DB:-0}"
REDIS_TIMEOUT="${REDIS_TIMEOUT:-10s}"
PROFILE_KEY_PREFIX="${PROFILE_KEY_PREFIX:-go-pgo:${FUNCTION_NAME}}"
GOMAXPROCS="${GOMAXPROCS:-1}"

ARTIFACT_ROOT="$PROTO_DIR/.runs/$RUN_ID"
PROFILE_ROOT_BASE="$ARTIFACT_ROOT/profiles"
RESULT_ROOT_BASE="$ARTIFACT_ROOT/results"
SYMBOL_BIN="$ARTIFACT_ROOT/handler.nopgo.symbols"
URL="$OPENFAAS_GATEWAY/function/$FUNCTION_NAME"

read -r -a BENCHMARK_LIST <<< "$BENCHMARKS"
if (( ${#BENCHMARK_LIST[@]} == 0 )); then
  echo "BENCHMARKS must contain at least one benchmark" >&2
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

build_push_deploy() {
  local tag="$1"
  local pgo_profile="$2"
  local label="$3"
  local benchmark="$4"
  local image="${IMAGE_PREFIX}:${tag}"

  echo "== Build image $image =="
  if [[ -n "$pgo_profile" ]]; then
    docker build \
      --build-arg "PGO_PROFILE=$pgo_profile" \
      --build-arg "BUILD_LABEL=$label" \
      --build-arg "OF_WATCHDOG_VERSION=$OF_WATCHDOG_VERSION" \
      -t "$image" "$PROTO_DIR"
  else
    docker build \
      --build-arg "BUILD_LABEL=$label" \
      --build-arg "OF_WATCHDOG_VERSION=$OF_WATCHDOG_VERSION" \
      -t "$image" "$PROTO_DIR"
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
  local manifest="$ARTIFACT_ROOT/${FUNCTION_NAME}-${label}.json"
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
            "terminationGracePeriodSeconds": 20,
            "containers": [
              {
                "name": "$FUNCTION_NAME",
                "image": "$image",
                "imagePullPolicy": "IfNotPresent",
                "ports": [{"name": "http", "containerPort": 8080}],
                "env": [
                  {"name": "REDIS_ADDR", "value": "$REDIS_ADDR"},
                  {"name": "REDIS_PASSWORD", "value": "$REDIS_PASSWORD"},
                  {"name": "REDIS_DB", "value": "$REDIS_DB"},
                  {"name": "REDIS_TIMEOUT", "value": "$REDIS_TIMEOUT"},
                  {"name": "PROFILE_KEY_PREFIX", "value": "$PROFILE_KEY_PREFIX"},
                  {"name": "BUILD_LABEL", "value": "$label"},
                  {"name": "BENCHMARK", "value": "$benchmark"},
                  {"name": "GOMAXPROCS", "value": "$GOMAXPROCS"},
                  {"name": "read_timeout", "value": "300s"},
                  {"name": "write_timeout", "value": "300s"},
                  {"name": "exec_timeout", "value": "300s"}
                ],
                "readinessProbe": {
                  "httpGet": {"path": "/healthz", "port": 8080},
                  "initialDelaySeconds": 2,
                  "periodSeconds": 3,
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

measure() {
  local benchmark="$1"
  local label="$2"
  local out_prefix="$3"
  local result_root="$4"

  python3 "$ROOT_DIR/scripts/http_invoke_latency.py" \
    --url "$URL/work" \
    --method POST \
    --header "Content-Type: application/json" \
    --body "{\"benchmark\":\"$benchmark\",\"requests\":$HANDLER_REQUESTS}" \
    --requests "$MEASURE_REQUESTS" \
    --warmup "$MEASURE_WARMUP" \
    --concurrency "$MEASURE_CONCURRENCY" \
    --timeout 120 \
    --label "$label" \
    --csv "$result_root/${out_prefix}.csv" \
    --summary "$result_root/${out_prefix}.json" \
    --svg "$result_root/${out_prefix}.svg"
}

run_pod_churn() {
  local benchmark="$1"
  local treatment="$2"
  local profile_iters="$3"
  local out_prefix="$4"
  local result_root="$5"
  local password="${OPENFAAS_PASSWORD:-}"
  local username="${OPENFAAS_USERNAME:-admin}"
  local auth_args=()

  if [[ -z "$password" ]] && kubectl -n "$OPENFAAS_NAMESPACE" get secret basic-auth >/dev/null 2>&1; then
    password="$(kubectl -n "$OPENFAAS_NAMESPACE" get secret basic-auth -o jsonpath='{.data.basic-auth-password}' | base64 --decode)"
    username="admin"
  fi
  if [[ -n "$password" ]]; then
    auth_args+=(--username "$username" --password "$password")
  fi

  echo "== Run pod-churn trace $treatment ($benchmark) =="
  python3 "$PROTO_DIR/run_churn_bench.py" \
    --function "$FUNCTION_NAME" \
    --namespace "$FUNCTION_NAMESPACE" \
    --gateway "$OPENFAAS_GATEWAY" \
    --benchmark "$benchmark" \
    --treatment "$treatment" \
    --profile-iters "$profile_iters" \
    --requests "$HANDLER_REQUESTS" \
    --invocations "$CHURN_INVOCATIONS" \
    --segment-length "$CHURN_SEGMENT_LENGTH" \
    --churn-at "$CHURN_AT" \
    --post-ready-delay "$CHURN_POST_READY_DELAY" \
    --invoke-timeout "$CHURN_INVOKE_TIMEOUT" \
    "${auth_args[@]}" \
    --csv "$result_root/${out_prefix}-pod-churn.csv" \
    --summary "$result_root/${out_prefix}-pod-churn.json"
}

capture_profile() {
  local benchmark="$1"
  local benchmark_slug="$2"
  local iter="$3"
  local profile_root="$4"
  local raw_key="${PROFILE_KEY_PREFIX}:raw:${RUN_ID}:${benchmark_slug}:${iter}"
  local dir="$profile_root/raw"
  mkdir -p "$dir"

  echo "== Capture warm profile $iter to Redis key $raw_key =="
  curl -fsS "$URL/profile/capture?seconds=$PROFILE_SECONDS&key=$raw_key" > "$dir/capture-${iter}.json" &
  local capture_pid=$!

  sleep 1
  python3 "$ROOT_DIR/scripts/http_invoke_latency.py" \
    --url "$URL/work" \
    --method POST \
    --header "Content-Type: application/json" \
    --body "{\"benchmark\":\"$benchmark\",\"requests\":$HANDLER_REQUESTS}" \
    --requests "$PROFILE_LOAD_REQUESTS" \
    --warmup 0 \
    --concurrency "$PROFILE_CONCURRENCY" \
    --timeout 120 \
    --label "profile-load-$iter" \
    --csv "$dir/load-${iter}.csv" \
    --summary "$dir/load-${iter}.json" \
    --svg "$dir/load-${iter}.svg" >/dev/null

  wait "$capture_pid"
  curl -fsS "$URL/profile/fetch?key=$raw_key" -o "$dir/invoke-${iter}.pprof"
}

store_merged_profile() {
  local profile="$1"
  local key="$2"
  curl -fsS -X POST --data-binary "@$profile" "$URL/profile/put?key=$key" > "$profile.store.json"
}

require_cmd docker
require_cmd faas-cli
require_cmd kubectl
require_cmd curl
require_cmd go
require_cmd python3
if [[ -n "$KIND_CLUSTER" ]]; then
  require_cmd kind
fi

mkdir -p "$PROFILE_ROOT_BASE" "$RESULT_ROOT_BASE"

echo "== Build local no-PGO symbol binary for profile merges =="
(cd "$PROTO_DIR" && go build -buildvcs=false -trimpath -pgo=off -o "$SYMBOL_BIN" .)

if [[ "$INSTALL_REDIS" == "1" ]]; then
  echo "== Install Redis profile cache in namespace $FUNCTION_NAMESPACE =="
  kubectl apply -n "$FUNCTION_NAMESPACE" -f "$PROTO_DIR/k8s/redis.yaml"
  kubectl rollout status deployment/profile-cache-redis -n "$FUNCTION_NAMESPACE" --timeout=180s
fi

login_openfaas

max_iter=0
for iter_count in $PROFILE_ITERS; do
  if (( iter_count > max_iter )); then
    max_iter="$iter_count"
  fi
done

for benchmark in "${BENCHMARK_LIST[@]}"; do
  benchmark_slug="$(slugify "$benchmark")"
  profile_root="$PROFILE_ROOT_BASE"
  result_root="$RESULT_ROOT_BASE"
  if (( ${#BENCHMARK_LIST[@]} > 1 )); then
    profile_root="$PROFILE_ROOT_BASE/$benchmark_slug"
    result_root="$RESULT_ROOT_BASE/$benchmark_slug"
  fi
  mkdir -p "$profile_root" "$result_root"

  build_push_deploy "${RUN_ID}-${benchmark_slug}-nopgo" "" "nopgo-${benchmark_slug}" "$benchmark"

  echo "== Check Redis connectivity from function ($benchmark) =="
  curl -fsS "$URL/profile/ping" | tee "$result_root/redis-ping.json"
  echo

  echo "== Measure baseline ($benchmark) =="
  measure "$benchmark" "go-openfaas-nopgo" "go-openfaas-nopgo" "$result_root"
  if [[ "$RUN_POD_CHURN" == "1" ]]; then
    run_pod_churn "$benchmark" "nopgo" "0" "go-openfaas-nopgo" "$result_root"
  fi

  echo "== Capture $max_iter warm profiles from baseline ($benchmark) =="
  for iter in $(seq 1 "$max_iter"); do
    capture_profile "$benchmark" "$benchmark_slug" "$iter" "$profile_root"
  done

  for iter_count in $PROFILE_ITERS; do
    profile_dir="$profile_root/${iter_count}-profiles"
    mkdir -p "$profile_dir"
    for iter in $(seq 1 "$iter_count"); do
      cp "$profile_root/raw/invoke-${iter}.pprof" "$profile_dir/"
    done

    echo "== Merge $iter_count Redis-backed profiles ($benchmark) =="
    HOME="${PPROF_HOME:-/private/tmp/go-pprof-home}" \
      PPROF_TMPDIR="${PPROF_TMPDIR:-/private/tmp/go-pprof-tmp}" \
      go tool pprof -symbolize=none -proto -output="$profile_dir/merged.pprof" \
        "$SYMBOL_BIN" "$profile_dir"/invoke-*.pprof
    merged_key="${PROFILE_KEY_PREFIX}:merged:${RUN_ID}:${benchmark_slug}:${iter_count}"
    store_merged_profile "$profile_dir/merged.pprof" "$merged_key"

    rel_profile="${profile_dir#$PROTO_DIR/}/merged.pprof"
    build_push_deploy "${RUN_ID}-${benchmark_slug}-pgo-${iter_count}" "$rel_profile" "pgo-${benchmark_slug}-${iter_count}" "$benchmark"

    echo "== Measure PGO build from $iter_count warm profiles ($benchmark) =="
    measure "$benchmark" "go-openfaas-pgo-${iter_count}" "go-openfaas-pgo-${iter_count}" "$result_root"
    if [[ "$RUN_POD_CHURN" == "1" ]]; then
      run_pod_churn "$benchmark" "pgo-${iter_count}" "$iter_count" "go-openfaas-pgo-${iter_count}" "$result_root"
    fi
  done
done

echo
echo "Done."
echo "  run:      $RUN_ID"
echo "  results:  $RESULT_ROOT_BASE"
echo "  profiles: $PROFILE_ROOT_BASE"
