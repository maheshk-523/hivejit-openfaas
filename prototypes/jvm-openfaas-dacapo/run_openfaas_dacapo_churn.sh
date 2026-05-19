#!/usr/bin/env bash
set -euo pipefail

PROTO_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$PROTO_DIR/../.." && pwd)"

FUNCTION_NAME="${FUNCTION_NAME:-dacapo-jvm}"
FUNCTION_NAMESPACE="${FUNCTION_NAMESPACE:-openfaas-fn}"
OPENFAAS_NAMESPACE="${OPENFAAS_NAMESPACE:-openfaas}"
OPENFAAS_GATEWAY="${OPENFAAS_GATEWAY:-http://127.0.0.1:8080}"

RUN_ID="${RUN_ID:-$(date +%Y%m%d-%H%M%S)}"
BENCHMARKS="${BENCHMARKS:-h2 lusearch eclipse fop jython}"
SIZE="${SIZE:-small}"
ITERATIONS="${ITERATIONS:-1}"
THREADS="${THREADS:-1}"
INVOCATIONS="${INVOCATIONS:-60}"
SEGMENT_LENGTH="${SEGMENT_LENGTH:-20}"
WATCHDOG_TIMEOUT="${WATCHDOG_TIMEOUT:-300s}"
VALIDATION="${VALIDATION:-none}"
PRE_GC="${PRE_GC:-1}"

IMAGE_PREFIX="${IMAGE_PREFIX:-ttl.sh/${FUNCTION_NAME}-${USER:-user}}"
PUSH_IMAGE="${PUSH_IMAGE:-1}"
KIND_CLUSTER="${KIND_CLUSTER:-}"
DEPLOY_BACKEND="${DEPLOY_BACKEND:-direct}"
BASE_IMAGE="${BASE_IMAGE:-maheshk523/hivejit-openfaas-test:fix13}"

DACAPO_ZIP="${DACAPO_ZIP:-/Users/maheshk/dacapo/dacapo-23.11-MR2-chopin.zip}"
DACAPO_JAR="${DACAPO_JAR:-/Users/maheshk/dacapo/dacapo.jar}"
PAYLOAD_DIR="${PAYLOAD_DIR:-$PROTO_DIR/.cache/dacapo-payload}"
PAYLOAD_BENCHMARKS="${PAYLOAD_BENCHMARKS:-${BENCHMARKS// /,}}"
PAYLOAD_LIB_DIRS="${PAYLOAD_LIB_DIRS:-batik,commons-beanutils,commons-codec,commons-collections,commons-httpclient,commons-lang,commons-logging,daytrader,derby,ezmorph,h2,janino,json,junit,lucene,xerces}"

ARTIFACT_ROOT="$PROTO_DIR/.runs/$RUN_ID"
RESULT_ROOT="$ARTIFACT_ROOT/results"
MANIFEST_ROOT="$ARTIFACT_ROOT/k8s"

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

prepare_payload() {
  local ready=1
  for benchmark in "${BENCHMARK_LIST[@]}"; do
    if [[ ! -d "$PAYLOAD_DIR/dacapo/dat/$benchmark" || ! -d "$PAYLOAD_DIR/dacapo/jar/$benchmark" ]]; then
      ready=0
      break
    fi
  done
  if [[ -f "$PAYLOAD_DIR/dacapo.jar" && "$ready" == "1" ]]; then
    echo "== Reuse DaCapo payload $PAYLOAD_DIR =="
    return
  fi

  echo "== Prepare DaCapo payload $PAYLOAD_DIR =="
  python3 "$PROTO_DIR/prepare_dacapo_payload.py" \
    --dacapo-zip "$DACAPO_ZIP" \
    --dacapo-jar "$DACAPO_JAR" \
    --out "$PAYLOAD_DIR" \
    --benchmarks "$PAYLOAD_BENCHMARKS" \
    --lib-dirs "$PAYLOAD_LIB_DIRS" \
    --force
}

build_image() {
  local image="$1"
  echo "== Build JVM DaCapo/OpenFaaS image $image =="
  DOCKER_BUILDKIT=1 docker build \
    --build-arg "BASE_IMAGE=$BASE_IMAGE" \
    --build-context "dacapo_payload=$PAYLOAD_DIR" \
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

deploy_direct() {
  local image="$1"
  local manifest="$MANIFEST_ROOT/$FUNCTION_NAME.yaml"
  local pre_gc_value
  pre_gc_value="$([[ "$PRE_GC" == "1" ]] && echo true || echo false)"
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
              value: "$RUN_ID"
            - name: DACAPO_SIZE
              value: "$SIZE"
            - name: DACAPO_ITERATIONS
              value: "$ITERATIONS"
            - name: DACAPO_THREADS
              value: "$THREADS"
            - name: DACAPO_VALIDATION
              value: "$VALIDATION"
            - name: DACAPO_ALLOWED_BENCHMARKS
              value: "$PAYLOAD_BENCHMARKS"
            - name: DACAPO_PRE_GC
              value: "$pre_gc_value"
            - name: DACAPO_DIGEST_OUTPUT
              value: "false"
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
            initialDelaySeconds: 5
            periodSeconds: 5
            timeoutSeconds: 3
          readinessProbe:
            httpGet:
              path: /_/health
              port: 8080
            initialDelaySeconds: 5
            periodSeconds: 5
            timeoutSeconds: 3
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

deploy_function() {
  local image="$1"
  echo "== Deploy $FUNCTION_NAME =="
  if [[ "$DEPLOY_BACKEND" == "direct" ]]; then
    deploy_direct "$image"
    return
  fi

  if [[ "$FUNCTION_NAME" != "dacapo-jvm" ]]; then
    echo "FUNCTION_NAME must be dacapo-jvm for faas-cli deploy because stack.yml is static." >&2
    exit 2
  fi
  export DACAPO_JVM_IMAGE="$image" OPENFAAS_GATEWAY WATCHDOG_TIMEOUT SIZE ITERATIONS THREADS VALIDATION
  faas-cli deploy -f "$PROTO_DIR/stack.yml" --gateway "$OPENFAAS_GATEWAY"
  kubectl label "deployment/$FUNCTION_NAME" -n "$FUNCTION_NAMESPACE" \
    com.openfaas.scale.min=1 \
    com.openfaas.scale.max=1 \
    com.openfaas.scale.zero=false \
    --overwrite
  kubectl scale "deployment/$FUNCTION_NAME" -n "$FUNCTION_NAMESPACE" --replicas=1
  kubectl rollout status "deployment/$FUNCTION_NAME" -n "$FUNCTION_NAMESPACE" --timeout=300s
}

measure_benchmark() {
  local benchmark="$1"
  local slug
  slug="$(slugify "$benchmark")"
  local csv="$RESULT_ROOT/$slug.csv"
  local summary="$RESULT_ROOT/$slug.json"
  local svg="$RESULT_ROOT/$slug-warmup.svg"
  local plot_summary="$RESULT_ROOT/$slug-plot-summary.json"

  echo "== Measure $benchmark size=$SIZE invocations=$INVOCATIONS segment_length=$SEGMENT_LENGTH =="
  local pre_gc_arg
  if [[ "$PRE_GC" == "1" ]]; then
    pre_gc_arg="--pre-gc"
  else
    pre_gc_arg="--no-pre-gc"
  fi
  python3 "$PROTO_DIR/run_churn_bench.py" \
    --function "$FUNCTION_NAME" \
    --namespace "$FUNCTION_NAMESPACE" \
    --gateway "$OPENFAAS_GATEWAY" \
    --benchmark "$benchmark" \
    --size "$SIZE" \
    --iterations "$ITERATIONS" \
    --threads "$THREADS" \
    --validation "$VALIDATION" \
    "$pre_gc_arg" \
    --invocations "$INVOCATIONS" \
    --segment-length "$SEGMENT_LENGTH" \
    --csv "$csv" \
    --summary "$summary"

  python3 "$PROTO_DIR/plot_churn.py" \
    --csv "$csv" \
    --svg "$svg" \
    --summary "$plot_summary" \
    --title "DaCapo $benchmark on OpenFaaS with pod churn"
}

require_cmd docker
require_cmd kubectl
require_cmd python3
if [[ "$DEPLOY_BACKEND" != "direct" ]]; then
  require_cmd faas-cli
fi
if [[ -n "$KIND_CLUSTER" ]]; then
  require_cmd kind
fi

mkdir -p "$RESULT_ROOT"
prepare_payload

if [[ "$DEPLOY_BACKEND" != "direct" ]]; then
  login_openfaas
fi

IMAGE="${IMAGE_PREFIX}:${RUN_ID}"
build_image "$IMAGE"
deploy_function "$IMAGE"

for benchmark in "${BENCHMARK_LIST[@]}"; do
  measure_benchmark "$benchmark"
done

echo
echo "Done."
echo "  run:      $RUN_ID"
echo "  image:    $IMAGE"
echo "  results:  $RESULT_ROOT"
