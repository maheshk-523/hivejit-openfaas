#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
exec ./prototypes/node-openfaas-v8-cache/run_openfaas_v8_cache.sh "$@"
