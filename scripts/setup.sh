#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKEND_ROOT="$PROJECT_ROOT/backend"
FRONTEND_ROOT="$PROJECT_ROOT/frontend"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required. Install it from https://docs.astral.sh/uv/ and rerun this script." >&2
  exit 1
fi
if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
  echo "Node.js 20 or newer is required to build the frontend." >&2
  exit 1
fi

NODE_MAJOR="$(node --version | sed 's/^v//' | cut -d. -f1)"
if (( NODE_MAJOR < 20 )); then
  echo "Node.js 20 or newer is required; found $(node --version)." >&2
  exit 1
fi

mkdir -p "$PROJECT_ROOT/data" "$PROJECT_ROOT/logs" "$PROJECT_ROOT/backups"
if [[ ! -f "$PROJECT_ROOT/.env" ]]; then
  SECRET_KEY="$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')"
  sed "s/replace-with-a-long-random-string/$SECRET_KEY/" "$PROJECT_ROOT/.env.example" > "$PROJECT_ROOT/.env"
  chmod 600 "$PROJECT_ROOT/.env"
  echo "Created .env with a random SECRET_KEY. Keep this file stable and private."
fi

(
  cd "$BACKEND_ROOT"
  uv sync --frozen --group dev
  .venv/bin/python -m alembic upgrade head
)
(
  cd "$FRONTEND_ROOT"
  npm ci
  npm run build
)

chmod +x "$SCRIPT_DIR"/*.sh
echo "Setup complete. Start the API with ./scripts/start-api.sh and the worker with ./scripts/start-worker.sh."
echo "Reader: http://127.0.0.1:8000/#/"
echo "Admin:  http://127.0.0.1:8000/#/admin/daily"
