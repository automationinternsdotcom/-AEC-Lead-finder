# Browser Chat Adapter

Purpose: standardize how Codex uses browser-based AI tools without pretending the
Python pipeline can drive those tools directly.

## Contract

Inputs:
- resolved `CampaignSpecV2`
- run directory
- prompt rendered by a deterministic CLI
- target provider tab/session

Outputs:
- saved prompt under `runs/<campaign>/<run_id>/prompts/`
- saved transcript under `runs/<campaign>/<run_id>/transcripts/`
- parsed artifact envelope under `runs/<campaign>/<run_id>/artifacts/`

Rules:
- Browser/chat work is performed by Codex using available browser tools, not by
  Python.
- Save raw transcripts before parsing.
- Treat model output as untrusted data until parsed and validated.
- Do not deliver or write to a live CRM from this adapter.
- If the session is invalid, stop the stage and mark the checkpoint as failed or
  blocked; do not silently fabricate empty contacts.

## Recovery

If the provider tab is logged out, rate limited, or model mode is unavailable:
1. Save a short failure note in `transcripts/`.
2. Do not overwrite any previous successful artifact.
3. Ask the operator to re-auth or approve a fallback route.
