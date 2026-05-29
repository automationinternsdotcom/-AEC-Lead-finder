# Aether Lead Intelligence Pipeline Implementation Plan

This plan translates `prd.md` into a coding-agent-ready implementation sequence for the current repository.

Current repo state: a Python scaffold already exists with `main.py`, `pipeline/*`, SQLite, RSS discovery, Anthropic extraction, Apollo enrichment, and direct Pipedrive REST deal sync. The PRD requires a broader v1: Google Sheet source registry, Phoenix-only commercial lead qualification, Grok enrichment, human review, dedupe, and Pipedrive MCP lead sync. Treat this as a migration, not a small patch.

## 0. Baseline Audit

Read:

- `prd.md`
- `claude_scheduled.MD`
- `Aether Brand Doc for LLM.docx`
- `main.py`
- `schema.py`
- `pipeline/*.py`

Identify current behavior gaps:

- Uses `sources.yaml`, not Google Sheets.
- Includes Tucson and multifamily logic, but PRD limits v1 to Phoenix metro commercial types.
- Uses Apollo, but PRD specifies Grok enrichment.
- Creates Pipedrive deals via REST, but PRD wants leads via Pipedrive MCP.
- No human review queue yet.
- No PRD extraction schema, evidence model, scoring route, or feedback labels.

## 1. Refactor Data Contracts First

Update `schema.py` to define Pydantic models matching the PRD:

- `SourceRecord`
- `FetchedPage`
- `ExtractionResult`
- `LeadRecord`
- `EnrichmentResult`
- `ReviewDecision`
- `ScoreBreakdown`
- `PipedriveSyncResult`

Hard-code or configure these v1 constants:

- Allowed cities: Phoenix, Scottsdale, Mesa, Tempe, Chandler, Gilbert, Glendale, Peoria, Surprise, Goodyear.
- Allowed property types: `office`, `retail`, `medical`, `industrial`.
- Excluded categories: residential-only, market commentary, opinion, personnel moves, awards-only, old summaries, restaurant-only unless part of a larger center.

## 2. Replace Source Loading With Google Sheets

Create `pipeline/sheets.py`.

Responsibilities:

- Read active source rows from the Google Sheet using the PRD columns.
- Skip `disabled` and inactive rows.
- Normalize `allowed_paths` into a list.
- Write back source status:
  - `last_checked_at`
  - `last_success_at`
  - `last_error`
- Write lead/review rows to the review queue sheet.

Keep `sources.yaml` only as a local dev fallback if useful.

## 3. Expand SQLite Storage

Update `pipeline/db.py` with migration-safe tables:

- `sources`
- `fetched_pages`
- `lead_records`
- `enrichment_records`
- `review_decisions`
- `crm_syncs`
- `runs`
- `outcomes`

Do not store secrets. Store raw/cleaned page content carefully enough for traceability, or store file references if content grows large.

## 4. Discovery Layer

Split `pipeline/fetch.py` into clear responsibilities:

- RSS polling:
  - Fetch feed.
  - Parse URL, title, summary, published date.
  - Skip already-seen URLs.
  - Respect `last_success_at` and source lookback.
- Website crawling:
  - Add `pipeline/crawl.py`.
  - Enforce same-domain and `allowed_paths`.
  - Discover likely pages from `/news`, `/blog`, `/press`, `/projects`, `/development`, `/properties`, `/insights`, and sitemap `lastmod`.
  - Add source-level crawl budget.
  - Never let the crawler browse outside approved constraints.

Use `trafilatura` for article text cleanup as the repo already does.

## 5. Extraction And Qualification

Refactor `pipeline/extract.py`.

Implementation details:

- Use Anthropic tool calling with the PRD JSON extraction schema.
- Require evidence snippets for key fields.
- Contacts must be null unless explicitly present in the source.
- Description must be factual and short.
- Do not let the LLM decide final acceptance alone.

Add `pipeline/qualify.py` for deterministic rules:

- City must be allowed or route to review if ambiguous.
- Property type must be allowed.
- Recency must match PRD rules.
- Rejection reason must be explicit.
- Unknown or thin-but-possible leads route to review, not CRM.

## 6. Lead Scoring

Create `pipeline/score.py`.

Implement configurable scoring weights from the PRD:

- Positive signals: property type, Phoenix metro, recency, property name, address, article contact, enriched contact, large/multi-tenant, evidence.
- Negative signals: missing contact, unclear status, duplicate risk, weak source confidence.

Return both:

- Final score.
- Score breakdown for logs and review sheet.

Routing:

- `80-100`: eligible, but v1 still requires review.
- `60-79`: human review.
- `40-59`: store only.
- `<40`: reject.

## 7. Grok Enrichment

Replace or isolate `pipeline/enrich.py`.

Create a Grok enrichment adapter that:

- Runs only after extraction/qualification.
- Receives PRD enrichment input.
- Returns PRD enrichment output.
- Requires evidence URLs for enriched facts.
- Keeps article facts separate from enrichment facts.
- Never overwrites article-extracted contact fields without review.
- Marks low-confidence results as `review`.

Leave Apollo code unused or behind a separate optional adapter; do not mix it into PRD v1 behavior.

## 8. Human Review Queue

Use Google Sheets for v1 review queue.

Add columns from PRD:

- `review_status`
- `reviewer`
- `reviewed_at`
- `lead_score`
- `property_name`
- `address`
- `property_type`
- `opening_date_or_status`
- `original_contact`
- `enriched_contact`
- `enrichment_confidence`
- `source_url`
- `qualification_reason`
- `rejection_reason`
- `pipedrive_status`
- `pipedrive_lead_id`
- `next_action`

Pipeline behavior:

- New qualified leads go to review.
- Only `review_status=approved` enters Pipedrive sync.
- `reject`, `hold`, `merge`, and `re-enrich` are handled deterministically.

## 9. Dedupe Engine

Create `pipeline/dedupe.py`.

Check in this order:

1. Local SQLite lead history.
2. Google Sheet historical/review rows.
3. Pipedrive MCP search.

Use normalized keys:

- Property name.
- Address.
- Source URL.
- Developer/company/property manager.
- Organization name.

Outcomes:

- Exact duplicate: update existing record or note.
- Probable duplicate: review.
- No duplicate: continue.
- Existing org but new lead: attach to org.

## 10. Pipedrive MCP Sync

Refactor `pipeline/push.py` into a CRM adapter.

The PRD wants Pipedrive MCP tools, not direct REST deals. Implement an interface like:

- `search_items`
- `create_or_update_organization`
- `create_or_update_person`
- `create_lead`
- `update_lead`
- `add_note`
- `create_activity`
- `update_custom_fields`

Then wire the actual MCP client/tool calls available in the runtime. If MCP is unavailable locally, add a dry-run/mock implementation and keep REST code separate from the PRD path.

Important: create Pipedrive leads, not deals, unless the user explicitly changes the CRM design.

## 11. Orchestrator

Rewrite `main.py` around the PRD flow:

1. Start run log.
2. Load active sources from Google Sheets.
3. Discover new RSS and website pages.
4. Fetch and clean content.
5. Classify candidate page.
6. Extract structured lead data.
7. Deterministically qualify.
8. Score.
9. Enrich if needed.
10. Dedupe.
11. Write/update review queue.
12. Sync only approved leads to Pipedrive.
13. Write statuses and IDs back to Google Sheets.
14. Finalize run log.

Keep per-source and per-article error boundaries so one failure does not stop the run.

## 12. Tests

Add focused tests before broad rollout:

- Source loading skips disabled rows.
- RSS dedupe works.
- Website crawler cannot leave allowed domain/path.
- Extraction schema validates null unknown contacts.
- Residential-only article is rejected.
- Phoenix industrial article qualifies.
- Missing contact triggers enrichment.
- Low-confidence enrichment stays in review.
- Duplicate Pipedrive result prevents new lead creation.
- Approved lead writes org/person/lead/note/activity in order.

Use mocked Anthropic, Grok, Sheets, and Pipedrive MCP clients.

## 13. Rollout Mode

Set production defaults to match PRD section 19:

```text
Discovery: automated
Extraction: automated
Qualification: automated rules + LLM evidence
Grok enrichment: automated for missing fields
Pipedrive write: human-approved
Auto-create: disabled
```

Do not enable auto-create until reviewed precision is at least 85% and duplicate creation is under 5%.

