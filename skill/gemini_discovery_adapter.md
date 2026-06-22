# Gemini Discovery Adapter

Purpose: optional browser/chat discovery for campaigns where deterministic source
feeds are not enough.

Phase 2 treats this as a future discovery helper, not a default source.

## Contract

Inputs:
- resolved campaign target profile
- source constraints
- lead pattern type
- max result count

Outputs:
- raw transcript in `runs/<campaign>/<run_id>/transcripts/`
- candidate records in an `artifact_envelope.v1`

Rules:
- Use deterministic fetch/filter first when possible.
- Do not let Gemini output bypass pattern modules.
- Every candidate must include source URL or source description.
- If the output cannot be parsed into records, quarantine the transcript.
