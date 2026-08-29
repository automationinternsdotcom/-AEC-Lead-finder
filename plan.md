# Aether AEC Lead Finder V2 Architecture Plan

## Summary

Redesign the canonical Scout pipeline around typed contracts, stable entity IDs, SQLite state, auditable raw/final artifacts, resumable stages, and provider-neutral enrichment. Preserve `uv run scout/pipeline.py`, existing CSV/HTML outputs, GPS-compatible limits, and Apollo opt-in behavior.

Nightly discovery will use the curated 125-site list plus learned RSS. NewsAPI and AEC-focused Apify/Facebook adapters will be manual-only. Invalid records will be quarantined, never silently discarded.

The existing top-level `pipeline/` package remains temporarily but is isolated and deprecated. Pipedrive behavior is out of scope.

## Implementation Changes

### State, contracts, and artifacts

- Add transactional schema migrations and normalized tables for runs, sources, discovery candidates, lead events, organizations, aliases, people, contact candidates, provider attempts, scores, review items, and the shared Apollo cache.
- Use persistent UUIDs for `run_id`, `lead_event_id`, `organization_id`, and `person_id`. Use deterministic hashes for rediscovered candidate URLs/provider IDs.
- Define Pydantic contracts for discovery candidates, lead judgments, organizations, people, contacts, evidence, scores, and review items.
- Store raw discovery/provider/model responses under `results/<day>/runs/<run_id>/raw/`, with paths and hashes recorded in SQLite. Store validated JSONL under `final/` and a manifest containing configuration, stage states, counts, usage, errors, and artifact hashes.
- Quarantine invalid or incomplete records with raw evidence, validation errors, retry count, and originating stage. Missing data must never be interpreted as rejection or score zero.
- Preserve `raw_leads.csv`, `uncertain_leads.csv`, `contacts.csv`, and `leads_email.html`. Keep existing columns and ordering, then append stable IDs, provenance, verification, run, and record-status fields.

### Pipeline behavior

- Replace subprocess orchestration with an in-process service pipeline whose stages commit status and counters independently. Existing stage scripts become compatibility wrappers around the same services.
- Preserve existing CLI options and defaults: five workers, yesterday as `--since`, `--max-articles 0` for uncapped GPS-compatible operation, two bounded attempts for unresolved decision-maker/contact work, and Apollo spending only with `--apollo-go`.
- Add `--run-id`, `--resume`, and `--retry-review`. Resume must skip completed artifacts and retry only eligible failed/review records.
- Remove every canonical `scout/` dependency on the legacy `pipeline/` package. Move URL canonicalization, HTTP handling, metadata extraction, and link discovery behind public Scout interfaces.
- Record model token/tool usage and provider calls per attempt. Systemic configuration/database failures stop the run; individual record/provider failures are persisted and quarantined.

### Discovery, qualification, and deduplication

- Implement a common discovery-adapter contract producing canonical candidates with both discovered and resolved URLs, source identity, publication metadata, provider identity, and raw artifact reference.
- Run curated-site discovery and learned RSS nightly. Extract publication dates from structured page metadata before URL patterns; undated candidates enter review rather than being silently excluded.
- Provide manual NewsAPI and Apify commands. Explicitly selected providers fail preflight when credentials are missing.
- Retain GPS provider limits: four provider workers, NewsAPI page setting defaulting to `0` with a hard 100-page ceiling, and Apify capped at 20 results across each of ten AEC query groups.
- Replace theft queries with Arizona opening, lease, occupancy, construction completion, redevelopment, management-change, and expansion signals. Apply deterministic AEC pre-screening before model qualification.
- Deduplicate exact canonical URLs first, then deterministic event fingerprints. The model may propose fuzzy groups, but its result must cover every candidate exactly once. Preserve all supporting sources on the retained lead event.

### Enrichment, verification, scoring, and exports

- Port neutral patterns from lead-enrichment: target preparation, raw/final separation, atomic persistence, candidate normalization, deterministic ranking, contact identity realignment, MX/disposable checks, optional cached HTTP verification, and per-field evidence.
- Group research by organization and stable person identity so multiple articles do not repeat the same work.
- Keep sourced professional contact data when external verification is unavailable, marking verification `unknown`; reject malformed, disposable, MX-invalid, mismatched, or unsourced candidates.
- Centralize Apollo behind a persistent `(normalized person, organization)` cache. A billed/null attempt is recorded once, fatal/transient failures remain distinguishable, and phone reveal remains separately authorized.
- Score by `lead_event_id`. Require exactly one valid 0–100 score per submitted ID; retry once, then quarantine incomplete batches without deleting leads.
- Render contacts by `lead_event_id`/`organization_id`, eliminating business-name collisions. CSV and HTML remain projections of validated final state.

### Migration and rollout

- Before migration, create a timestamped database backup. Run an idempotent transaction that imports all current accepted/rejected URL history and every dated lead/contact CSV.
- Create synthetic legacy run records per result date. Mark inferred identifiers, source mappings, and provenance explicitly; never overwrite historical files.
- Tag the pre-redesign commit as the frozen V1 baseline and run it from an isolated runtime checkout during comparison. V1 and V2 use the same source snapshot and date window but separate databases/artifacts.
- For three days, run both versions and send two separately labeled emails daily:
  - `Aether AEC Lead Crawl M/D [V1] - …`
  - `Aether AEC Lead Crawl M/D [V2] - …`
- Resolve the union of unresolved V1/V2 identities through one shared Apollo cache, then project identical paid results into both reports to prevent duplicate charges.
- Mirror the production GPS Gmail workflow: deterministic preflight, authenticated-profile validation, exact-subject Sent search, collision-safe body preparation, one Gmail send, and post-send verification requiring exactly one matching message.
- Send to the authenticated user plus the existing Aether digest recipient list, deduplicated. Validate the sender against runtime configuration and include: “Sent by Codex on Jon Schack’s behalf.”
- Add a 5:15 a.m. America/Phoenix monitor that checks both expected Sent messages and alerts the user if either is absent or unverifiable.

## Promotion Gate

Calculate a daily 100-point comparison score:

- 25 points: retention of V1-qualified leads and source-backed V2 additions.
- 25 points: reachable-contact rate versus V1 and correct selected-contact identity alignment.
- 15 points: duplicate-event and organization/person identity quality.
- 15 points: complete record/field provenance and contract validation.
- 10 points: successful required stages, resumability, and artifact integrity.
- 10 points: exactly-once email delivery, recorded usage, and zero duplicate Apollo attempts.

A day is green at 85 points or higher with valid manifests and no silent V1 lead loss—all unmatched V1 leads must appear in quarantine with a reason. Promote V2 automatically when at least two of the three days are green.

Any database corruption, duplicate Gmail send, or duplicate Apollo charge blocks automatic promotion regardless of score. Otherwise, one failed day may be outvoted. If promotion fails, V1 remains canonical and a comparison report records the failed gates. After promotion, retain the frozen V1 runtime as a rollback option while making the existing canonical command default to V2.

## Test Plan

- Unit-test stable IDs, canonicalization, source/date extraction, typed model validation, review transitions, identity alignment, verification ranking, Apollo caching, and complete-ID scoring.
- Test fuzzy dedup responses that omit, duplicate, or invent IDs; all must fail validation without losing candidates.
- Contract-test curated, RSS, NewsAPI, and Apify adapters using fixtures, including missing credentials and partial provider failures.
- Test migration against copies of the current SQLite database and dated CSVs; rerunning migration must make no further changes.
- Integration-test interruption and resume at every stage, including atomic artifact recovery and retry eligibility.
- Test same-name businesses, multiple events for one organization, renamed organizations, repeated people, null Apollo results, and transient provider errors.
- Test compatibility CSV columns and current HTML rendering.
- Test Gmail preflight, profile mismatch, existing exact subject, uncertain send resolution, duplicate detection, recipient deduplication, disclosure footer, and the two-email monitor.
- Keep all existing tests green and add the Scout self-check suite to CI. Acceptance requires no canonical `scout/` import from the deprecated `pipeline/` package.

## Assumptions

- Grok 4.3 remains the primary judgment/research/scoring model and Grok mini remains available for lower-cost extraction/dedup work.
- Curated and learned-RSS discovery run nightly; NewsAPI and Apify are explicit manual operations.
- Apollo mobile reveal, Pipedrive writes, and deletion of the deprecated pipeline are excluded.
- Runtime credentials, sender identity, and recipient addresses remain outside Git.
