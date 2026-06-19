#!/usr/bin/env bash
# Boot the four deterministic fake-LLM backends, one per scenario, one per
# port. RTIA_FAKE_SCENARIO is process-wide (re-read per invoke), so each
# scenario MUST be its own process. The token is PINNED so the Playwright
# auth fixture can seed it via ?token= without scraping any backend's stdout.
#
# Each backend serves ui-react/dist at / and gates /pipeline* on the bearer
# token. Run scripts/stop-backends.sh to tear them all down.
#
# Usage:
#   bash e2e/scripts/start-backends.sh
#   (or: pnpm --filter e2e backends:start)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${HERE}/../.." && pwd)"
PID_DIR="${HERE}/.pids"
LOG_DIR="${HERE}/.logs"
mkdir -p "${PID_DIR}" "${LOG_DIR}"

# Must match e2e/fixtures/constants.ts:PINNED_TOKEN.
PINNED_TOKEN="e2e-pinned-token"

# scenario:port pairs — must match e2e/fixtures/constants.ts:BACKENDS.
BACKENDS=(
  "deep_clean:8001"
  "deep_with_po:8002"
  "split:8003"
  "error:8004"
)

cd "${REPO_ROOT}"

for entry in "${BACKENDS[@]}"; do
  scenario="${entry%%:*}"
  port="${entry##*:}"
  pidfile="${PID_DIR}/${scenario}.pid"
  logfile="${LOG_DIR}/${scenario}.log"

  if [[ -f "${pidfile}" ]] && kill -0 "$(cat "${pidfile}")" 2>/dev/null; then
    echo "[start-backends] ${scenario} already running (pid $(cat "${pidfile}"), port ${port})"
    continue
  fi

  echo "[start-backends] starting ${scenario} on port ${port}…"
  RTIA_LLM_PROVIDER=fake \
  RTIA_FAKE_SCENARIO="${scenario}" \
  RTIA_API_PORT="${port}" \
  RTIA_API_HOST="127.0.0.1" \
  RTIA_API_TOKEN="${PINNED_TOKEN}" \
    uv run python scripts/run_api.py >"${logfile}" 2>&1 &
  echo $! >"${pidfile}"
done

echo
echo "[start-backends] all backends launching. Tail logs in: ${LOG_DIR}"
echo "[start-backends] pinned token: ${PINNED_TOKEN}"
echo "[start-backends] stop with: bash e2e/scripts/stop-backends.sh"
