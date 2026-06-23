#!/bin/bash
# Aether nightly lead pipeline — PREPARED, NOT ENABLED.
# Runs the AGENTS.md Grok/browser routine through Codex Desktop so browser
# enrichment uses the Desktop app's Chrome Extension context. No Apollo, no
# architecture change. Designed to run on THIS machine (it has the working
# Chrome / SuperGrok browser setup).
#
# RUN-TIME PREREQUISITES (must be true at 02:00):
#   - Machine awake + the user logged in (GUI session) — see pmset note in README.
#   - Google Chrome open, the browser extension CONNECTED, and SuperGrok logged
#     in (Fast mode). Enrichment cannot work without this.
#
# SAFETY: keep DRY_RUN=1 in the sourced env. AGENTS.md scopes DRY_RUN=0 only
# onto the exact Pipedrive/email write subprocesses.

set -uo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

RUNNER="/Users/openclaw/aether-runner"
CODEX_BIN="${CODEX_BIN:-/opt/homebrew/bin/codex}"
# Which Pipedrive account the nightly writes to. <-- CONFIRM THIS.
AETHER_ENV="${AETHER_ENV:-$HOME/.aether-pipedrive-prod.env}"
LOG_DIR="$RUNNER/logs"
TS="$(date +%Y%m%d-%H%M%S)"
LOG="$LOG_DIR/run-$TS.log"
mkdir -p "$LOG_DIR"
exec >>"$LOG" 2>&1

echo "===== Aether nightly run: $(date) ====="
echo "runner=$RUNNER  env=$AETHER_ENV"

if [ ! -f "$AETHER_ENV" ]; then echo "FATAL: missing env file $AETHER_ENV"; exit 1; fi
if [ ! -x "$CODEX_BIN" ]; then echo "FATAL: codex binary not executable at $CODEX_BIN"; exit 1; fi

# 1) Bring Chrome up so the Grok/extension bridge can (re)connect.
open -ga "Google Chrome" || true
sleep 20

# 2) Load Pipedrive creds + field hashes from the env file. Keep the env's
#    DRY_RUN default globally; AGENTS.md scopes live mode to write subprocesses.
set -a; source "$AETHER_ENV"; set +a
echo "DRY_RUN=${DRY_RUN:-unset}  domain=${PIPEDRIVE_DOMAIN:-unset}"

# 3) Hand the AGENTS.md routine to Codex Desktop. The separate headless
#    `codex exec` path cannot reliably attach to the Desktop Chrome Extension.
cd "$RUNNER"
PROMPT='Run the Aether daily lead pipeline now, end to end, by following AGENTS.md exactly: fetch new articles, extract, qualify per Jordan'"'"'s HIGH/MEDIUM/LOW protocol, enrich each qualifying lead via the browser enrichment flow described in AGENTS.md using the already-open, logged-in SuperGrok session in Chrome (Fast mode), push only qualified leads that have at least one enriched contact with an email address, then run the Step 5 email digest.

Explicit requirements:
- Resolve Google News/RSS wrapper URLs before extraction, dedup, same-event matching, push, and email.
- /tmp/urls.json must use resolved publisher article URLs, not wrapper URLs.
- Never push a news.google.com wrapper URL to Pipedrive.
- Run same-event dedup with find_event_candidates before every push.
- If a same-event match is found, merge contacts into the existing Lead, mark the URL merged, and skip creating a new Lead.
- Keep DRY_RUN=1 globally; use DRY_RUN=0 only on the exact live write subprocesses specified in AGENTS.md (Pipedrive push, contact merge, and email digest send).

Email-only contacts are acceptable; email plus contact details are ideal. Do not push empty enrichments or contacts with no email address. IMPORTANT: first verify the browser/SuperGrok bridge is reachable. If it is NOT reachable, report it and continue, but do not push leads unless another enrichment source produced an email-bearing contact. End with a summary: fetched / qualified / enriched-with-email / no-email-skipped / same-event-merged / pushed / skipped / emailed counts.'

PROMPT_FILE="$LOG_DIR/desktop-prompt-$TS.txt"
printf '%s\n' "$PROMPT" > "$PROMPT_FILE"
printf '%s' "$PROMPT" | pbcopy

"$CODEX_BIN" app "$RUNNER"
sleep 8

osascript <<'APPLESCRIPT'
tell application "Codex" to activate
delay 1
tell application "System Events"
  tell process "Codex"
    set frontmost to true
    keystroke "v" using command down
    delay 0.2
    key code 36
  end tell
end tell
APPLESCRIPT
RC=$?
echo "desktop_prompt=$PROMPT_FILE"
echo "===== codex desktop handoff exit=$RC  done: $(date) ====="
exit $RC
