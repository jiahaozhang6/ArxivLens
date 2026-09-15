#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKEND_ROOT="$PROJECT_ROOT/backend"
FRONTEND_ROOT="$PROJECT_ROOT/frontend"
PYTHON="$BACKEND_ROOT/.venv/bin/python"
FRONTEND_HOST="${FRONTEND_HOST:-0.0.0.0}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

if [[ ! -x "$PYTHON" ]]; then
  echo "Dependencies are missing. Run ./scripts/setup.sh first." >&2
  exit 1
fi

mkdir -p "$PROJECT_ROOT/logs"
(
  cd "$BACKEND_ROOT"
  exec "$PYTHON" -m app.api
) > "$PROJECT_ROOT/logs/api-dev.log" 2>&1 &
API_PID=$!
(
  cd "$BACKEND_ROOT"
  exec "$PYTHON" -m app.worker
) > "$PROJECT_ROOT/logs/worker-dev.log" 2>&1 &
WORKER_PID=$!

cleanup() {
  kill "$API_PID" "$WORKER_PID" 2>/dev/null || true
  wait "$API_PID" "$WORKER_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

sleep 2
if ! kill -0 "$API_PID" 2>/dev/null; then
  echo "API failed to start. Check logs/api-dev.log." >&2
  exit 1
fi
if ! kill -0 "$WORKER_PID" 2>/dev/null; then
  echo "Worker failed to start. Check logs/worker-dev.log." >&2
  exit 1
fi

DISPLAY_HOST="$FRONTEND_HOST"
if [[ "$FRONTEND_HOST" == "0.0.0.0" ]]; then
  DISPLAY_HOST="$(hostname -I 2>/dev/null | awk '{print $1}')"
  DISPLAY_HOST="${DISPLAY_HOST:-127.0.0.1}"
fi
echo "Reader (local): http://127.0.0.1:$FRONTEND_PORT/#/"
echo "Reader (LAN):   http://$DISPLAY_HOST:$FRONTEND_PORT/#/"
echo "Admin (LAN):    http://$DISPLAY_HOST:$FRONTEND_PORT/#/admin/daily"
echo "API (LAN):      http://$DISPLAY_HOST:8000"
echo "Backend logs are in $PROJECT_ROOT/logs. Press Ctrl+C to stop all processes."
cd "$FRONTEND_ROOT"
npm run dev -- --host "$FRONTEND_HOST" --port "$FRONTEND_PORT"
