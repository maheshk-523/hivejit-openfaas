#!/bin/sh
set -eu

mkdir -p "$(dirname "${PY_SPEC_ARTIFACT_PATH:-/profiles/specialized.py}")"

if [ "${PY_SPEC_MODE:-baseline}" = "saved" ]; then
  echo "python-profile-specialization entrypoint pulling ${PY_SPEC_ARTIFACT_KEY:-python-profile-specialization:artifact:v1}"
  if [ "${PY_SPEC_REQUIRE_ARTIFACT:-1}" = "1" ]; then
    python /app/openfaas_artifact.py pull --require
  else
    python /app/openfaas_artifact.py pull
  fi
else
  python - <<'PY'
import json
import os
from pathlib import Path

path = Path(os.getenv("PY_SPEC_IMPORT_META", "/profiles/python-specialization-import.json"))
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps({
    "imported": False,
    "artifact_found": False,
    "artifact_bytes": 0,
    "artifact_hash": "",
    "redis_key": os.getenv("PY_SPEC_ARTIFACT_KEY", "python-profile-specialization:artifact:v1"),
    "import_ms": 0.0,
}, sort_keys=True) + "\n", encoding="utf-8")
PY
fi

exec fwatchdog
