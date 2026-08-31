#!/bin/bash
# Stop and remove only the two Aether sales LaunchAgents.

set -euo pipefail

for label in com.aether.sales-api com.aether.sales-worker; do
  launchctl bootout "gui/$UID/$label" 2>/dev/null || true
  destination="$HOME/Library/LaunchAgents/$label.plist"
  if [ -f "$destination" ]; then
    mv "$destination" "$HOME/.Trash/$label.plist"
  fi
done

echo "Stopped both Aether sales LaunchAgents; their plists were moved to Trash."
