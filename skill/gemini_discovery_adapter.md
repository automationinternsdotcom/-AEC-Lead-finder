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
- raw transcript in `runs/<campaign>/<run_id>/transcripts/`
- `discover` artifact envelope parsed by `pipeline.cli.parse_gemini_discovery`
- fetch-compatible URL rows produced by `pipeline.cli.fetch_discovered`

Rules:
- Gemini only discovers source URLs. It does not qualify leads, enrich contacts,
  or deliver records.
- Every candidate must include an `http` or `https` URL.
- Model output is untrusted data until `parse_gemini_discovery` validates
  confidence, canonicalizes URLs, and deduplicates candidates.
- The shipped campaign is the Aether cleaning vertical. Do not branch into a
  second vertical during this closeout.
- If the output cannot be parsed into records, quarantine the transcript and do
  not continue to fetch.

## Manual Browser Flow

Run these commands from the repo root.

### 1. Create The Run Folder

```bash
RUN_DIR="$(uv run python -m pipeline.cli.init_run --campaign aether-cleaning-az)"
RUN_ID="$(basename "$RUN_DIR")"
printf '%s\n' "$RUN_DIR"
```

`init_run` creates `prompts/`, `transcripts/`, `artifacts/`, `quarantine/`, and
`previews/`. Do this before redirecting prompt or transcript files.

### 2. Render The Gemini Prompt

```bash
uv run python -m pipeline.cli.render_prompt gemini-discovery \
  --campaign aether-cleaning-az \
  --max-sources 25 \
  > "$RUN_DIR/prompts/gemini-discovery.txt"
```

Open the file and confirm it asks for JSON only:

```bash
open "$RUN_DIR/prompts/gemini-discovery.txt"
```

### 3. Run Gemini By Hand

1. Open the logged-in Gemini browser UI.
2. Start a fresh chat.
3. Copy the entire contents of `$RUN_DIR/prompts/gemini-discovery.txt`.
4. Paste it into Gemini without edits.
5. Wait for the full response to finish.
6. Copy Gemini's full raw response exactly as shown, including any prose or code
   fences. Do not clean, reformat, or repair the JSON by hand.
7. Save the copied response:

```bash
pbpaste > "$RUN_DIR/transcripts/gemini-discovery.txt"
```

If `pbpaste` is not appropriate, save the response with a text editor at the same
path. The transcript is the regression evidence, so preserve Gemini's output
verbatim.

### 4. Parse The Transcript

```bash
uv run python -m pipeline.cli.parse_gemini_discovery \
  "$RUN_DIR/transcripts/gemini-discovery.txt" \
  --campaign aether-cleaning-az \
  --run-id "$RUN_ID" \
  > "$RUN_DIR/artifacts/discover.json"
```

Validate the artifact envelope:

```bash
uv run python -m pipeline.cli.validate_artifact "$RUN_DIR/artifacts/discover.json"
```

If parsing fails, copy the transcript to quarantine and stop:

```bash
cp "$RUN_DIR/transcripts/gemini-discovery.txt" \
  "$RUN_DIR/quarantine/gemini-discovery-unparsed.txt"
```

Then harden `pipeline/source_discovery.py` or `pipeline/transcript_parser.py`
against the observed live output and add a regression test before retrying.

### 5. Produce Fetch Rows

For a safe probe that does not update local `db.sqlite` dedup state:

```bash
uv run python -m pipeline.cli.fetch_discovered \
  "$RUN_DIR/artifacts/discover.json" \
  --no-db \
  > "$RUN_DIR/artifacts/fetch_rows.json"
```

For an intentional local run that should record discovered URLs in SQLite, omit
`--no-db`.

### 6. Save The Live Transcript As A Fixture

After the parse succeeds and the transcript contains no secrets, copy it into
fixtures so future parser changes can be tested against real output:

```bash
cp "$RUN_DIR/transcripts/gemini-discovery.txt" \
  "tests/fixtures/gemini_discovery_transcript_live_${RUN_ID}.txt"
```

Add a focused test for the exact live shape if the parser needed hardening.
