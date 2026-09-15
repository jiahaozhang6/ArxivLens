#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
RUN_USER="${ARXIV_DIGEST_USER:-${SUDO_USER:-$USER}}"

if [[ ! -x "$PROJECT_ROOT/backend/.venv/bin/python" ]]; then
  echo "Backend environment is missing. Run ./scripts/setup.sh first." >&2
  exit 1
fi
if [[ "$PROJECT_ROOT" == *"|"* || "$PROJECT_ROOT" == *$'\n'* ]]; then
  echo "The project path contains unsupported characters for unit generation." >&2
  exit 1
fi
if ! id "$RUN_USER" >/dev/null 2>&1; then
  echo "Service user does not exist: $RUN_USER" >&2
  exit 1
fi

RUN_GROUP="$(id -gn "$RUN_USER")"
if (( EUID == 0 )); then
  SUDO=()
else
  if ! command -v sudo >/dev/null 2>&1; then
    echo "sudo is required to install system services." >&2
    exit 1
  fi
  SUDO=(sudo)
fi

escape_sed() {
  printf '%s' "$1" | sed -e 's/[&|\\]/\\&/g'
}

ROOT_ESCAPED="$(escape_sed "$PROJECT_ROOT")"
USER_ESCAPED="$(escape_sed "$RUN_USER")"
GROUP_ESCAPED="$(escape_sed "$RUN_GROUP")"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

for SERVICE in arxiv-digest-api arxiv-digest-worker; do
  sed \
    -e "s|@PROJECT_ROOT@|$ROOT_ESCAPED|g" \
    -e "s|@RUN_USER@|$USER_ESCAPED|g" \
    -e "s|@RUN_GROUP@|$GROUP_ESCAPED|g" \
    "$SCRIPT_DIR/systemd/$SERVICE.service.in" > "$TMP_DIR/$SERVICE.service"
  "${SUDO[@]}" install -m 0644 "$TMP_DIR/$SERVICE.service" "/etc/systemd/system/$SERVICE.service"
done

"${SUDO[@]}" systemctl daemon-reload
"${SUDO[@]}" systemctl enable --now arxiv-digest-api.service arxiv-digest-worker.service
echo "Installed systemd services as $RUN_USER:$RUN_GROUP."
echo "Open http://127.0.0.1:8000/#/ (reader) or http://127.0.0.1:8000/#/admin/daily (admin)."
echo "Inspect logs with journalctl -u arxiv-digest-api -u arxiv-digest-worker."
