# Grok Enrichment Adapter

Purpose: browser/chat enrichment for 1-3 decision-maker contacts after a lead has
survived deterministic filtering.

This adapter complements `skill/grok_enricher.md`; it states the Phase 2 artifact
contract.

## Inputs

- `company_name`
- `city`
- `description`
- `owner_entity`
- `article_summary`
- `article_url`
- resolved campaign buyer persona and outreach angle

Render prompts with:

```bash
uv run python -m pipeline.cli.render_prompt grok-fast --company-name "$COMPANY"
```

## Outputs

Save the raw browser response to:

```text
runs/<campaign>/<run_id>/transcripts/<candidate_id>.grok.txt
```

Parse it with:

```bash
uv run python -m pipeline.cli.parse_transcript \
  runs/<campaign>/<run_id>/transcripts/<candidate_id>.grok.txt \
  --company-name "$COMPANY" \
  --mode fast \
  --run-id "$RUN_ID"
```

The parser emits an `artifact_envelope.v1` with `stage="enrich"`.

## Escalation

Escalate Fast to Expert only when:
- no person was parsed,
- the parsed person is generic,
- contact data is hedged or incomplete enough to be operationally risky.

Do not push contacts directly from the browser transcript. Parse, validate, preview,
then deliver through the destination layer.
