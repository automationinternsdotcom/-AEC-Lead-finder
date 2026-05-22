# Aether CRE Lead Pipeline

A daily pipeline for Aether Facility Services (Phoenix, AZ) that discovers Arizona commercial real-estate news, qualifies them, optionally enriches them with decision-maker contact info via Apollo, and pushes qualified items into **Pipedrive's Leads Inbox** for Jordan to triage. A Claude Code session reads `skill/aether_daily_routine.md` and drives the pipeline step by step.

**Client:** Jordan Whitehurst, Aether Facility Services

## Architecture

Six stdlib CLI sub-tools, each reading from stdin or arguments and writing JSON to stdout:

```
pipeline.cli.fetch    — discover new article URLs via Google News + RSS; filters against seen_urls (SQLite)
pipeline.cli.extract  — fetch + clean article text for a single URL
pipeline.cli.qualify  — gate: pass only Arizona CRE signals above confidence threshold
pipeline.cli.enrich   — Apollo people lookup by company domain (optional)
pipeline.cli.push     — create Pipedrive Org + Person + Lead; dedup on Article URL custom field
pipeline.cli.mark     — record URL state in seen_urls (pushed / filtered / failed)
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

## Testing

```bash
uv run python -m unittest discover tests -v
```

Currently 44 tests covering the CLI tools and helper modules.

## Status

POC, not production. See [docs/superpowers/plans/2026-05-21-claude-routine-refactor.md](docs/superpowers/plans/2026-05-21-claude-routine-refactor.md) for the refactor that produced this architecture.
