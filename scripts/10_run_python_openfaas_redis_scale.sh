#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
exec "$ROOT_DIR/prototypes/python-openfaas-redis-scale/run_large_scale_openfaas_redis_python.sh" "$@"
