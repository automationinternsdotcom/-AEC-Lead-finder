# Phase 3 Closeout Runbook

Status: ready for the human-operated live Gemini probe.

This phase verifies the new discovery-first engine against live Gemini output
without enabling live Pipedrive delivery. The required path ends at Excel
preview:

```text
Gemini discovery -> parse -> fetch_discovered -> extract -> ExtractedArticle -> pattern -> Excel preview
```

The repo does not call Gemini directly. A human must run the prompt in the
logged-in Gemini browser UI and save the raw response verbatim.

## 1. Create A Run

From the repo root:

```bash
RUN_DIR="$(uv run python -m pipeline.cli.init_run --campaign aether-cleaning-az)"
RUN_ID="$(basename "$RUN_DIR")"
printf 'Run directory: %s\nRun id: %s\n' "$RUN_DIR" "$RUN_ID"
```

This creates:

```text
runs/aether-cleaning-az/<run_id>/
  artifacts/
  delivery/
  previews/
  prompts/
  quarantine/
  transcripts/
```

## 2. Render The Gemini Discovery Prompt

```bash
uv run python -m pipeline.cli.render_prompt gemini-discovery \
  --campaign aether-cleaning-az \
  --max-sources 25 \
  > "$RUN_DIR/prompts/gemini-discovery.txt"
```

Open the prompt:

```bash
open "$RUN_DIR/prompts/gemini-discovery.txt"
```

Confirm the prompt asks Gemini only for source URLs and JSON. Do not ask Gemini
to qualify leads, enrich contacts, or write outreach copy.

## 3. Human Gemini Browser Step

1. Open Gemini in the browser account that is already logged in.
2. Start a new chat so no previous context leaks into the run.
3. Copy the entire prompt from `$RUN_DIR/prompts/gemini-discovery.txt`.
4. Paste the prompt into Gemini exactly as rendered.
5. Wait until Gemini finishes the full answer.
6. Copy Gemini's entire response exactly as shown. Include code fences,
   preambles, trailing notes, malformed JSON, or anything else Gemini emitted.
7. Save it verbatim:

```bash
pbpaste > "$RUN_DIR/transcripts/gemini-discovery.txt"
```

If you use a text editor instead, save to the same path. Do not repair the JSON
manually; the goal is to test the parser against real output.

## 4. Parse And Validate Discovery

```bash
uv run python -m pipeline.cli.parse_gemini_discovery \
  "$RUN_DIR/transcripts/gemini-discovery.txt" \
  --campaign aether-cleaning-az \
  --run-id "$RUN_ID" \
  > "$RUN_DIR/artifacts/discover.json"
```

```bash
uv run python -m pipeline.cli.validate_artifact "$RUN_DIR/artifacts/discover.json"
```

Review `metadata.rejected` in `discover.json`. Expected rejection reasons
include invalid source shape, invalid URL scheme, duplicate URL, and confidence
below threshold.

If parsing fails:

```bash
cp "$RUN_DIR/transcripts/gemini-discovery.txt" \
  "$RUN_DIR/quarantine/gemini-discovery-unparsed.txt"
```

Stop there. Harden the parser against the observed output, add a regression
test, rerun tests, and then retry parsing the same transcript.

## 5. Convert Discovery To Fetch Rows

Safe probe mode, with no SQLite dedup write:

```bash
uv run python -m pipeline.cli.fetch_discovered \
  "$RUN_DIR/artifacts/discover.json" \
  --no-db \
  > "$RUN_DIR/artifacts/fetch_rows.json"
```

Intentional local run mode, with discovered URLs recorded in `db.sqlite`:

```bash
uv run python -m pipeline.cli.fetch_discovered \
  "$RUN_DIR/artifacts/discover.json" \
  > "$RUN_DIR/artifacts/fetch_rows.json"
```

Use the safe probe mode unless you explicitly want this run to affect local
dedup state.

## 6. Extract Article Text

The current extract CLI handles one URL at a time. Pick one or more article URLs
from `fetch_rows.json`.

To extract the first URL:

```bash
FIRST_URL="$(uv run python -c 'import json, sys; print(json.load(open(sys.argv[1]))[0]["url"])' "$RUN_DIR/artifacts/fetch_rows.json")"
uv run python -m pipeline.cli.extract "$FIRST_URL" \
  > "$RUN_DIR/artifacts/article-001.txt"
```

If extraction fails for the first URL, choose the next article-like URL from
`fetch_rows.json` and repeat. URL liveness and 404 handling belong to this
fetch/extract boundary, not to the Gemini parser.

## 7. Build ExtractedArticle JSON

Render the assessment prompt:

```bash
uv run python -m pipeline.cli.render_prompt assess \
  --campaign aether-cleaning-az \
  > "$RUN_DIR/prompts/assess.txt"
```

Human/Codex step:

1. Open `$RUN_DIR/prompts/assess.txt`.
2. Open `$RUN_DIR/artifacts/article-001.txt`.
3. Give the assessment prompt plus the article text to the in-session judgment
   operator.
4. Ask for `ExtractedArticle` JSON only.
5. Add the source URL used for extraction as a top-level `url` field on that
   same record. This is stage metadata, not an LLM judgment; copy it from
   `fetch_rows.json` / `$FIRST_URL`.
6. Save the result as a JSON list at:

```text
$RUN_DIR/artifacts/extracted_articles.json
```

The file must look like:

```json
[
  {
    "title": "Article title",
    "published_date": "2026-06-24",
    "summary_2sent": "Two sentence summary.",
    "signal_type": "construction",
    "company_name": "Example Company",
    "company_domain_guess": "example.com",
    "property_type": "multifamily",
    "address": null,
    "city": "Phoenix",
    "square_footage": null,
    "dollar_value": null,
    "unit_count": null,
    "az_relevant": true,
    "confidence": 0.8,
    "priority": "high",
    "filter_reason": "Short audit trail.",
    "service_angle": "Campaign-voice reason to reach out.",
    "url": "https://example.com/source-article"
  }
]
```

## 8. Run Pattern And Preview

```bash
uv run python -m pipeline.cli.run_pattern \
  "$RUN_DIR/artifacts/extracted_articles.json" \
  --campaign aether-cleaning-az \
  --pattern event_signal \
  --run-id "$RUN_ID" \
  > "$RUN_DIR/artifacts/pattern.json"
```

```bash
uv run python -m pipeline.cli.validate_artifact "$RUN_DIR/artifacts/pattern.json"
```

```bash
uv run python -m pipeline.cli.preview_delivery \
  "$RUN_DIR/artifacts/pattern.json" \
  --campaign aether-cleaning-az \
  --run-id "$RUN_ID" \
  > "$RUN_DIR/artifacts/preview.json"
```

The Excel file path is printed in `preview.json` and written under
`$RUN_DIR/previews/`.

## 9. Save Regression Evidence

After the live Gemini transcript parses successfully and contains no secrets:

```bash
cp "$RUN_DIR/transcripts/gemini-discovery.txt" \
  "tests/fixtures/gemini_discovery_transcript_live_${RUN_ID}.txt"
```

If parser hardening was required, add a test that reads this fixture and proves
the live shape stays supported. Keep the existing synthetic bad-output fixtures;
the live run complements them, it does not replace them.

## 10. Completion Checklist

- Live Gemini transcript saved verbatim.
- `discover.json` validates and contains expected accepted/rejected records.
- `fetch_rows.json` exists.
- At least one live source URL was extracted into article text.
- `extracted_articles.json` validates indirectly through `run_pattern`.
- `pattern.json` validates.
- Excel preview was written.
- Live transcript fixture and any parser hardening tests were added.
- `uv run python -m unittest discover tests -v` passes.
- No live Pipedrive delivery was run.
