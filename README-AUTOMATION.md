# Aether AEC Nightly Automation

This repository now follows the same headless automation pattern as
`gps-grok-leadfinder`.

The nightly command is:

```bash
uv run scout/pipeline.py
```

`run-nightly.sh` is a thin wrapper around that command. It loads `.env` when present,
writes a timestamped log under `logs/`, and exits with the same status as the scout
pipeline.

## Files

- `run-nightly.sh` — local nightly wrapper.
- `com.aether.nightly.plist` — launchd LaunchAgent template.
- `logs/` — generated run logs, ignored by git.
- `results/YYYY-MM-DD/` — generated CSV and HTML reports, ignored by git.
- `scout.db` — SQLite state, ignored by git.

## Enable Locally

1. Confirm `.env` contains the Responses API settings from `README.md`.
2. Run a manual smoke test:

   ```bash
   ./run-nightly.sh --max-articles 5
   ```

3. Install the LaunchAgent when the smoke test is good:

   ```bash
   cp com.aether.nightly.plist ~/Library/LaunchAgents/
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.aether.nightly.plist
   launchctl enable gui/$(id -u)/com.aether.nightly
   ```

## Disable

```bash
launchctl bootout gui/$(id -u)/com.aether.nightly
rm ~/Library/LaunchAgents/com.aether.nightly.plist
```
