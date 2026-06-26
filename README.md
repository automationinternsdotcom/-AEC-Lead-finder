# Aether Lead Engine

CampaignSpec-driven lead discovery pipeline for Aether Facility Services.

The current cleaning-company flow uses Gemini API discovery to find candidate
source URLs, deterministic Python stages to normalize/classify/expand/dedupe and
extract, Codex to qualify articles, Grok to enrich contacts, and Pipedrive as
the CRM destination. After all pushed leads are complete, the daily email
digest reads the new Leads back out of Pipedrive and sends Jordan the summary.

## Current Flow

```text
CampaignSpec
-> render Gemini discovery prompt
-> Gemini API discovery
-> discover.json
-> classify / expand URLs
-> fetch_rows.json
-> extract article/page text
-> Codex creates ExtractedArticle JSON
-> event_signal pattern
-> Grok Fast enrichment
-> Grok Expert fallback when needed
-> Pipedrive Leads
-> end-of-day email report
```

Gemini only discovers source URLs. It does not qualify leads, enrich contacts,
write outreach, or deliver records. Grok only enriches contacts after Codex has
qualified the article.

## Key Files

- `campaigns/aether-cleaning-az.yaml` - current cleaning-company campaign spec.
- `campaigns/_template.yaml` - template for future vertical/client campaigns.
- `prompts/gemini_discovery.md` - shared Gemini discovery prompt template.
- `prompts/generic_b2b_qualification_rubric.md` - reusable HIGH/MEDIUM/LOW qualification rubric for future campaigns.
- `docs/phase3-closeout-runbook.md` - current end-to-end runbook.
- `skill/aether_daily_routine.md` - full cleaning-company operating routine.
- `skill/gemini_discovery_adapter.md` - short adapter notes for Gemini discovery.
- `skill/grok_enricher.md` - Grok Fast/Expert enrichment prompts and browser workflow.
- `skill/grok_enrichment_adapter.md` - Grok enrichment adapter notes.

## Setup

Install dependencies:

```bash
uv sync
```

Create and load the local env file:

```bash
cp .env.example ~/.aether-pipedrive.env
chmod 600 ~/.aether-pipedrive.env
```

Add your Gemini key to `~/.aether-pipedrive.env`:

```bash
export GEMINI_API_KEY="..."
```

For Pipedrive delivery, keep the existing Pipedrive variables in the same env
file.

For the daily email digest, set the SMTP variables in the same env file:

```bash
export SMTP_HOST="smtp.gmail.com"
export SMTP_PORT="587"
export SMTP_USER="..."
export SMTP_PASSWORD="..."
export LEAD_DIGEST_TO="..."
export LEAD_DIGEST_FROM="..."
```

Load env before running:

```bash
source ~/.aether-pipedrive.env
```

## Campaign Config

The cleaning campaign is configured in `campaigns/aether-cleaning-az.yaml`.
Important discovery fields:

```yaml
discovery:
  provider: gemini_api
  prompt_template: gemini_discovery
  max_sources: 100
  allowed_url_types:
    - article
    - rss_feed
    - atom_feed
    - sitemap
    - permit_listing
    - market_report
  dedupe:
    scope: campaign
    namespace: aether-cleaning-az
  gemini:
    model: gemini-3.1-pro-preview
    use_google_search: true
    temperature: 0.1
  client_prompt: |-
    ...
```

For a new vertical, copy `campaigns/_template.yaml`, set a new `campaign_id`,
replace `client_prompt`, use a new `dedupe.namespace`, and adapt the campaign
qualification examples using `prompts/generic_b2b_qualification_rubric.md`.

## Run The Current Pipeline

For the full cleaning-company routine, follow `skill/aether_daily_routine.md`.
The commands below show the discovery through preview spine; the routine doc
carries the Grok enrichment and Pipedrive push loop.

Create a run:

```bash
RUN_DIR="$(uv run python -m pipeline.cli.init_run --campaign aether-cleaning-az)"
RUN_ID="$(basename "$RUN_DIR")"
printf 'Run directory: %s\nRun id: %s\n' "$RUN_DIR" "$RUN_ID"
```

Run Gemini API discovery:

```bash
uv run python -m pipeline.cli.gemini_discover \
  --campaign aether-cleaning-az \
  --run-id "$RUN_ID" \
  > "$RUN_DIR/artifacts/discover.stdout.json"
```

Validate discovery:

```bash
uv run python -m pipeline.cli.validate_artifact "$RUN_DIR/artifacts/discover.json"
```

Classify and expand discovered URLs into fetch rows.

Safe preview mode, no SQLite dedupe write:

```bash
uv run python -m pipeline.cli.expand_discovered \
  "$RUN_DIR/artifacts/discover.json" \
  --campaign aether-cleaning-az \
  --run-id "$RUN_ID" \
  --no-db \
  > "$RUN_DIR/artifacts/fetch_rows.stdout.json"
```

Intentional local run mode, records final URLs in `db.sqlite`:

```bash
uv run python -m pipeline.cli.expand_discovered \
  "$RUN_DIR/artifacts/discover.json" \
  --campaign aether-cleaning-az \
  --run-id "$RUN_ID" \
  > "$RUN_DIR/artifacts/fetch_rows.stdout.json"
```

Extract the first fetch row:

```bash
FIRST_URL="$(uv run python -c 'import json, sys; print(json.load(open(sys.argv[1]))[0]["url"])' "$RUN_DIR/artifacts/fetch_rows.json")"
uv run python -m pipeline.cli.extract "$FIRST_URL" \
  > "$RUN_DIR/artifacts/article-001.txt"
```

Render the article-assessment prompt:

```bash
uv run python -m pipeline.cli.render_prompt assess \
  --campaign aether-cleaning-az \
  > "$RUN_DIR/prompts/assess.txt"
```

Use Codex/in-session reasoning to read `assess.txt` plus
`article-001.txt`, then write `ExtractedArticle` JSON as a list:

```text
$RUN_DIR/artifacts/extracted_articles.json
```

Run the pattern stage:

```bash
uv run python -m pipeline.cli.run_pattern \
  "$RUN_DIR/artifacts/extracted_articles.json" \
  --campaign aether-cleaning-az \
  --pattern event_signal \
  --run-id "$RUN_ID" \
  > "$RUN_DIR/artifacts/pattern.json"
```

Validate and preview:

```bash
uv run python -m pipeline.cli.validate_artifact "$RUN_DIR/artifacts/pattern.json"

uv run python -m pipeline.cli.preview_delivery \
  "$RUN_DIR/artifacts/pattern.json" \
  --campaign aether-cleaning-az \
  --run-id "$RUN_ID" \
  > "$RUN_DIR/artifacts/preview.json"
```

The Excel preview path is printed in `preview.json` and written under:

```text
$RUN_DIR/previews/
```

## Run Artifacts

Each run writes:

```text
runs/<campaign>/<run_id>/
  prompts/gemini-discovery.txt
  prompts/assess.txt
  transcripts/gemini-discovery-api.json
  transcripts/gemini-discovery.txt
  artifacts/discover.json
  artifacts/classified_sources.json
  artifacts/fetch_rows.json
  artifacts/article-001.txt
  artifacts/extracted_articles.json
  artifacts/pattern.json
  artifacts/preview.json
  previews/*.xlsx
```

`gemini-discovery-api.json` is the raw Gemini API response. Keep it for audit
and parser hardening.

## CLI Reference

```text
pipeline.cli.init_run              create run folder and manifest
pipeline.cli.render_prompt         render Gemini or assessment prompts
pipeline.cli.gemini_discover       call Gemini API and write discover.json
pipeline.cli.parse_gemini_discovery fallback parser for manual Gemini transcripts
pipeline.cli.expand_discovered     classify/expand URLs and write fetch_rows.json
pipeline.cli.extract               fetch and clean one article/page URL
pipeline.cli.run_pattern           score extracted records with event_signal
pipeline.cli.preview_delivery      write Excel preview
pipeline.cli.validate_artifact     validate artifact envelopes
pipeline.cli.email_digest          send or preview the daily Pipedrive Lead digest
```

Older deterministic feed fetching, Grok enrichment, Apollo enrichment, and
Pipedrive delivery code may still exist in the repo. For the cleaning-company
flow, Grok enrichment and Pipedrive push are part of the operating path after
qualification. The end-of-day email report is implemented by
`pipeline.cli.email_digest`.

Preview the digest without SMTP send:

```bash
uv run python -m pipeline.cli.email_digest --daily --print
```

Send the daily digest:

```bash
uv run python -m pipeline.cli.email_digest --daily
```

## Testing

Run tests with the project environment:

```bash
uv run python -m unittest discover tests -v
```

Useful focused tests:

```bash
uv run python -m unittest \
  tests.test_source_discovery \
  tests.test_source_expansion \
  tests.test_gemini_client \
  tests.test_prompts \
  tests.test_spec_v2 \
  tests.test_cli_phase2 \
  -v
```

## Current Status

Phase 3 is API-first discovery validation plus preservation of the full
cleaning-company operating path. Gemini discovery, URL expansion, qualification,
Grok enrichment, Pipedrive push, and the SMTP daily digest are the intended
daily flow.
