ARG OF_WATCHDOG_VERSION=0.9.16

FROM ghcr.io/openfaas/of-watchdog:${OF_WATCHDOG_VERSION} AS watchdog

FROM python:3.11-slim

ARG JAX_PACKAGE="jax[cpu]"
ARG BUILD_LABEL=baseline

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN python -m pip install --upgrade pip && \
    python -m pip install "${JAX_PACKAGE}"

COPY --from=watchdog /fwatchdog /usr/bin/fwatchdog
WORKDIR /app
COPY cachectl.py handler.py jax_workload.py entrypoint.sh /app/
RUN chmod +x /app/entrypoint.sh

ENV mode="http" \
    upstream_url="http://127.0.0.1:8082" \
    http_port="8080" \
    fprocess="python /app/handler.py" \
    HANDLER_ADDR=":8082" \
    BUILD_LABEL="${BUILD_LABEL}" \
    JAX_CACHE_MODE="baseline" \
    JAX_CACHE_DIR="/profiles/jax-cache" \
    JAX_CACHE_IMPORT_META="/profiles/jax-cache-import.json" \
    JAX_CACHE_EXPORT_META="/profiles/jax-cache-export.json" \
    JAX_PLATFORMS="cpu" \
    EXECUTIONS="3"

HEALTHCHECK --interval=5s CMD [ -e /tmp/.lock ] || exit 1
CMD ["/app/entrypoint.sh"]
