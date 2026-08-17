#!/usr/bin/env bash
set -euo pipefail

RUNTIME_DIR="${A2A_RUNTIME_DIR:-/home/dell/.local/a2a-runtime}"
JAVA_HOME="${JAVA_HOME:-$RUNTIME_DIR/jdk}"
NACOS_HOME="${NACOS_HOME:-$RUNTIME_DIR/nacos}"
PID_FILE="$NACOS_HOME/logs/a2a-nacos.pid"
START_OUT="$NACOS_HOME/logs/start.out"

export JAVA_HOME
export PATH="$JAVA_HOME/bin:$PATH"

if [ ! -x "$JAVA_HOME/bin/java" ]; then
  echo "Java not found at $JAVA_HOME/bin/java" >&2
  exit 1
fi

if [ ! -x "$NACOS_HOME/bin/startup.sh" ]; then
  echo "Nacos startup script not found at $NACOS_HOME/bin/startup.sh" >&2
  exit 1
fi

mkdir -p "$NACOS_HOME/logs"

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "Nacos is already running with pid $(cat "$PID_FILE")"
  exit 0
fi

cd "$NACOS_HOME"
setsid "$JAVA_HOME/bin/java" \
  -Xms512m \
  -Xmx512m \
  -Xmn256m \
  -Dnacos.standalone=true \
  -Dnacos.member.list= \
  -Dloader.path="$NACOS_HOME/plugins,$NACOS_HOME/plugins/health,$NACOS_HOME/plugins/cmdb,$NACOS_HOME/plugins/selector" \
  -Dnacos.home="$NACOS_HOME" \
  -jar "$NACOS_HOME/target/nacos-server.jar" \
  --spring.config.additional-location="file:$NACOS_HOME/conf/" \
  --logging.config="$NACOS_HOME/conf/nacos-logback.xml" \
  --server.max-http-header-size=524288 \
  > "$START_OUT" 2>&1 < /dev/null &

echo "$!" > "$PID_FILE"
echo "Nacos is starting with pid $(cat "$PID_FILE"). Logs: $START_OUT"
