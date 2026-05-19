#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
exec "$ROOT_DIR/prototypes/jax-openfaas-redis-xla/run_openfaas_redis_jax_xla.sh" "$@"
