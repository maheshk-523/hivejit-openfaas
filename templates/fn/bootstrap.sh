#!/usr/bin/env bash
set -euo pipefail

mkdir -p /profiles

pull_profile() {
  local tmp=/profiles/pulled.tmp
  if redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" --raw GET "$ARTIFACT_KEY" > "$tmp" 2>/dev/null; then
    if [ -s "$tmp" ]; then
      mv "$tmp" "$PROFILE_PATH"
      echo "PROFILE_PULL_HIT key=$ARTIFACT_KEY bytes=$(wc -c < "$PROFILE_PATH")"
      return 0
    fi
  fi
  rm -f "$tmp"
  echo "PROFILE_PULL_MISS key=$ARTIFACT_KEY"
  return 1
}

push_profile() {
  if [ -f "$PROFILE_DUMP_PATH" ] && [ -s "$PROFILE_DUMP_PATH" ]; then
    cat "$PROFILE_DUMP_PATH" | redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" -x SET "$ARTIFACT_KEY" >/dev/null
    echo "PROFILE_PUSH_OK key=$ARTIFACT_KEY bytes=$(wc -c < "$PROFILE_DUMP_PATH")"
  else
    echo "PROFILE_PUSH_SKIP reason=no_dump"
  fi
}

term_handler() {
  echo "TERM_HANDLER_START"
  if [ -n "${JAVA_PID:-}" ] && kill -0 "$JAVA_PID" 2>/dev/null; then
    kill -TERM "$JAVA_PID" || true
    wait "$JAVA_PID" || true
  fi
  push_profile
  echo "TERM_HANDLER_DONE"
  exit 0
}

trap term_handler TERM INT

pull_profile || true

unset JAVA_HOME
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
/opt/java/openjdk/bin/java \
  --add-exports java.base/jdk.internal.profilecheckpoint=ALL-UNNAMED \
  -cp /app/function.jar com.example.ServerMain &
JAVA_PID=$!

echo "JAVA_STARTED pid=$JAVA_PID"
wait "$JAVA_PID"
RC=$?
push_profile || true
exit $RC
