#!/bin/bash
# One bounded pass over the local SQLite queue. launchd runs this every minute.

set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

RUNNER="${RUNNER:-$(cd "$(dirname "$0")" && pwd)}"
AETHER_ENV_FILE="${AETHER_ENV_FILE:-$RUNNER/.env}"

UV_ENV_ARGS=()
if [ -f "$AETHER_ENV_FILE" ]; then
  UV_ENV_ARGS=(--env-file "$AETHER_ENV_FILE")
fi

cd "$RUNNER"
exec uv run "${UV_ENV_ARGS[@]}" python -m integration.worker --enqueue-gmail-sync
