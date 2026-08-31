#!/bin/bash
# Install or refresh the local webhook API and one-minute worker LaunchAgents.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
case "$REPO_ROOT" in
  */.codex/worktrees/*)
    echo "ERROR: install from the permanent AEC-Lead-finder checkout, not a Codex worktree."
    exit 1
    ;;
esac
AGENTS_DIR="$HOME/Library/LaunchAgents"
LOG_DIR="$REPO_ROOT/logs"
mkdir -p "$AGENTS_DIR" "$LOG_DIR"

install_agent() {
  local label="$1"
  local template="$REPO_ROOT/infra/macos/$label.plist.template"
  local destination="$AGENTS_DIR/$label.plist"
  sed \
    -e "s|__REPO_ROOT__|$REPO_ROOT|g" \
    -e "s|__HOME__|$HOME|g" \
    "$template" > "$destination"
  launchctl bootout "gui/$UID/$label" 2>/dev/null || true
  launchctl bootstrap "gui/$UID" "$destination"
}

install_agent com.aether.sales-api
install_agent com.aether.sales-worker
install_agent com.aether.sales-tunnel

echo "Installed com.aether.sales-api, com.aether.sales-worker, and com.aether.sales-tunnel"
echo "Health: curl http://127.0.0.1:${AETHER_SALES_PORT:-8187}/healthz"
echo "Logs:   $LOG_DIR/sales-api.log and $LOG_DIR/sales-worker.log"
