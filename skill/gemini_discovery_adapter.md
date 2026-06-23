# Gemini Discovery Adapter

Purpose: Gemini is the dynamic source-discovery provider for Phase 2 campaigns.
It replaces static source lists as the default way to find candidate URLs, while
Python still validates, deduplicates, fetches, extracts, scores, enriches, and
previews afterward.

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
- Model output is treated as untrusted data until `parse_gemini_discovery`
  validates confidence, canonicalizes URLs, and deduplicates candidates.
- Static source feeds may remain as fallback/parity for Aether, but new campaigns
  should start from Gemini discovery.
- If the output cannot be parsed into records, quarantine the transcript and do
  not continue to fetch.

## Flow

Render the discovery prompt:

```bash
uv run python -m pipeline.cli.render_prompt gemini-discovery --max-sources 25
```

Save Gemini's raw response:

```text
runs/<campaign>/<run_id>/transcripts/gemini-discovery.txt
```

Parse and validate the response:

```bash
uv run python -m pipeline.cli.parse_gemini_discovery \
  runs/<campaign>/<run_id>/transcripts/gemini-discovery.txt \
  --run-id "$RUN_ID" \
  > runs/<campaign>/<run_id>/artifacts/discover.json
```

Produce fetch-compatible URL rows:

```bash
uv run python -m pipeline.cli.fetch_discovered \
  runs/<campaign>/<run_id>/artifacts/discover.json \
  > /tmp/urls.json
```
