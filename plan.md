# Aether AEC Lead Finder V2 Architecture Plan

## Summary

Redesign the canonical Scout pipeline around typed contracts, stable entity IDs, SQLite state, auditable raw/final artifacts, resumable stages, and provider-neutral enrichment. Preserve `uv run scout/pipeline.py`, existing CSV/HTML outputs, established operator limits, and the explicit Apollo authorization gate.

GPS means the sibling reference repository at `../gps-grok-leadfinder`. It is a behavioral and compatibility baseline, not a runtime dependency of Aether.

Nightly discovery will use the curated 125-site list plus validated RSS/Atom feeds learned from those sites. NewsAPI and AEC-focused Apify/Facebook adapters will be new, manual-only integrations. Invalid records will be quarantined, never silently discarded.

The existing top-level `pipeline/` package remains temporarily but is isolated and deprecated. Pipedrive behavior is out of scope.

## Implementation Changes

### State, contracts, and artifacts

- Add transactional schema migrations and normalized tables for runs, sources, discovered feeds, discovery candidates, lead events, organizations, aliases, people, contact candidates, provider attempts, scores, review items, and the shared Apollo cache.
- Use persistent UUIDs for `run_id`, `lead_event_id`, `organization_id`, and `person_id`. Use deterministic hashes for rediscovered candidate URLs and provider IDs.
- Define Pydantic contracts for discovery candidates, lead judgments, organizations, people, contacts, evidence, scores, and review items.
- Store raw discovery, provider, and model responses under `results/<day>/runs/<run_id>/raw/`, with paths and hashes recorded in SQLite. Store validated JSONL under `final/` and a manifest containing configuration, stage states, counts, usage, errors, and artifact hashes.
- Quarantine invalid or incomplete records with raw evidence, validation errors, retry count, and originating stage. Missing data must never be interpreted as rejection or score zero.
- Preserve `raw_leads.csv`, `uncertain_leads.csv`, `contacts.csv`, and `leads_email.html`. Keep existing columns and ordering, then append stable IDs, provenance, verification, run, and record-status fields.

### Pipeline behavior and compatibility

- Replace subprocess orchestration with an in-process service pipeline whose stages commit status and counters independently. Existing stage scripts become compatibility wrappers around the same services.
- Treat `scout/pipeline.py` as a compatibility boundary for `run-nightly.sh`, `com.aether.nightly.plist`, and `.github/workflows/nightly-scout.yml`. Preserve argument names, stderr stage banners and summaries, nonzero exit on the first systemic stage failure or a zero-lead run, per-stage logs under `scout/logs/`, and wrapper logs under `logs/`. If any part of that contract changes, update and test all three callers in the same change.
- Preserve existing CLI defaults: five workers, yesterday as `--since`, `--max-articles 0` for uncapped operation, and two bounded attempts for unresolved decision-maker/contact work.
- Keep Apollo spending gated by `--apollo-go`. The scheduled GitHub Actions job currently supplies that flag automatically; treat this as explicit workflow authorization, not a hidden pipeline default. During the V1/V2 comparison, neither version may receive `--apollo-go`; the comparison harness and shared cache are the only billable Apollo path.
- Add `--run-id`, `--resume`, and `--retry-review`. Resume must skip completed artifacts and retry only eligible failed or review records.
- Remove every canonical `scout/` dependency on the legacy `pipeline/` package. Move URL canonicalization, HTTP handling, metadata extraction, and link discovery behind public Scout interfaces.
- Record model token/tool usage and provider calls per attempt. Systemic configuration or database failures stop the run; individual record or provider failures are persisted and quarantined.

### Discovery, qualification, and deduplication

- Implement a common discovery-adapter contract producing canonical candidates with both discovered and resolved URLs, source identity, publication metadata, provider identity, and raw artifact reference.
- Learn RSS/Atom feeds only from curated sources by checking HTML `<link rel="alternate">` declarations and a bounded list of same-site conventional feed paths. Do not accept model-proposed feeds in V2.
- Validate a learned feed before activation: it must return successfully, parse as RSS or Atom, contain at least one item with an HTTP(S) article URL, and remain on the curated site's registrable domain unless an explicit redirect/domain alias is recorded. Persist the discovery method, validation time, redirect chain, and health counters.
- Revalidate active feeds nightly. Mark a feed `degraded` after three consecutive fetch/parse failures or 14 days without a new valid item, and `disabled` after seven consecutive failures or 30 inactive days. Quarantine feeds whose recent entries are predominantly off-domain, duplicated, or non-article links; require manual review before reactivation.
- Extract publication dates from structured page metadata before URL patterns. Undated candidates enter review rather than being silently excluded.
- Build NewsAPI and Apify adapters from scratch as manual commands. Use the GPS-compatible environment names `NEWSAPI_AI_API_KEY`, `NEWSAPI_AI_MAX_PAGES`, `NEWSAPI_AI_TIMEOUT_SECONDS`, `APIFY_TOKEN`, `APIFY_FACEBOOK_ACTOR_ID`, and `APIFY_TIMEOUT_SECONDS`; document them in `.env.example`. Explicitly selected providers fail preflight when credentials are missing.
- Adopt the GPS provider ceilings: four provider workers, NewsAPI page configuration defaulting to `0` with a hard 100-page ceiling, and Apify capped at 20 results for each of ten AEC query groups.
- Define Aether-specific Arizona query groups around openings, leases, occupancy, construction completion, redevelopment, management changes, and expansions. Apply deterministic geography and AEC signal screening before model qualification.
- Deduplicate exact canonical URLs first, then deterministic event fingerprints. The model may propose fuzzy groups, but its result must cover every candidate exactly once. Preserve all supporting sources on the retained lead event.

### Enrichment, verification, scoring, and exports

- Port neutral patterns from lead-enrichment: target preparation, raw/final separation, atomic persistence, candidate normalization, deterministic ranking, contact identity realignment, MX/disposable checks, optional cached HTTP verification, and per-field evidence.
- Group research by organization and stable person identity so multiple articles do not repeat the same work.
- Keep sourced professional contact data when external verification is unavailable, marking verification `unknown`; reject malformed, disposable, MX-invalid, mismatched, or unsourced candidates.
- Centralize Apollo behind a persistent `(normalized person, organization)` cache. A billed or null attempt is recorded once, fatal and transient failures remain distinguishable, and phone reveal remains separately authorized.
- Score by `lead_event_id`. Require exactly one valid 0–100 score per submitted ID; retry once, then quarantine incomplete batches without deleting leads.
- Render contacts by `lead_event_id` and `organization_id`, eliminating business-name collisions. CSV and HTML remain projections of validated final state.

### Migration and rollout

- Run the historical migration on the local machine, where the complete git-ignored `results/` history exists. Before migration, create a timestamped database backup and inventory the source files. Run an idempotent transaction that imports all current accepted/rejected URL history and every available dated lead/contact CSV. GitHub Actions' 14-day artifacts are validation inputs only, not the historical source of truth.
- Create synthetic legacy run records per result date. Mark inferred identifiers, source mappings, and provenance explicitly; never overwrite historical files. Emit a migration manifest with input hashes, counts, warnings, and the resulting schema version.
- Tag the pre-redesign commit as the frozen V1 baseline and run it from an isolated runtime checkout during comparison. V1 and V2 use the same source snapshot and date window but separate databases and artifacts.
- Namespace GitHub Actions state so V1 and V2 can never restore each other's database. Use keys shaped like `scout-db-v1-<baseline-sha>-<run-id>` with a baseline-specific restore prefix and `scout-db-v2-<schema-version>-<run-id>` with a schema-specific restore prefix. After promotion, only the V2 namespace is restored or saved; retain the final V1 database with the rollback bundle rather than in the canonical cache path.
- Make the shared Apollo cache and comparison harness hard prerequisites for starting the three-day window. Run frozen V1 and V2 without `--apollo-go`; the harness resolves the union of unresolved identities once, records every paid/null attempt in the shared cache, and projects identical results into version-specific final artifacts before scoring and rendering.
- Keep V1 byte-frozen. The external comparison harness, not either version's checkout, owns Apollo resolution, subject labeling, report delivery, duplicate-send protection, and the daily scorecard.
- For three days, produce and send two separately labeled emails daily:
  - `Aether AEC Lead Crawl M/D [V1] - …`
  - `Aether AEC Lead Crawl M/D [V2] - …`
- Mirror the production GPS Gmail workflow in the harness: deterministic preflight, authenticated-profile validation, exact-subject Sent search, collision-safe body preparation, one Gmail send, and post-send verification requiring exactly one matching message.
- Send to the authenticated user plus the existing Aether digest recipient list, deduplicated. Validate the sender against runtime configuration and include: “Sent by Codex on Jon Schack’s behalf.”
- During comparison, use GitHub Actions as the sole compute scheduler and leave the local launchd template disabled. Move the dual-run workflow to 10:00 UTC (3:00 a.m. America/Phoenix), retain its 90-minute timeout, and run the delivery harness only after both artifact sets reach a terminal state.
- Add a 5:15 a.m. America/Phoenix Codex heartbeat to the delivery thread. It checks for both exact subjects in Gmail Sent plus the two matching final manifests. Missing, duplicate, or unverifiable results alert the user through the Codex task notification; the monitor never attempts a second send.

## Promotion Gate

Emit a versioned `promotion_scorecard.json` under each comparison day's `final/` directory. It must record the scorer version, criterion-level points, evidence references, input artifact hashes, deterministic calculations, judge prompt hash, model identifier, raw judge response path, hard-block status, and final decision.

Calculate a daily 100-point score:

- 25 points for retention of V1-qualified leads and source-backed V2 additions: 20 deterministic points for complete V1 ID accounting and 5 judged points for whether sampled V2-only additions are supported and relevant.
- 25 points for contact quality: 15 deterministic points for reachable-contact rate and contract-valid evidence, plus 10 judged points for sampled selected-contact identity alignment.
- 15 points for identity quality: 5 deterministic points for collision/invariant checks and 10 judged points for sampled fuzzy event, organization, and person grouping.
- 15 deterministic points for complete record/field provenance and contract validation.
- 10 deterministic points for successful required stages, resumability, and artifact integrity.
- 10 deterministic points for exactly-once email delivery, recorded usage, and zero duplicate Apollo attempts.

Use a version-pinned Grok 4.3 rubric judge at temperature zero for the 25 judged points. The prompt accepts only bounded evidence samples and must return criterion IDs, integer points, and evidence-linked reasons. Missing, malformed, or incomplete judge output makes the day non-green and routes it to manual review; it must never be interpreted as zero-quality data. Store the exact prompt, inputs, and raw response so the score can be reproduced and audited. A human reviewer may veto promotion but cannot silently alter points; overrides require a signed reason in the scorecard.

A day is green at 85 points or higher with valid manifests and no silent V1 lead loss; every unmatched V1 lead must appear in quarantine with a reason. Promote V2 automatically when at least two of the three days are green, no scorecard requires manual review, and no human veto is recorded.

Any database corruption, duplicate Gmail send, duplicate Apollo charge, cache namespace crossover, or invalid scorecard blocks automatic promotion regardless of score. Otherwise, one failed day may be outvoted. If promotion fails, V1 remains canonical and the comparison report records the failed gates. After promotion, retain the frozen V1 runtime and final V1 database as a rollback bundle while making the existing canonical command default to V2.

## Test Plan

- Unit-test stable IDs, canonicalization, source/date extraction, typed model validation, review transitions, identity alignment, verification ranking, Apollo caching, and complete-ID scoring.
- Test RSS autodiscovery, conventional-path probing, domain aliasing, activation, degradation, disabling, spam quarantine, and manual reactivation using fixtures.
- Test fuzzy dedup responses that omit, duplicate, or invent IDs; all must fail validation without losing candidates.
- Contract-test curated, RSS, NewsAPI, and Apify adapters using fixtures, including missing credentials and partial provider failures.
- Test migration against copies of the current SQLite database and dated CSVs; rerunning migration must make no further changes.
- Test V1/V2 cache namespaces, cross-restore rejection, the shared Apollo prerequisite, frozen-V1 behavior, and exactly-once projection of paid/null results.
- Integration-test interruption and resume at every stage, including atomic artifact recovery and retry eligibility.
- Test same-name businesses, multiple events for one organization, renamed organizations, repeated people, null Apollo results, and transient provider errors.
- Test compatibility CSV columns, current HTML rendering, CLI arguments, exit codes, stderr summaries, `scout/logs/`, wrapper logs, and all three pipeline callers.
- Test promotion score calculations, evidence linkage, prompt/model version recording, malformed judge output, hard blockers, manual vetoes, and reproducible scorecard serialization.
- Test Gmail preflight, profile mismatch, existing exact subject, uncertain send resolution, duplicate detection, recipient deduplication, disclosure footer, terminal-state gating, and the two-email monitor.
- Keep all existing tests green and add the Scout self-check suite to CI. Acceptance requires no canonical `scout/` import from the deprecated `pipeline/` package.

## Assumptions

- Grok 4.3 remains the primary judgment, research, scoring, and promotion-rubric model; Grok mini remains available for lower-cost extraction and deduplication work.
- Curated and validated RSS discovery run nightly; NewsAPI and Apify are explicit manual operations.
- Apollo mobile reveal, Pipedrive writes, and deletion of the deprecated pipeline are excluded.
- Runtime credentials, sender identity, and recipient addresses remain outside Git.
