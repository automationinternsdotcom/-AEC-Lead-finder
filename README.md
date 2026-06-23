# Aether CRE Lead Pipeline

A daily pipeline for Aether Facility Services (Phoenix, AZ) that discovers Arizona commercial real-estate news, qualifies them, enriches them with decision-maker contact info, and pushes qualified items into **Pipedrive's Leads Inbox** for Jordan to triage. Codex follows `AGENTS.md` to drive the pipeline step by step.

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

Codex orchestrates the loop via `AGENTS.md`, running each tool with Bash and making all judgment calls (extraction, qualification confidence, prompt-injection defense). SQLite (`db.sqlite`) is the local dedup state store. The `Article URL` custom field on Pipedrive (shared between Lead and Deal entities) is the secondary dedup gate.

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
- Verify **Fast** mode is selected in the chat input (not Expert / Heavy / Auto). Expert mode takes 5+ minutes per query, so it's used only for the step-6b escalation, not as the starting mode.
- The daily routine's enricher subagent (`skill/grok_enricher.md`) drives the session per-article via Chrome MCP. ~6-10s per query.

**Path B: Apollo.io API (set `APOLLO_API_KEY` in `.env`)**

- Requires an Apollo subscription (~$99/mo+).
- When `APOLLO_API_KEY` is set in your env, the routine uses Apollo and skips Grok entirely.
- Useful for headless CI or environments without an active Chrome session.

### 2.6 Create the `NOT RELEVANT` Lead label

Jordan flags article-sourced Leads that aren't relevant by applying a Pipedrive Lead label named **`NOT RELEVANT`** (Settings → Lead labels → + Add label). The daily routine polls these flags at the end of each run and surfaces them in the run report so the operator can spot patterns and manually tune the routine's filter protocol (`AGENTS.md` Step 2b).

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

`AGENTS.md` contains the full step-by-step instructions. Start Codex in the repo root and trigger it one of two ways:

### Option A: Interactive Codex run

Open Codex in the repo directory and ask it to:

```
Run the Aether daily lead pipeline by following AGENTS.md.
```

This is the easiest option for development and testing.

### Option B: Local automation

Use the Codex Desktop handoff in `run-nightly.sh` or create a native Codex automation that follows `AGENTS.md`.

See `README-AUTOMATION.md` for the local automation setup notes.

## Testing

```bash
uv run python -m unittest discover tests -v
```

Currently 44 tests covering the CLI tools and helper modules.

## Status

POC, not production. See [docs/superpowers/plans/2026-05-21-claude-routine-refactor.md](docs/superpowers/plans/2026-05-21-claude-routine-refactor.md) for the refactor that produced this architecture.
