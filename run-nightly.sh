#!/bin/bash
# Aether nightly lead pipeline.
# GPS-style headless run: scout/pipeline.py does discovery, enrichment, scoring,
# and HTML generation. No Codex Desktop or browser handoff is required.

set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

RUNNER="${RUNNER:-$(cd "$(dirname "$0")" && pwd)}"
AETHER_ENV="${AETHER_ENV:-$RUNNER/.env}"
LOG_DIR="$RUNNER/logs"
TS="$(date +%Y%m%d-%H%M%S)"
LOG="$LOG_DIR/run-$TS.log"
mkdir -p "$LOG_DIR"
exec >>"$LOG" 2>&1

echo "===== Aether scout nightly run: $(date) ====="
echo "runner=$RUNNER env=$AETHER_ENV"

UV_ENV_ARGS=()
if [ -f "$AETHER_ENV" ]; then
  UV_ENV_ARGS=(--env-file "$AETHER_ENV")
else
  echo "WARN: env file not found: $AETHER_ENV"
fi

cd "$RUNNER"
set +e
uv run "${UV_ENV_ARGS[@]}" scout/pipeline.py "$@"
RC=$?
set -e
echo "===== scout pipeline exit=$RC done: $(date) ====="
exit "$RC"
