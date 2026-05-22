# Aether CRE Lead Pipeline

A daily pipeline for Aether Facility Services (Phoenix, AZ) that discovers Arizona commercial real-estate news, qualifies leads, optionally enriches them with decision-maker contact info via Apollo, and pushes qualified deals into Pipedrive. A Claude Code session reads `skill/aether_daily_routine.md` and drives the pipeline step by step.

**Client:** Jordan Whitehurst, Aether Facility Services

## Architecture

Six stdlib CLI sub-tools, each reading from stdin or arguments and writing JSON to stdout:

```
pipeline.cli.fetch    — discover new article URLs via Google News + RSS; filters against seen_urls (SQLite)
pipeline.cli.extract  — fetch + clean article text for a single URL
pipeline.cli.qualify  — gate: pass only Arizona CRE signals above confidence threshold
pipeline.cli.enrich   — Apollo people lookup by company domain (optional)
pipeline.cli.push     — create Pipedrive org + deal; dedup on Article URL custom field
pipeline.cli.mark     — record URL state in seen_urls (pushed / filtered / failed)
```

Claude orchestrates the loop via `skill/aether_daily_routine.md`, running each tool with Bash and making all judgment calls (extraction, qualification confidence, prompt-injection defense). SQLite (`db.sqlite`) is the local dedup state store. The `Article URL` custom field on Pipedrive deals is the secondary dedup gate.

No GHA workflow. No Anthropic SDK. No remote scheduled job — execution is local.

## Setup

### 1. Clone and install dependencies

```bash
git clone <repo-url>
cd aether-cre-lead-pipeline
uv sync   # installs Python 3.12 + all deps
```

### 2. Configure Pipedrive

In your Pipedrive account:

1. Create a pipeline named **"Aether Article Sources"** with stages: New, Reviewing, Pursuing, Discarded.
2. Add a custom field named **"Article URL"** of type Text on the Deal entity.
3. Capture the pipeline ID, stage ID, and field key:

```bash
# Pipeline and stage IDs
curl "https://<domain>.pipedrive.com/api/v1/pipelines?api_token=<token>" | jq '.data[] | {id, name}'
curl "https://<domain>.pipedrive.com/api/v1/stages?api_token=<token>" | jq '.data[] | {id, name, pipeline_id}'

# Article URL field key (looks like a hash, e.g. abc123def...)
curl "https://<domain>.pipedrive.com/api/v1/dealFields?api_token=<token>" \
  | jq '.data[] | select(.name=="Article URL")'
```

### 3. Create your env file

```bash
cp .env.example ~/.aether-pipedrive.env
chmod 600 ~/.aether-pipedrive.env
# Edit and fill in real values
```

### 4. Verify setup

```bash
source ~/.aether-pipedrive.env
env | grep -E '^PIPEDRIVE_' | wc -l   # should print 5
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

Currently 40 tests covering the CLI tools and helper modules.

## Status

POC, not production. See [docs/superpowers/plans/2026-05-21-claude-routine-refactor.md](docs/superpowers/plans/2026-05-21-claude-routine-refactor.md) for the refactor that produced this architecture.
