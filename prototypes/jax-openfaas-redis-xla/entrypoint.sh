#!/usr/bin/env sh
set -eu

mkdir -p "${JAX_CACHE_DIR:-/profiles/jax-cache}"

echo "jax-openfaas-redis-xla entrypoint mode=${JAX_CACHE_MODE:-baseline} key=${JAX_CACHE_KEY:-jax-xla-cache:default}"
python /app/cachectl.py pull

exec /usr/bin/fwatchdog
