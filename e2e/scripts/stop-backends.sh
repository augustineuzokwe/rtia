#!/usr/bin/env bash
# Tear down the four fake backends started by start-backends.sh.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_DIR="${HERE}/.pids"

if [[ ! -d "${PID_DIR}" ]]; then
  echo "[stop-backends] no pid dir (${PID_DIR}); nothing to stop."
  exit 0
fi

shopt -s nullglob
for pidfile in "${PID_DIR}"/*.pid; do
  scenario="$(basename "${pidfile}" .pid)"
  pid="$(cat "${pidfile}")"
  if kill -0 "${pid}" 2>/dev/null; then
    echo "[stop-backends] stopping ${scenario} (pid ${pid})…"
    kill "${pid}" 2>/dev/null || true
  fi
  rm -f "${pidfile}"
done

echo "[stop-backends] done."
