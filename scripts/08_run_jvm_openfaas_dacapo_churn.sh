#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
exec "$ROOT_DIR/prototypes/jvm-openfaas-dacapo/run_openfaas_dacapo_churn.sh" "$@"
