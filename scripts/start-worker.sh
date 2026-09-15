#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKEND_ROOT="$PROJECT_ROOT/backend"
PYTHON="$BACKEND_ROOT/.venv/bin/python"

if [[ ! -x "$PYTHON" ]]; then
  echo "Backend environment is missing. Run ./scripts/setup.sh first." >&2
  exit 1
fi

cd "$BACKEND_ROOT"
exec "$PYTHON" -m app.worker
