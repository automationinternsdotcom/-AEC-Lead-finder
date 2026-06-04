# Aether CRE Lead Pipeline

A daily pipeline for Aether Facility Services (Phoenix, AZ) that discovers Arizona commercial real-estate news, qualifies them, optionally enriches them with decision-maker contact info via Apollo, and pushes qualified items into **Pipedrive's Leads Inbox** for Jordan to triage. A Claude Code session reads `skill/aether_daily_routine.md` and drives the pipeline step by step.

**Client:** Jordan Whitehurst, Aether Facility Services

## Architecture

Stdlib CLI sub-tools, each reading from stdin or arguments and writing JSON to stdout:

```
pipeline.cli.fetch    — discover new article URLs via Google News + RSS; filters against seen_urls (SQLite)
pipeline.cli.backfill — one-off expanded 60-day Google News sweep for demo/backfill work
pipeline.cli.extract  — fetch + clean article text for a single URL
pipeline.cli.qualify  — gate: pass only Arizona CRE signals above confidence threshold
pipeline.cli.enrich   — Apollo people lookup by company domain (optional)
pipeline.cli.push     — create Pipedrive Org + Person + Lead; dedup on Article URL custom field
pipeline.cli.mark     — record URL state in seen_urls (pushed / filtered / failed)
pipeline.cli.pipedrive_v2 — dry-run cleanup, schema v2, pipeline v2, and automation previews
```

Claude orchestrates the loop via `skill/aether_daily_routine.md`, running each tool with Bash and making all judgment calls (extraction, qualification confidence, prompt-injection defense). SQLite (`db.sqlite`) is the local dedup state store. The `Article URL` custom field on Pipedrive (shared between Lead and Deal entities) is the secondary dedup gate.

**Why Leads, not Deals:** Pipedrive Leads are the right surface for machine-extracted, unvetted inputs. Jordan triages the Leads Inbox daily — promising ones convert to Deals (preserving all the data + carrying the Article URL field), the rest archive. Pushing straight to Deals would have polluted his active pipeline with ~50/day of noise and lost the conversion-as-qualification signal.

No GHA workflow. No Anthropic SDK. No remote scheduled job — execution is local.

## Setup

### 1. Clone and install dependencies

```bash
git clone <repo-url>
cd aether-cre-lead-pipeline
uv sync   # installs Python 3.12 + all deps
```

### 2. Configure Pipedrive

In your Pipedrive account, add a custom field named **"Article URL"** of type Text. Pipedrive shares custom fields between Lead and Deal entities, so creating it once works for both — and means a Lead's URL is preserved when Jordan converts it to a Deal.

Capture the field hash:

```bash
curl "https://<domain>.pipedrive.com/api/v1/dealFields?api_token=<token>" \
  | jq -r '.data[] | select(.name=="Article URL") | .key'
```

(You can also use `/leadFields` — same hash either way.)

### 2.5 Configure enrichment (choose one)

The pipeline enriches qualifying leads with decision-maker contact info. Two paths:

**Path A: SuperGrok via Claude in Chrome (default — no extra API costs)**

- Open SuperGrok ([grok.com](https://grok.com)) in Chrome with the [Claude in Chrome](https://www.anthropic.com/news/claude-in-chrome) extension active.
- Log in to your SuperGrok account.
- Verify **Fast** mode is selected in the chat input (not Heavy / Expert / Auto). Heavy mode takes 5+ minutes per query and blows the daily time budget.
- The daily routine's enricher subagent (`skill/grok_enricher.md`) drives the session per-article via Chrome MCP. ~6-10s per query.

**Path B: Apollo.io API (set `APOLLO_API_KEY` in `.env`)**

- Requires an Apollo subscription (~$99/mo+).
- When `APOLLO_API_KEY` is set in your env, the routine uses Apollo and skips Grok entirely.
- Useful for headless CI or environments without an active Chrome session.

### 2.6 Optional schema v2 / automation fields

The minimal required field is `Article URL`. For the Friday v2 demo, also create/configure:

- `Date Posted`
- `Lead 1`, `Lead 2`, `Lead 3`
- `Lead 1 LinkedIn`, `Lead 2 LinkedIn`, `Lead 3 LinkedIn`
- `Next Follow-up Due`
- `Follow-up Status`
- `Automation Log`

Preview the desired field plan without writing to Pipedrive:

```bash
uv run python -m pipeline.cli.pipedrive_v2 schema
```

`push.py` now writes LinkedIn URLs both into the visible Lead 1/2/3 contact text and into the optional individual `Lead N LinkedIn` fields when their hashes are configured.

### 2.7 Create the `NOT RELEVANT` Lead label

Jordan flags article-sourced Leads that aren't relevant by applying a Pipedrive Lead label named **`NOT RELEVANT`** (Settings → Lead labels → + Add label). The daily routine polls these flags at the end of each run and surfaces them in the run report so the operator can spot patterns and manually tune the routine's filter protocol (`skill/aether_daily_routine.md` Step 2b).

**Important:** no automated suppression — flagging the same company multiple times doesn't change pipeline behavior. The signal is informational only. If `NOT RELEVANT` flags become high-volume, the operator updates the routine's HIGH/MEDIUM/LOW protocol or disables noisy source feeds.

### 3. Recommend: set up a saved Leads view for Jordan

In the Pipedrive UI, open **Leads Inbox**, click **+ Add filter**, and save a filter like `Article URL is not empty` named **"Aether Article Leads"**. Configure the visible columns: Title, Organization, Value, Article URL, Labels, Created, Owner. This gives Jordan a single-click daily review surface — and avoids the "902 unreviewed leads" graveyard scenario.

### 4. Create your env file

```bash
cp .env.example ~/.aether-pipedrive.env
chmod 600 ~/.aether-pipedrive.env
# Edit and fill in real values
```

### 5. Verify setup

```bash
source ~/.aether-pipedrive.env
env | grep -E '^PIPEDRIVE_' | wc -l   # should print 3
```

## How to Run

The skill file `skill/aether_daily_routine.md` contains the full step-by-step instructions. Start a Claude Code session in the repo root and trigger it one of two ways:

### Option A: Interactive via `/loop` in a Claude Code session

Open a Claude Code session in the repo directory and run:

```
/loop 24h follow skill/aether_daily_routine.md
```

Claude will re-execute the pipeline every 24 hours while the session stays open. This is the easiest option for development and testing.

### Option B: Local cron via `claude code --headless`

Add a crontab entry to run the pipeline autonomously:

```
0 7 * * * cd /path/to/repo && source ~/.aether-pipedrive.env && claude code --headless 'follow skill/aether_daily_routine.md'
```

This runs at 7am daily. The machine must be on at that time. The exact `claude code --headless` invocation is approximate — verify against current Claude Code CLI docs.

### Friday demo dry-runs

```bash
# Expanded 60-day source sweep
uv run python -m pipeline.cli.backfill --days 60 > /tmp/aether_backfill.json

# Schema v2 plan
uv run python -m pipeline.cli.pipedrive_v2 schema | jq .

# Pipeline v2 plan
uv run python -m pipeline.cli.pipedrive_v2 pipeline | jq .

# Cleanup report from an exported/sampled Leads JSON array
uv run python -m pipeline.cli.pipedrive_v2 cleanup < /tmp/leads.json | jq .
```

Follow-up call/email activities are production-gated. Leave `PIPEDRIVE_ENABLE_AUTOMATIONS=0` for previews; set it to `1` only after dry-run review and owner ID confirmation.

## Testing

```bash
uv run python -m unittest discover tests -v
```

The suite covers CLI tools, Grok parsing, Pipedrive push/update behavior, v2 planning, backfill generation, and automation payloads.

## Status

POC, not production. See [docs/superpowers/plans/2026-05-21-claude-routine-refactor.md](docs/superpowers/plans/2026-05-21-claude-routine-refactor.md) for the refactor that produced this architecture.
