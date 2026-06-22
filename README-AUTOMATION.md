# Aether nightly automation — PREPARED, NOT ENABLED

Runs the existing pipeline (fetch → qualify → **Grok/browser enrich** → push to
Pipedrive) every night at **02:00** on **this machine** (it has the working
Chrome / SuperGrok / Codex Chrome Extension setup). No Apollo. No architecture
change.

## Files (all here, nothing installed system-wide yet)
- `run-nightly.sh` — the wrapper (opens Chrome, sources the env, runs the routine headless via `codex exec`).
- `com.aether.nightly.plist` — the launchd LaunchAgent (02:00 daily). **Not yet in `~/Library/LaunchAgents`.**
- `logs/` — per-run logs.
- This repo is a git worktree pinned to `main`.

## ⚠️ The one thing that must be validated before enabling
Enrichment drives the **Codex Chrome Extension → SuperGrok** bridge. Whether a
*headless* `codex exec` run can pair with that extension at 02:00 unattended is
**unproven**. The wrapper's prompt tells Codex to verify the bridge first and to
avoid pushing leads unless enrichment produced at least one email-bearing
contact, but you must confirm a real run works end to end first.

## Step 1 — Validate manually
Keep `DRY_RUN=1` as the default in the env file. The scheduled wrapper currently
overrides `DRY_RUN=0` for the actual pipeline run, so validate only when you are
ready for live Pipedrive writes. With Chrome open + SuperGrok logged in (Fast
mode) + the Codex Chrome Extension connected:
```bash
AETHER_ENV=$HOME/.aether-pipedrive-prod.env /Users/openclaw/aether-runner/run-nightly.sh
tail -n 200 /Users/openclaw/aether-runner/logs/run-*.log
```
Confirm in the log: bridge reached, articles fetched, URLs were resolved to
publisher article links, same-event dedup ran, leads enriched WITH email-bearing
contacts, and Pipedrive push completed. If enrichment returned no contacts, the
headless bridge isn't working — fix that before going further.

## Step 2 — Go live (writes)
Only after Step 1 looks right:
1. Decide the target account and set `AETHER_ENV` (prod `~/.aether-pipedrive-prod.env` or the sandbox `~/.aether-pipedrive.env`).
2. Keep `DRY_RUN=1` as the env-file default unless you intentionally want all manual commands live. The scheduled wrapper overrides only its own run to `DRY_RUN=0`.
3. Make the Mac wake for the job (LaunchAgents don't wake the machine themselves):
   ```bash
   sudo pmset repeat wakeorpoweron MTWRFSU 01:58:00
   ```
4. Install + load the LaunchAgent:
   ```bash
   cp /Users/openclaw/aether-runner/com.aether.nightly.plist ~/Library/LaunchAgents/
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.aether.nightly.plist
   launchctl enable gui/$(id -u)/com.aether.nightly
   ```
5. Optional immediate test fire: `launchctl kickstart -k gui/$(id -u)/com.aether.nightly`

## Disable / remove
```bash
launchctl bootout gui/$(id -u)/com.aether.nightly
rm ~/Library/LaunchAgents/com.aether.nightly.plist
sudo pmset repeat cancel
```

## Hard requirements at 02:00 (else the run fails or under-enriches)
- Mac awake + user logged in (GUI session) — LaunchAgent needs the Aqua session for Chrome.
- Chrome open, Codex Chrome Extension connected, SuperGrok logged in, **Fast** mode.
- A valid `codex` login on this machine.

## Known limitations (inherent to keeping the Grok/browser architecture)
- If the SuperGrok session expires or the extension drops, that night under-enriches or aborts.
- No alerting — check `logs/` (add a notifier later if you want).
- `bypassPermissions` is used so it can run unattended; it writes to a live CRM, so validate carefully.
