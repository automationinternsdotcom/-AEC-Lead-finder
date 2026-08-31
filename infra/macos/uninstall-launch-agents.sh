#!/bin/bash
# Stop and remove only the Aether sales LaunchAgents.

set -euo pipefail

for label in com.aether.sales-api com.aether.sales-worker com.aether.sales-tunnel; do
  launchctl bootout "gui/$UID/$label" 2>/dev/null || true
  destination="$HOME/Library/LaunchAgents/$label.plist"
  if [ -f "$destination" ]; then
    mv "$destination" "$HOME/.Trash/$label.plist"
  fi
done

echo "Stopped the Aether sales LaunchAgents; their plists were moved to Trash."
