# Aether nightly automation — PREPARED, NOT ENABLED

Runs the existing pipeline (fetch → qualify → **Grok/browser enrich** → push to
Pipedrive → email digest) every night at **02:00** on **this machine** (it has
the working Chrome / SuperGrok / Codex Chrome Extension setup). No Apollo. No
architecture change.

## Files (all here, nothing installed system-wide yet)
- `run-nightly.sh` — the wrapper (opens Chrome, sources the env, copies the AGENTS.md prompt, opens Codex Desktop, then pastes/submits the handoff).
- `com.aether.nightly.plist` — the launchd LaunchAgent (02:00 daily). **Not yet in `~/Library/LaunchAgents`.**
- `logs/` — per-run logs.
- This repo is a git worktree pinned to `main`.

## ⚠️ The one thing that must be validated before enabling
Enrichment drives the **Codex Desktop → Codex Chrome Extension → SuperGrok**
bridge. The old headless `codex exec` path cannot reliably attach to the Desktop
Chrome Extension, so the wrapper now hands the run to the Codex Desktop app via
GUI automation. The wrapper's prompt tells Codex to verify the bridge first and
to avoid pushing leads unless enrichment produced at least one email-bearing
contact, but you must confirm a real run works end to end first.

## Step 1 — Validate manually
Keep `DRY_RUN=1` as the default in the env file. The scheduled wrapper keeps that
global default and AGENTS.md scopes `DRY_RUN=0` only to the exact live write
subprocesses: Pipedrive push, same-event contact merge, and email digest send. Validate
only when you are ready for those live writes. With Chrome open + SuperGrok logged in
(Fast mode) + the Codex Chrome Extension connected:
```bash
AETHER_ENV=$HOME/.aether-pipedrive-prod.env /Users/openclaw/aether-runner/run-nightly.sh
tail -n 200 /Users/openclaw/aether-runner/logs/run-*.log
```
Confirm in the log: bridge reached, articles fetched, URLs were resolved to
publisher article links, same-event dedup ran, leads enriched WITH email-bearing
contacts, and Pipedrive push completed. If enrichment returned no contacts, the
Desktop handoff / Chrome Extension bridge needs checking — fix that before
going further.

## Step 2 — Go live (writes)
Only after Step 1 looks right:
1. Decide the target account and set `AETHER_ENV` (prod `~/.aether-pipedrive-prod.env` or the sandbox `~/.aether-pipedrive.env`).
2. Keep `DRY_RUN=1` as the env-file default unless you intentionally want all manual commands live. The scheduled wrapper does not globally override it; AGENTS.md prefixes only the required write subprocesses with `DRY_RUN=0`.
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
- Codex Desktop installed, logged in, and controllable in the GUI session.
- `codex app` available on this machine.
- macOS automation permissions granted so the script can activate Codex and paste/submit the prompt with `osascript`.

## Known limitations (inherent to keeping the Grok/browser architecture)
- If the SuperGrok session expires or the extension drops, that night under-enriches or aborts.
- No alerting — check `logs/` (add a notifier later if you want).
- The wrapper depends on GUI automation (`pbcopy`, `codex app`, and `osascript`), so the user session must be unlocked enough for Codex to receive the pasted prompt.
- AGENTS.md scopes `DRY_RUN=0` to live Pipedrive/email write subprocesses, so validate carefully before enabling.
