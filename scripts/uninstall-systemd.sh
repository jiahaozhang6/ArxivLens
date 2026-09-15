#!/usr/bin/env bash
set -euo pipefail

if (( EUID == 0 )); then
  SUDO=()
else
  if ! command -v sudo >/dev/null 2>&1; then
    echo "sudo is required to uninstall system services." >&2
    exit 1
  fi
  SUDO=(sudo)
fi

"${SUDO[@]}" systemctl disable --now arxiv-digest-worker.service arxiv-digest-api.service 2>/dev/null || true
"${SUDO[@]}" rm -f /etc/systemd/system/arxiv-digest-worker.service /etc/systemd/system/arxiv-digest-api.service
"${SUDO[@]}" systemctl daemon-reload
echo "Removed ArxivLens systemd services. Project data was not deleted."
