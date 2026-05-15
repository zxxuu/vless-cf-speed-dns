#!/usr/bin/env sh
set -eu

cd /app

run_once() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting speed DNS run"
  python /app/main.py
}

interval="${RUN_INTERVAL_SECONDS:-0}"

run_once || {
  status="$?"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Run failed with status $status"
  if [ "${RUN_CONTINUE_ON_FAILURE:-true}" != "true" ] || [ "$interval" -le 0 ]; then
    exit "$status"
  fi
}

if [ "$interval" -le 0 ]; then
  exit 0
fi

while true; do
  sleep "$interval"
  run_once || {
    status="$?"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Run failed with status $status"
    if [ "${RUN_CONTINUE_ON_FAILURE:-true}" != "true" ]; then
      exit "$status"
    fi
  }
done
