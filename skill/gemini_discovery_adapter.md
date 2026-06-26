# Gemini Discovery Adapter

Purpose: Gemini is the dynamic source-discovery provider for the Aether
campaign closeout. It finds candidate source URLs only. Python still validates,
deduplicates, fetches, extracts, scores, enriches, and previews afterward.

## Contract

Inputs:
- resolved `CampaignSpecV2`
- lead pattern type
- geography / ICP / exclusions
- max source count

Outputs:
- raw Gemini API response in `runs/<campaign>/<run_id>/transcripts/`
- `discover` artifact envelope written by `pipeline.cli.gemini_discover`
- classified source audit written by `pipeline.cli.expand_discovered`
- fetch-compatible URL rows written by `pipeline.cli.expand_discovered`

Rules:
- Gemini only discovers source URLs. It does not qualify leads, enrich contacts,
  or deliver records.
- Every candidate must include an `http` or `https` URL.
- Model output is untrusted data until `gemini_discover` / `parse_gemini_discovery` validates
  confidence, canonicalizes URLs, and deduplicates candidates.
- The shipped campaign is the Aether cleaning vertical. Do not branch into a
  second vertical during this closeout.
- If the output cannot be parsed into records, quarantine the transcript and do
  not continue to fetch.

## API Flow

Run these commands from the repo root.

### 1. Create The Run Folder

```bash
RUN_DIR="$(uv run python -m pipeline.cli.init_run --campaign aether-cleaning-az)"
RUN_ID="$(basename "$RUN_DIR")"
printf '%s\n' "$RUN_DIR"
```

`init_run` creates `prompts/`, `transcripts/`, `artifacts/`, `quarantine/`, and
`previews/`. Do this before redirecting prompt or transcript files.

### 2. Run Gemini API Discovery

Load local secrets first:

```bash
source ~/.aether-pipedrive.env
```

`GEMINI_API_KEY` must be present in the environment.

```bash
uv run python -m pipeline.cli.gemini_discover \
  --campaign aether-cleaning-az \
  --run-id "$RUN_ID" \
  > "$RUN_DIR/artifacts/discover.stdout.json"
```

This writes the rendered prompt, raw API response, extracted response text, and
`artifacts/discover.json`.

Validate the artifact envelope:

```bash
uv run python -m pipeline.cli.validate_artifact "$RUN_DIR/artifacts/discover.json"
```

If parsing fails, copy the response text to quarantine and stop:

```bash
cp "$RUN_DIR/transcripts/gemini-discovery.txt" \
  "$RUN_DIR/quarantine/gemini-discovery-unparsed.txt"
```

Then harden `pipeline/source_discovery.py` or `pipeline/transcript_parser.py`
against the observed live output and add a regression test before retrying.

### 3. Classify/Expand And Produce Fetch Rows

For a safe probe that does not update local `db.sqlite` dedup state:

```bash
uv run python -m pipeline.cli.expand_discovered \
  "$RUN_DIR/artifacts/discover.json" \
  --campaign aether-cleaning-az \
  --run-id "$RUN_ID" \
  --no-db \
  > "$RUN_DIR/artifacts/fetch_rows.stdout.json"
```

This writes `artifacts/classified_sources.json` and `artifacts/fetch_rows.json`.
For an intentional local run that should record final article/page URLs in
SQLite, omit `--no-db`.

## Manual Browser Fallback

Use the manual browser flow only when the API path fails or when comparing
Gemini web output to API output. Render the prompt with
`pipeline.cli.render_prompt gemini-discovery`, paste it into Gemini, save the
verbatim transcript at `$RUN_DIR/transcripts/gemini-discovery.txt`, parse it
with `pipeline.cli.parse_gemini_discovery`, then continue at classify/expand.

## Save The Live Transcript As A Fixture

After the parse succeeds and the transcript/API response contains no secrets,
copy it into fixtures so future parser changes can be tested against real
output:

```bash
cp "$RUN_DIR/transcripts/gemini-discovery.txt" \
  "tests/fixtures/gemini_discovery_transcript_live_${RUN_ID}.txt"
```

Add a focused test for the exact live shape if the parser needed hardening.
