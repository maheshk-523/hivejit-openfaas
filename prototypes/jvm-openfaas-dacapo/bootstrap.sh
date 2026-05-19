#!/bin/sh
set -eu

JAVA_BIN="${JAVA_BIN:-/opt/george-jdk/bin/java}"
if [ ! -x "$JAVA_BIN" ]; then
  JAVA_BIN="$(command -v java)"
fi

JAVA_HEAP_OPTS="${JAVA_HEAP_OPTS:-}"
JAVA_EXTRA_OPTS="${JAVA_EXTRA_OPTS:-}"

exec "$JAVA_BIN" \
  -Djava.awt.headless=true \
  -Djava.security.manager=allow \
  --add-exports java.base/jdk.internal.ref=ALL-UNNAMED \
  --add-exports java.base/jdk.internal.misc=ALL-UNNAMED \
  --add-exports java.base/sun.nio.ch=ALL-UNNAMED \
  --add-exports java.management.rmi/com.sun.jmx.remote.internal.rmi=ALL-UNNAMED \
  --add-exports java.rmi/sun.rmi.registry=ALL-UNNAMED \
  --add-exports java.rmi/sun.rmi.server=ALL-UNNAMED \
  --add-exports java.sql/java.sql=ALL-UNNAMED \
  --add-exports java.base/jdk.internal.math=ALL-UNNAMED \
  --add-exports java.base/jdk.internal.module=ALL-UNNAMED \
  --add-exports java.base/jdk.internal.util.jar=ALL-UNNAMED \
  --add-exports jdk.management/com.sun.management.internal=ALL-UNNAMED \
  --add-opens java.base/java.lang=ALL-UNNAMED \
  --add-opens java.base/java.lang.module=ALL-UNNAMED \
  --add-opens java.base/java.net=ALL-UNNAMED \
  --add-opens java.base/jdk.internal.loader=ALL-UNNAMED \
  --add-opens java.base/jdk.internal.ref=ALL-UNNAMED \
  --add-opens java.base/jdk.internal.reflect=ALL-UNNAMED \
  --add-opens java.base/java.io=ALL-UNNAMED \
  --add-opens java.base/sun.nio.ch=ALL-UNNAMED \
  --add-opens java.base/java.util=ALL-UNNAMED \
  --add-opens java.base/java.util.concurrent=ALL-UNNAMED \
  --add-opens java.base/java.util.concurrent.atomic=ALL-UNNAMED \
  --add-opens java.base/java.nio=ALL-UNNAMED \
  $JAVA_HEAP_OPTS \
  $JAVA_EXTRA_OPTS \
  -cp /app/function.jar:/app/lib/dacapo.jar \
  S
