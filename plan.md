# Aether AEC Lead Finder V2 Architecture Plan

> **Review — Claude, 2026-08-28.** Inline notes are marked `> **REVIEW:**` directly under the text they refer to; the original plan text is unchanged. Overall: the contracts/quarantine/resume design is sound and the compatibility guarantees are the right call. The main gaps are in the rollout section — the dual-run email comparison assumes delivery machinery V1 doesn't have, the monitor time conflicts with the current cron, and the promotion score has no defined scorer.

## Summary

Redesign the canonical Scout pipeline around typed contracts, stable entity IDs, SQLite state, auditable raw/final artifacts, resumable stages, and provider-neutral enrichment. Preserve `uv run scout/pipeline.py`, existing CSV/HTML outputs, GPS-compatible limits, and Apollo opt-in behavior.

Nightly discovery will use the curated 125-site list plus learned RSS. NewsAPI and AEC-focused Apify/Facebook adapters will be manual-only. Invalid records will be quarantined, never silently discarded.

> **REVIEW:** "Learned RSS" is undefined anywhere in this plan — spell out how feeds are discovered (autodiscovery `<link rel="alternate">` on curated sites? model-proposed?), how they're validated before entering nightly rotation, and how dead/spammy feeds get pruned. The 125-site count checks out against `news_websites.csv`. Also, "GPS" is referenced throughout (limits, architecture, Gmail workflow) but never expanded — add one sentence saying what GPS is and where it lives, since this doc will outlive that context.

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

> **REVIEW:** Right direction. One constraint to state: `run-nightly.sh`, the launchd plist, and `nightly-scout.yml` all treat `scout/pipeline.py` as a black box with meaningful exit codes and log output — the wrappers must preserve exit-code semantics and keep per-stage logs landing in `scout/logs/` (or update all three callers in the same change).
- Preserve existing CLI options and defaults: five workers, yesterday as `--since`, `--max-articles 0` for uncapped GPS-compatible operation, two bounded attempts for unresolved decision-maker/contact work, and Apollo spending only with `--apollo-go`.

> **REVIEW:** Note that "opt-in" Apollo is already automatic in production — `.github/workflows/nightly-scout.yml` (~line 71) passes `--apollo-go` on every *scheduled* run. So during the three-day dual run, both versions will bill Apollo nightly unless the shared cache (below) is in place first. Make the shared cache a hard precondition of starting the comparison window, not something that lands alongside it.

- Add `--run-id`, `--resume`, and `--retry-review`. Resume must skip completed artifacts and retry only eligible failed/review records.
- Remove every canonical `scout/` dependency on the legacy `pipeline/` package. Move URL canonicalization, HTTP handling, metadata extraction, and link discovery behind public Scout interfaces.
- Record model token/tool usage and provider calls per attempt. Systemic configuration/database failures stop the run; individual record/provider failures are persisted and quarantined.

### Discovery, qualification, and deduplication

- Implement a common discovery-adapter contract producing canonical candidates with both discovered and resolved URLs, source identity, publication metadata, provider identity, and raw artifact reference.
- Run curated-site discovery and learned RSS nightly. Extract publication dates from structured page metadata before URL patterns; undated candidates enter review rather than being silently excluded.
- Provide manual NewsAPI and Apify commands. Explicitly selected providers fail preflight when credentials are missing.
- Retain GPS provider limits: four provider workers, NewsAPI page setting defaulting to `0` with a hard 100-page ceiling, and Apify capped at 20 results across each of ten AEC query groups.
- Replace theft queries with Arizona opening, lease, occupancy, construction completion, redevelopment, management-change, and expansion signals. Apply deterministic AEC pre-screening before model qualification.

> **REVIEW:** There are no "theft queries" anywhere in this repo — this bullet (and the provider limits above) reads as ported verbatim from the GPS plan. Reword so a future reader doesn't go hunting for code that never existed here. More substantively: NewsAPI and Apify are *net-new* integrations for this repo (no adapter code, no credential names defined), so "retain GPS provider limits" is really "adopt" — the plan should name the expected env vars and note these adapters need building from scratch, not porting.
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

> **REVIEW:** Say *where* migration runs. `results/` is git-ignored and GH Actions only retains artifacts for 14 days, so the complete dated-CSV history exists only on the local machine — migration must run there, or the plan should accept a 14-day horizon. Related: `scout.db` currently lives in the GHA Actions cache between runs; with V1 and V2 keeping separate databases for three days, define the cache-key scheme (per-version keys?) and which database the cache holds after promotion.
- Create synthetic legacy run records per result date. Mark inferred identifiers, source mappings, and provenance explicitly; never overwrite historical files.
- Tag the pre-redesign commit as the frozen V1 baseline and run it from an isolated runtime checkout during comparison. V1 and V2 use the same source snapshot and date window but separate databases/artifacts.
- For three days, run both versions and send two separately labeled emails daily:
  - `Aether AEC Lead Crawl M/D [V1] - …`
  - `Aether AEC Lead Crawl M/D [V2] - …`
- Resolve the union of unresolved V1/V2 identities through one shared Apollo cache, then project identical paid results into both reports to prevent duplicate charges.

> **REVIEW:** The biggest tension in this section: the current V1 scout has **no email delivery at all** (no Gmail/SMTP code exists in `scout/` — `leads_email.html` is reviewed by a human), and V1's `apollo_lead_enrichment.py` calls the API directly with no concept of a shared cache. So sending a labeled `[V1]` email and projecting cached Apollo results into V1's report both require new machinery — which contradicts "frozen V1 baseline" if it's added to the V1 runtime. Recommend stating explicitly that the *comparison harness* (not the frozen V1 checkout) reads both versions' artifacts, performs the shared Apollo resolution, and sends both emails. V1 stays byte-frozen; the harness owns delivery.
- Mirror the production GPS Gmail workflow: deterministic preflight, authenticated-profile validation, exact-subject Sent search, collision-safe body preparation, one Gmail send, and post-send verification requiring exactly one matching message.
- Send to the authenticated user plus the existing Aether digest recipient list, deduplicated. Validate the sender against runtime configuration and include: “Sent by Codex on Jon Schack’s behalf.”
- Add a 5:15 a.m. America/Phoenix monitor that checks both expected Sent messages and alerts the user if either is absent or unverifiable.

> **REVIEW:** Timing conflict — the nightly workflow fires at 13:00 UTC, which is **6:00 a.m. Phoenix**, so a 5:15 a.m. monitor checks for emails that won't be sent for another 45+ minutes and will alert every day. Either move the pipeline cron earlier (e.g. ~11:00 UTC) or the monitor later (e.g. 7:30 a.m. Phoenix, leaving headroom for slow runs). Also specify where the monitor executes (second GHA cron vs. local launchd) and where its alert goes.

## Promotion Gate

Calculate a daily 100-point comparison score:

- 25 points: retention of V1-qualified leads and source-backed V2 additions.
- 25 points: reachable-contact rate versus V1 and correct selected-contact identity alignment.
- 15 points: duplicate-event and organization/person identity quality.
- 15 points: complete record/field provenance and contract validation.
- 10 points: successful required stages, resumability, and artifact integrity.
- 10 points: exactly-once email delivery, recorded usage, and zero duplicate Apollo attempts.

A day is green at 85 points or higher with valid manifests and no silent V1 lead loss—all unmatched V1 leads must appear in quarantine with a reason. Promote V2 automatically when at least two of the three days are green.

> **REVIEW:** The gate needs a defined scorer. Several criteria are judgment calls ("correct selected-contact identity alignment," "source-backed V2 additions," "identity quality") that a deterministic script can't compute — is this an LLM judge, a human checklist, or a mix? For *automatic* promotion to be defensible, each day's score should be emitted as an auditable scorecard artifact (per-criterion evidence, stored under the run's `final/`), and any LLM-judged criterion should say which model and prompt. Otherwise "85 points" is a number nobody can reproduce.

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
