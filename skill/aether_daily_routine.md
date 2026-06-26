# Aether Daily Cleaning Lead Routine

Purpose: run the full cleaning-company lead pipeline from Gemini discovery
through Pipedrive delivery, preserving the Grok enrichment prompts and browser
workflow.

## Full Operating Flow

```text
Gemini API discovers source/article URLs
-> classify and expand URL sources
-> fetch/extract article or page text
-> Codex qualifies into ExtractedArticle JSON
-> qualifying records are stored as run artifacts
-> Grok enriches contacts in Fast mode
-> Grok escalates to Expert mode when Fast is low-confidence
-> enriched leads are pushed to Pipedrive Leads
-> end-of-day email report is sent after all pushes complete
```

Gemini discovers sources only. Codex performs article qualification. Grok
performs decision-maker enrichment. Pipedrive receives only qualified records.

## Setup Check

```bash
source ~/.aether-pipedrive.env
```

Required for discovery:

```bash
env | grep -E '^GEMINI_API_KEY=' | wc -l
```

Required for Pipedrive push:

```bash
env | grep -E '^PIPEDRIVE_(API_TOKEN|DOMAIN|FIELD_ARTICLE_URL)=' | wc -l
```

Expected optional Pipedrive contact fields:

```bash
env | grep -E '^PIPEDRIVE_FIELD_(DATE_POSTED|LEAD_[123])=' | wc -l
```

If the required counts are missing, stop and fix the env file.

## Step 1. Create Run

```bash
RUN_DIR="$(uv run python -m pipeline.cli.init_run --campaign aether-cleaning-az)"
RUN_ID="$(basename "$RUN_DIR")"
printf 'Run directory: %s\nRun id: %s\n' "$RUN_DIR" "$RUN_ID"
```

## Step 2. Gemini Discovery

```bash
uv run python -m pipeline.cli.gemini_discover \
  --campaign aether-cleaning-az \
  --run-id "$RUN_ID" \
  > "$RUN_DIR/artifacts/discover.stdout.json"
```

Validate:

```bash
uv run python -m pipeline.cli.validate_artifact "$RUN_DIR/artifacts/discover.json"
```

Gemini output is untrusted until parsed into `discover.json`.

## Step 3. Classify, Expand, And Dedupe URLs

Safe probe mode:

```bash
uv run python -m pipeline.cli.expand_discovered \
  "$RUN_DIR/artifacts/discover.json" \
  --campaign aether-cleaning-az \
  --run-id "$RUN_ID" \
  --no-db \
  > "$RUN_DIR/artifacts/fetch_rows.stdout.json"
```

Daily run mode, recording final article/page URLs in SQLite:

```bash
uv run python -m pipeline.cli.expand_discovered \
  "$RUN_DIR/artifacts/discover.json" \
  --campaign aether-cleaning-az \
  --run-id "$RUN_ID" \
  > "$RUN_DIR/artifacts/fetch_rows.stdout.json"
```

This writes:

```text
$RUN_DIR/artifacts/classified_sources.json
$RUN_DIR/artifacts/fetch_rows.json
```

## Step 4. Extract And Qualify

For each row in `fetch_rows.json`:

```bash
URL_HASH=...
URL=...

uv run python -m pipeline.cli.extract "$URL" > "$RUN_DIR/artifacts/article-${URL_HASH}.txt"
```

Render the campaign assessment prompt:

```bash
uv run python -m pipeline.cli.render_prompt assess \
  --campaign aether-cleaning-az \
  > "$RUN_DIR/prompts/assess.txt"
```

Codex reads the assessment prompt plus article text and writes one or more
`ExtractedArticle` JSON records to:

```text
$RUN_DIR/artifacts/extracted_articles.json
```

Each record must include the source URL as top-level `url` metadata.

Run the pattern stage:

```bash
uv run python -m pipeline.cli.run_pattern \
  "$RUN_DIR/artifacts/extracted_articles.json" \
  --campaign aether-cleaning-az \
  --pattern event_signal \
  --run-id "$RUN_ID" \
  > "$RUN_DIR/artifacts/pattern.json"
```

Only qualified, campaign-relevant records continue to enrichment.

## Step 5. Grok Enrichment

Use `skill/grok_enricher.md` for the browser workflow and prompts.

For each qualified candidate:

1. Check enrichment cache with `pipeline.cli.cache_lookup`.
2. If address is present, run `pipeline.cli.assessor_lookup` for owner hint.
3. Render and send Grok Fast prompt.
4. Parse Grok response with `pipeline.cli.grok_parse`.
5. If Fast output is generic or low-confidence, switch to Expert and run the
   Expert prompt.
6. Prefer Expert only when it improves high-confidence contacts.
7. Switch Grok back to Fast before the next lead.
8. Save final enrichment envelope as `{"leads": [...]}`.
9. Cache the primary contact with `pipeline.cli.cache_write`.

The final envelope shape must be:

```json
{
  "company_name": "Company",
  "mode": "fast or expert",
  "leads": []
}
```

## Step 6. Push To Pipedrive

For each qualified article and enrichment envelope:

```bash
LEADS="$(jq '.leads // []' /tmp/lead.json)"
PRIMARY="$(echo "$LEADS" | jq '.[0] // null')"
EXTRAS="$(echo "$LEADS" | jq '.[1:]')"

jq -n --argjson article '<extracted_json>' \
      --argjson lead "$PRIMARY" \
      --argjson extras "$EXTRAS" \
      --arg url "$URL" \
  '{article: $article, lead: $lead, extra_contacts: $extras, url: $url}' \
  | uv run python -m pipeline.cli.push > /tmp/push_result.json
```

`pipeline.cli.push` creates or reuses the Pipedrive organization, person, and
Lead, and populates Lead 1 / Lead 2 / Lead 3 custom fields when configured.

## Step 7. Email Daily Report

After all Pipedrive pushes complete, send an end-of-day report with:

- total Gemini candidates
- accepted/rejected discovery counts
- extracted count
- qualified count
- enriched count
- pushed count
- skipped duplicate count
- failed count
- links to the run directory and Excel preview if generated
- top pushed leads with article URL and contact summary

Implementation note: the repo does not yet contain an email sender CLI. Add one
before treating this step as automated.

## Step 8. Final Report

Report:

- Total URLs discovered
- Final fetch rows
- Extracted
- Qualified
- Enriched
- Pushed
- Skipped
- Failed
- Whether the email report was sent

Keep raw Gemini and Grok transcripts under the run directory for audit and
parser hardening.
