#!/usr/bin/env bash
set -euo pipefail

PROTO_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$PROTO_DIR/../.." && pwd)"

FUNCTION_NAME="${FUNCTION_NAME:-python-profile-specialization}"
FUNCTION_NAMESPACE="${FUNCTION_NAMESPACE:-openfaas-fn}"
OPENFAAS_GATEWAY="${OPENFAAS_GATEWAY:-http://127.0.0.1:8080}"
OPENFAAS_USERNAME="${OPENFAAS_USERNAME:-admin}"

RUN_ID="${RUN_ID:-$(date +%Y%m%d-%H%M%S)}"
BENCHMARKS="${BENCHMARKS:-dacapo-lusearch dacapo-h2 dacapo-eclipse}"
REQUESTS="${REQUESTS:-12000}"
PROFILE_REQUESTS="${PROFILE_REQUESTS:-36000}"
PROFILE_ITERS="${PROFILE_ITERS:-3}"
PODS="${PODS:-3}"
REQUESTS_PER_POD="${REQUESTS_PER_POD:-10}"
WARMUP_REQUESTS="${WARMUP_REQUESTS:-4}"
WATCHDOG_TIMEOUT="${WATCHDOG_TIMEOUT:-300s}"

IMAGE_PREFIX="${IMAGE_PREFIX:-${FUNCTION_NAME}}"
BASE_IMAGE="${BASE_IMAGE:-jax-xla-redis:20260513-warmup}"
IMAGE="${IMAGE:-${IMAGE_PREFIX}:${RUN_ID}}"
SKIP_BUILD="${SKIP_BUILD:-0}"
KIND_CLUSTER="${KIND_CLUSTER:-openfaas}"
PUSH_IMAGE="${PUSH_IMAGE:-0}"
DEPLOY_BACKEND="${DEPLOY_BACKEND:-direct}"
INSTALL_REDIS="${INSTALL_REDIS:-0}"

REDIS_ADDR="${REDIS_ADDR:-profile-cache-redis.${FUNCTION_NAMESPACE}.svc.cluster.local:6379}"
REDIS_PASSWORD="${REDIS_PASSWORD:-}"
REDIS_DB="${REDIS_DB:-0}"
REDIS_TIMEOUT="${REDIS_TIMEOUT:-10s}"
PY_SPEC_ARTIFACT_PATH="${PY_SPEC_ARTIFACT_PATH:-/profiles/specialized.py}"
PY_SPEC_IMPORT_META="${PY_SPEC_IMPORT_META:-/profiles/python-specialization-import.json}"

ARTIFACT_ROOT="$PROTO_DIR/.runs/$RUN_ID"
RESULT_ROOT_BASE="$ARTIFACT_ROOT/results"
MANIFEST_ROOT="$ARTIFACT_ROOT/k8s"
FIGURE_DIR="$ROOT_DIR/docs/figures"
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
  local image="$1"
  echo "== Build Python/OpenFaaS image $image =="
  docker build \
    -f "$PROTO_DIR/Dockerfile.openfaas" \
    --build-arg "BASE_IMAGE=$BASE_IMAGE" \
    --build-arg "BUILD_LABEL=$RUN_ID" \
    -t "$image" "$PROTO_DIR"

  if [[ "$PUSH_IMAGE" == "1" ]]; then
    docker push "$image"
  fi
  if [[ -n "$KIND_CLUSTER" ]]; then
    kind load docker-image "$image" --name "$KIND_CLUSTER"
  fi
}

deploy_direct() {
  local image="$1"
  local mode="$2"
  local label="$3"
  local benchmark="$4"
  local artifact_key="$5"
  local require_artifact="$6"
  local manifest="$MANIFEST_ROOT/$FUNCTION_NAME-$mode-$benchmark.yaml"

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
      volumes:
        - name: python-profile-artifact
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
            - name: BENCHMARK
              value: "$benchmark"
            - name: REQUESTS
              value: "$REQUESTS"
            - name: PROFILE_REQUESTS
              value: "$PROFILE_REQUESTS"
            - name: PROFILE_ITERS
              value: "$PROFILE_ITERS"
            - name: PY_SPEC_MODE
              value: "$mode"
            - name: PY_SPEC_ARTIFACT_KEY
              value: "$artifact_key"
            - name: PY_SPEC_ARTIFACT_PATH
              value: "$PY_SPEC_ARTIFACT_PATH"
            - name: PY_SPEC_IMPORT_META
              value: "$PY_SPEC_IMPORT_META"
            - name: PY_SPEC_REQUIRE_ARTIFACT
              value: "$require_artifact"
            - name: REDIS_ADDR
              value: "$REDIS_ADDR"
            - name: REDIS_PASSWORD
              value: "$REDIS_PASSWORD"
            - name: REDIS_DB
              value: "$REDIS_DB"
            - name: REDIS_TIMEOUT
              value: "$REDIS_TIMEOUT"
            - name: read_timeout
              value: "$WATCHDOG_TIMEOUT"
            - name: write_timeout
              value: "$WATCHDOG_TIMEOUT"
            - name: exec_timeout
              value: "$WATCHDOG_TIMEOUT"
          readinessProbe:
            httpGet:
              path: /_/health
              port: 8080
            initialDelaySeconds: 2
            periodSeconds: 3
            timeoutSeconds: 2
          livenessProbe:
            httpGet:
              path: /_/health
              port: 8080
            initialDelaySeconds: 2
            periodSeconds: 5
            timeoutSeconds: 2
          volumeMounts:
            - name: python-profile-artifact
              mountPath: /profiles
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
      targetPort: http
YAML

  kubectl apply -f "$manifest"
  kubectl rollout status "deployment/$FUNCTION_NAME" -n "$FUNCTION_NAMESPACE" --timeout=300s
}

deploy_mode() {
  local image="$1"
  local mode="$2"
  local label="$3"
  local benchmark="$4"
  local artifact_key="$5"
  local require_artifact="$6"

  echo "== Deploy $FUNCTION_NAME mode=$mode benchmark=$benchmark =="
  if [[ "$DEPLOY_BACKEND" == "direct" ]]; then
    deploy_direct "$image" "$mode" "$label" "$benchmark" "$artifact_key" "$require_artifact"
  else
    export OPENFAAS_GATEWAY PY_SPEC_IMAGE="$image" BUILD_LABEL="$label" BENCHMARK="$benchmark"
    export REQUESTS PROFILE_REQUESTS PROFILE_ITERS WATCHDOG_TIMEOUT
    export PY_SPEC_MODE="$mode" PY_SPEC_ARTIFACT_KEY="$artifact_key" PY_SPEC_ARTIFACT_PATH PY_SPEC_IMPORT_META
    export PY_SPEC_REQUIRE_ARTIFACT="$require_artifact" REDIS_ADDR REDIS_PASSWORD REDIS_DB REDIS_TIMEOUT
    faas-cli deploy -f "$PROTO_DIR/stack.yml" --gateway "$OPENFAAS_GATEWAY"
    kubectl patch "deployment/$FUNCTION_NAME" -n "$FUNCTION_NAMESPACE" --patch-file "$PROTO_DIR/k8s/function-patch.yaml"
    kubectl rollout status "deployment/$FUNCTION_NAME" -n "$FUNCTION_NAMESPACE" --timeout=300s
  fi
}

populate_artifact() {
  local benchmark="$1"
  local result_root="$2"
  local auth_args=()
  if [[ -n "${OPENFAAS_PASSWORD:-}" ]]; then
    auth_args=(-u "$OPENFAAS_USERNAME:$OPENFAAS_PASSWORD")
  fi
  echo "== Check Redis connectivity from function ($benchmark) =="
  curl -fsS "${auth_args[@]}" "$URL/profile/ping" > "$result_root/redis-ping.json"
  echo
  echo "== Populate Python specialization artifact in Redis ($benchmark) =="
  curl -fsS \
    "${auth_args[@]}" \
    -X POST \
    -H "Content-Type: application/json" \
    --data "{\"benchmark\":\"$benchmark\",\"profile_iters\":$PROFILE_ITERS,\"profile_requests\":$PROFILE_REQUESTS}" \
    "$URL/profile/populate" > "$result_root/populate.json"
}

measure_lifecycle() {
  local benchmark="$1"
  local label="$2"
  local out="$3"
  python3 "$PROTO_DIR/measure_openfaas_lifecycle.py" \
    --function "$FUNCTION_NAME" \
    --namespace "$FUNCTION_NAMESPACE" \
    --gateway "$OPENFAAS_GATEWAY" \
    --benchmark "$benchmark" \
    --requests "$REQUESTS" \
    --pods "$PODS" \
    --requests-per-pod "$REQUESTS_PER_POD" \
    --warmup-requests "$WARMUP_REQUESTS" \
    --label "$label" \
    --username "$OPENFAAS_USERNAME" \
    --password "${OPENFAAS_PASSWORD:-}" \
    --csv "$out"
}

require_cmd docker
require_cmd kubectl
require_cmd curl
require_cmd python3
if [[ "$DEPLOY_BACKEND" != "direct" ]]; then
  require_cmd faas-cli
fi
if [[ -n "$KIND_CLUSTER" ]]; then
  require_cmd kind
fi

mkdir -p "$RESULT_ROOT_BASE" "$FIGURE_DIR"
load_openfaas_password

if [[ "$INSTALL_REDIS" == "1" ]]; then
  kubectl apply -n "$FUNCTION_NAMESPACE" -f "$PROTO_DIR/k8s/redis.yaml"
  kubectl rollout status deployment/profile-cache-redis -n "$FUNCTION_NAMESPACE" --timeout=180s
fi

if [[ "$SKIP_BUILD" == "1" ]]; then
  echo "== Reuse Python/OpenFaaS image $IMAGE =="
else
  build_image "$IMAGE"
fi

for benchmark in "${BENCHMARK_LIST[@]}"; do
  slug="$(slugify "$benchmark")"
  result_root="$RESULT_ROOT_BASE/$slug"
  artifact_key="python-profile-specialization:${FUNCTION_NAME}:${RUN_ID}:${slug}:artifact:v1"
  mkdir -p "$result_root"

  deploy_mode "$IMAGE" "populate" "populate-$slug" "$benchmark" "$artifact_key" "0"
  populate_artifact "$benchmark" "$result_root"

  deploy_mode "$IMAGE" "baseline" "baseline-$slug" "$benchmark" "$artifact_key" "0"
  measure_lifecycle "$benchmark" "python-generic" "$result_root/python-generic-lifecycle.csv"

  deploy_mode "$IMAGE" "saved" "saved-$slug" "$benchmark" "$artifact_key" "1"
  measure_lifecycle "$benchmark" "python-specialized-3" "$result_root/python-specialized-3-lifecycle.csv"

  python3 "$PROTO_DIR/plot_lifecycle.py" \
    --input "python-generic=$result_root/python-generic-lifecycle.csv" \
    --input "python-specialized-3=$result_root/python-specialized-3-lifecycle.csv" \
    --title "OpenFaaS Python ${benchmark#dacapo-} pod lifecycle" \
    --subtitle "Real OpenFaaS pod restarts; saved Redis specialization artifact versus no saved state" \
    --svg "$FIGURE_DIR/python-openfaas-profile-specialization-lifecycle-$slug.svg"
  if command -v rsvg-convert >/dev/null 2>&1; then
    rsvg-convert "$FIGURE_DIR/python-openfaas-profile-specialization-lifecycle-$slug.svg" \
      -o "$FIGURE_DIR/python-openfaas-profile-specialization-lifecycle-$slug.png"
  fi

  python3 "$PROTO_DIR/plot_lifecycle.py" \
    --input "python-generic=$result_root/python-generic-lifecycle.csv" \
    --input "python-specialized-3=$result_root/python-specialized-3-lifecycle.csv" \
    --aggregate-by-request \
    --title "OpenFaaS Python ${benchmark#dacapo-} median pod lifecycle" \
    --subtitle "Median gateway latency at each request position across repeated real pod restarts" \
    --svg "$FIGURE_DIR/python-openfaas-profile-specialization-lifecycle-median-$slug.svg"
  if command -v rsvg-convert >/dev/null 2>&1; then
    rsvg-convert "$FIGURE_DIR/python-openfaas-profile-specialization-lifecycle-median-$slug.svg" \
      -o "$FIGURE_DIR/python-openfaas-profile-specialization-lifecycle-median-$slug.png"
  fi
done

echo
echo "Done."
echo "  run:     $RUN_ID"
echo "  image:   $IMAGE"
echo "  results: $RESULT_ROOT_BASE"
echo "  figures: $FIGURE_DIR/python-openfaas-profile-specialization-lifecycle-*.png"
