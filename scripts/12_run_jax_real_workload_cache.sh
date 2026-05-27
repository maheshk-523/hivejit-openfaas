#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
exec bash prototypes/jax-real-workload-cache/run_jax_real_workload_cache.sh "$@"
