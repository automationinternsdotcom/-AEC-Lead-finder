# Phase 2 General Engine Notes

Phase 2 extends the Phase 1 CampaignSpec work into reusable engine contracts.
The implementation keeps Aether's existing event-signal pipeline as the parity
anchor while adding seams for non-article lead products.

## Implemented

- `CampaignSpecV2` resolved contract with v1 compatibility.
- Run manifests, checkpoints, artifact envelopes, and next-action helpers.
- `discover` stage ahead of fetch, routed to browser/chat by default.
- Gemini discovery prompt rendering for CampaignSpec-driven source discovery.
- Gemini transcript parser that validates, canonicalizes, confidence-filters,
  and deduplicates source URLs before fetch.
- Fetch-compatible discovered URL handoff via `pipeline.cli.fetch_discovered`.
- Pattern stage and pattern-module registry.
- `event_signal` pattern wrapping the current article qualification behavior.
- `entity_aggregation` MVP with deterministic grouping and Codex adjudication
  flags.
- Deterministic scoring helpers for event signals and entity aggregates.
- Browser/chat transcript parsing into enrichment artifact envelopes.
- Persona-aware enrichment cache helpers keyed by campaign, organization, and
  buyer persona.
- Destination abstraction with Excel-compatible preview export.
- Guarded Pipedrive destination wrapper that refuses live delivery without
  explicit approval and still defers CRM writes to the existing push path.

## Explicitly Guarded

- Python does not drive Grok, Gemini, Chrome, or browser sessions directly.
- Browser/chat output is saved as transcript text, then parsed and validated.
- Gemini can discover source URLs, but it cannot qualify, enrich, or deliver
  leads. Deterministic validation and dedup always run before fetch.
- Entity ambiguity creates `needs_codex_adjudication=true`; it does not silently
  enrich or deliver.
- Live Pipedrive delivery is not exposed through the Phase 2 destination adapter.
- `runs/` artifacts stay local and gitignored.

## Verification

The Phase 2 code is covered by unit tests for:

- v1-to-v2 spec resolution
- Gemini source discovery parsing
- discovered URL deduplication and fetch handoff
- run-state setup
- artifact validation
- pattern module behavior
- entity aggregation fixtures
- deterministic scoring
- transcript parsing
- persona-aware cache isolation
- Excel preview output
- guarded Pipedrive delivery behavior

Run:

```bash
PYTHONPATH=/tmp/aether-phase1-deps python3 -m unittest discover tests -v
```
