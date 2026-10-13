#!/usr/bin/env bash
# Install uv if needed, then start candaptor-gui. Double-click run.command on macOS.
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v uv >/dev/null 2>&1; then
  if [[ -x "$HOME/.local/bin/uv" ]]; then
    export PATH="$HOME/.local/bin:$PATH"
  else
    echo "Installing uv…"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
  fi
fi

echo "Starting candaptor-gui (first run may take a minute)…"
exec uv run candaptor-gui "$@"
