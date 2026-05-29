# PRD: Aether Lead Intelligence Pipeline

**Product name:** Aether Lead Intelligence Pipeline  
**Version:** v1.0  
**Date:** 2026-05-29  
**Primary user:** Aether Facility Services owner/operator  
**Business goal:** Automatically identify, qualify, enrich, and push commercial-property cleaning leads into Pipedrive.

---

## 1. Executive Summary

Aether needs an end-to-end lead intelligence system that monitors approved commercial real estate, construction, and development sources listed in a Google Sheet, detects new relevant postings, extracts property lead data using the same qualification logic as the existing Claude scheduled task, enriches missing company/contact data using Grok, and creates qualified leads in Pipedrive through its MCP integration.

The system should prioritize **lead quality over lead volume**. It should only surface Phoenix metro commercial properties that are relevant to Aether's commercial cleaning and facility maintenance sales motion.

The Claude scheduled task already defines the baseline lead standard:

- Include newly announced, topped-out, leased-up, recently opened, or soon-to-open commercial properties.
- Restrict to Phoenix metro cities.
- Restrict to office, retail/shopping center, medical/healthcare, and industrial/warehouse/flex property types.
- Exclude stale, residential-only, market-commentary, opinion, and personnel-move content.
- Never fabricate contact names, emails, phone numbers, or property details.
- Leave uncertain fields blank/null and mark leads for enrichment.

The output and CRM notes should align with Aether's brand voice: direct, asset-minded, operator-focused, and centered on ROI, NOI, asset protection, reliability, and Aether Assurance.

---

## 2. Problem Statement

Aether's current lead discovery process depends on manual source checking and/or a narrow scheduled Claude task. That task works for one RSS feed but does not yet provide a scalable pipeline across all approved sources.

The new system must solve four problems:

1. **Lead discovery is fragmented.** Sources live in a Google Sheet, but there is no full automated workflow for every listed website.
2. **Extraction must be consistent.** Every lead should be filtered and formatted according to the same rules as the Claude task.
3. **Contacts are often missing.** Articles frequently mention properties but not the person responsible for facility or cleaning decisions.
4. **CRM entry is manual.** Qualified leads need to be deduped, enriched, and pushed into Pipedrive with notes and follow-up tasks.

---

## 3. Goals

### 3.1 Primary Goals

The system shall:

1. Read approved source websites and RSS feeds from the Google Sheet.
2. Detect new posts/articles from those sources.
3. Scrape article/page content from approved websites only.
4. Extract property lead data using the Claude-style qualification contract.
5. Reject non-qualified postings.
6. Enrich qualified or near-qualified leads using Grok.
7. Deduplicate against existing CRM records.
8. Create or update Pipedrive organizations, people, leads, notes, and activities through Pipedrive MCP.
9. Write processing status and CRM IDs back to the Google Sheet.
10. Build a feedback loop so accepted/rejected leads improve scoring over time.

### 3.2 Success Criteria

| Metric | Target |
|---|---:|
| Qualified lead precision after human review | >= 85% |
| Duplicate CRM creation rate | <= 5% |
| Extraction completeness for property name/type/status/source URL | >= 80% |
| Average manual review time per lead | <= 2 minutes |
| Source polling reliability | >= 95% successful scheduled runs |
| Pipedrive write accuracy | >= 95% correct org/person/lead mapping |
| Hallucinated contact rate | 0% |

---

## 4. Non-Goals

The v1 system will **not**:

1. Train a custom large language model from scratch.
2. Scrape unapproved sources.
3. Bypass paywalls, authentication, robots controls, or site restrictions.
4. Fabricate contact names, emails, phone numbers, square footage, addresses, or opening dates.
5. Auto-send sales emails.
6. Convert Pipedrive leads into deals automatically.
7. Replace human review for low-confidence leads during v1.

---

## 5. Key Assumptions

1. Aether has permission from the individual websites listed in the source sheet to scrape approved content.
2. The Google Sheet acts as the v1 source registry and operating dashboard.
3. Pipedrive is the source of truth for active sales pipeline records.
4. Grok is used for enrichment after a lead has already passed initial extraction/qualification.
5. The Claude scheduled task rules are the authoritative lead qualification baseline.
6. The Aether brand document is the authoritative writing/voice baseline for notes, summaries, and outreach drafts.

---

## 6. Users and Personas

### 6.1 Primary User: Aether Owner / Operator

**Needs:** High-quality sales leads without spending hours checking construction and real estate sites.  
**Workflow:** Reviews lead queue, approves CRM pushes, contacts decision-makers.  
**Pain point:** Thin leads and duplicate CRM records waste time.

### 6.2 Secondary User: Sales / Operations Assistant

**Needs:** Clear lead context, accurate contact info, and next action.  
**Workflow:** Reviews Pipedrive lead, calls/emails contact, updates outcome.  
**Pain point:** Missing source context and unclear reason the lead matters.

### 6.3 Technical User: Pipeline Maintainer

**Needs:** Observable jobs, clean failure logs, easy source management, configurable rules.  
**Workflow:** Adds/removes sources, checks failed crawls, updates schemas and prompts.  
**Pain point:** Silent failures and untraceable LLM outputs.

---

## 7. Product Scope

## 7.1 Source Registry

The Google Sheet will store approved source websites, feed URLs, crawl settings, and pipeline status.

### Required Source Registry Columns

```csv
source_id,site_name,source_type,source_url,rss_url,crawl_start_url,allowed_paths,city_scope,status,last_checked_at,last_success_at,last_error,notes
```

### Source Types

| Source type | Description |
|---|---|
| `rss` | Source has RSS feed. Poll feed first. |
| `website` | Source has no feed. Crawl approved paths. |
| `hybrid` | Source has feed plus website fallback. |
| `disabled` | Source exists in sheet but is not currently active. |

---

## 7.2 New Posting Detection

The pipeline shall detect new postings through two paths.

### RSS Path

For sources with RSS feeds:

1. Fetch feed.
2. Parse item URL, title, summary, and published date.
3. Process only items newer than `last_success_at` or the source-specific lookback window.
4. Fetch article page.
5. Store raw and cleaned content.
6. Send content to extraction layer.

### Website Agent Path

For sources without RSS feeds:

1. Crawl approved start URL.
2. Respect allowed paths.
3. Prefer pages likely to contain new postings:
   - `/news`
   - `/blog`
   - `/press`
   - `/projects`
   - `/development`
   - `/properties`
   - `/insights`
   - sitemap URLs with recent `lastmod`
4. Identify candidate pages.
5. Fetch and clean page content.
6. Send content to extraction layer.

The LLM website agent should not freely browse the internet. It should operate inside the approved domain/path constraints from the sheet.

---

## 7.3 Lead Qualification Rules

A lead qualifies only if it satisfies the Claude-style criteria.

### Geography

Include properties in:

```text
Phoenix, Scottsdale, Mesa, Tempe, Chandler, Gilbert, Glendale, Peoria, Surprise, Goodyear
```

### Property Types

Include:

| Type | Examples |
|---|---|
| `office` | Office towers, coworking, corporate HQs, build-to-suits |
| `retail` | Shopping centers, plazas, strip malls, big-box openings |
| `medical` | Clinics, urgent care, dental, surgical centers, hospital wings |
| `industrial` | Warehouses, distribution centers, light industrial, flex space |

Exclude:

```text
Residential-only projects
General market commentary
Opinion pieces
Personnel moves
Awards-only articles
Old project summaries
Restaurant-only articles unless part of a larger center
```

### Recency

Include properties that are:

1. Newly announced.
2. Topped out.
3. Leased up.
4. Opening within roughly 90 days.
5. Opened within the last 14 days.

Skip properties opening more than 90 days out unless they are especially large, greater than 100k square feet, and have a named facilities/operations contact already public.

---

## 7.4 Extraction Contract

The extraction step shall convert article/page content into a structured lead object.

The Claude skill requires the core fields below and instructs the system to leave unknown contact fields blank/null rather than inventing them.

### Core Lead Fields

```csv
property_name,address,property_type,opening_date_or_status,contact_name,contact_title,contact_email,contact_phone,description,source_url
```

### Extended Pipeline Fields

```csv
lead_id,source_site,source_type,published_at,first_seen_at,last_seen_at,qualification_status,qualification_reason,confidence_score,enrichment_status,pipedrive_status,pipedrive_org_id,pipedrive_person_id,pipedrive_lead_id,dedupe_key,rejection_reason
```

### JSON Extraction Schema

```json
{
  "property_name": "string|null",
  "address": "string|null",
  "property_type": "office|retail|medical|industrial|null",
  "opening_date_or_status": "string|null",
  "contact_name": "string|null",
  "contact_title": "string|null",
  "contact_email": "string|null",
  "contact_phone": "string|null",
  "description": "string|null",
  "source_url": "string",
  "qualification": {
    "is_qualified": "boolean",
    "city": "string|null",
    "reason": "string",
    "recency_status": "within_14_days_opened|within_90_days_opening|announced|topped_out|leased_up|too_far_out|unknown"
  },
  "confidence": "number",
  "evidence": [
    {
      "field": "string",
      "quote": "string"
    }
  ]
}
```

---

## 7.5 Contact Handling

The system shall follow strict contact rules:

1. Contacts extracted from the source article/page must be explicitly present in that source.
2. If no reliable contact is present, extraction must leave contact fields null.
3. If contact data is missing, the lead may move to Grok enrichment.
4. Enriched contact data must be labeled as enrichment, not original article data.
5. No fabricated names, titles, emails, or phone numbers are allowed.

When the article does not name a relevant contact, the lead should be marked:

```text
LOW_CONFIDENCE - run enrichment
```

---

## 7.6 Enrichment with Grok

Grok enrichment shall run only after a page has been identified as a likely or confirmed property lead.

### Enrichment Inputs

```json
{
  "property_name": "string",
  "address": "string|null",
  "property_type": "string",
  "source_url": "string",
  "article_facts": {},
  "missing_fields": []
}
```

### Enrichment Tasks

Grok should attempt to find:

1. Owner company.
2. Developer company.
3. Property manager company.
4. Tenant/operator, if relevant.
5. Publicly available decision-maker or likely routing contact.
6. Public phone/email if clearly tied to the organization.
7. Evidence URLs for each enriched field.

### Enrichment Output

```json
{
  "owner_company": "string|null",
  "developer_company": "string|null",
  "property_manager_company": "string|null",
  "tenant_or_operator": "string|null",
  "best_contact": {
    "name": "string|null",
    "title": "string|null",
    "email": "string|null",
    "phone": "string|null",
    "confidence": "number",
    "why_this_person": "string|null"
  },
  "evidence": [
    {
      "field": "string",
      "source_url": "string",
      "source_title": "string",
      "reason": "string"
    }
  ],
  "enrichment_confidence": "number",
  "needs_human_review": "boolean"
}
```

### Enrichment Rules

The enrichment layer must:

1. Keep original article facts separate from enriched facts.
2. Preserve source URLs for every enriched field.
3. Reject uncertain contact matches.
4. Flag low-confidence enrichments for human review.
5. Never overwrite higher-confidence article-extracted data without review.

---

## 7.7 Lead Scoring

The system shall assign a score from 0 to 100.

### Suggested Scoring Formula

| Signal | Points |
|---|---:|
| Qualified property type | +20 |
| Phoenix metro location confirmed | +20 |
| Valid recency signal | +20 |
| Property name present | +10 |
| Address or cross-streets present | +5 |
| Contact found in article | +10 |
| Contact found through high-confidence enrichment | +8 |
| Large property or multi-tenant opportunity | +7 |
| Source URL and evidence complete | +5 |
| Missing contact | -10 |
| Unclear opening/status | -10 |
| Possible duplicate | -25 |
| Weak source confidence | -15 |

### Routing Rules

| Score | Action |
|---:|---|
| 80–100 | Eligible for auto-create in Pipedrive |
| 60–79 | Human review queue |
| 40–59 | Store in sheet, do not push to CRM |
| <40 | Reject |

During v1, auto-create should remain disabled until the system demonstrates high precision.

---

## 7.8 Deduplication

Before creating anything in Pipedrive, the system shall search for duplicates.

### Dedupe Keys

Use combinations of:

```text
normalized_property_name
normalized_address
source_url
developer_company
property_manager_company
organization_name
```

### Dedupe Checks

1. Search internal lead database.
2. Search Google Sheet historical leads.
3. Search Pipedrive leads, organizations, and people.

### Dedupe Outcomes

| Outcome | Action |
|---|---|
| Exact duplicate | Update existing record, add note if useful |
| Probable duplicate | Human review |
| No duplicate | Continue to CRM write |
| Existing org but new lead | Attach new lead to existing org |

---

## 7.9 Pipedrive MCP Integration

The pipeline shall use Pipedrive MCP as the CRM write layer.

### Required MCP Tools

The Pipedrive MCP layer should expose:

```json
[
  "pipedrive.search_items",
  "pipedrive.create_or_update_organization",
  "pipedrive.create_or_update_person",
  "pipedrive.create_lead",
  "pipedrive.update_lead",
  "pipedrive.add_note",
  "pipedrive.create_activity",
  "pipedrive.update_custom_fields"
]
```

### Pipedrive Write Sequence

1. Search Pipedrive for duplicates.
2. Create or update organization.
3. Create or update person if contact data exists.
4. Create lead linked to organization and/or person.
5. Add note with article facts and enrichment facts.
6. Create follow-up activity.
7. Write Pipedrive IDs back to the sheet.

### Object Mapping

#### Organization

| Pipedrive field | Pipeline value |
|---|---|
| Name | Property manager, developer, owner, or property name fallback |
| Address | Property address |
| Custom: Property Name | `property_name` |
| Custom: Property Type | `property_type` |
| Custom: Opening Status | `opening_date_or_status` |
| Custom: Source URL | `source_url` |
| Custom: Source Site | `source_site` |

#### Person

| Pipedrive field | Pipeline value |
|---|---|
| Name | `contact_name` or enriched contact |
| Organization | Linked org |
| Email | `contact_email` |
| Phone | `contact_phone` |
| Job Title | `contact_title` |

#### Lead

| Pipedrive field | Pipeline value |
|---|---|
| Title | `Aether | {property_name} | {property_type}` |
| Organization | Pipedrive org ID |
| Person | Pipedrive person ID, if available |
| Source | API / pipeline |
| Custom: Score | `confidence_score` |
| Custom: Source URL | `source_url` |
| Custom: Enrichment Status | `enrichment_status` |

#### Note

The note should include:

```text
Original article facts:
- Property:
- Address:
- Type:
- Opening/status:
- Source:

Enriched facts:
- Owner:
- Developer:
- Property manager:
- Best contact:
- Evidence URLs:

Why this matters for Aether:
- Asset preservation opportunity
- Facility maintenance need
- Recommended next action
```

The note language should use Aether's "Straight Shooter" voice and avoid generic cleaning fluff.

---

## 8. Functional Requirements

### FR1: Source Loading

The system shall read active source rows from the Google Sheet.

**Acceptance criteria:**

1. Disabled sources are skipped.
2. Missing RSS URLs route to website-agent mode.
3. Last checked timestamps are updated after each run.
4. Source-level errors are written back to the sheet.

---

### FR2: RSS Polling

The system shall poll approved RSS feeds and identify new feed items.

**Acceptance criteria:**

1. Only active RSS/hybrid sources are polled.
2. Previously processed URLs are skipped.
3. New feed items are stored with title, URL, published date, source ID, and raw content.
4. Feed failures do not stop the full run.

---

### FR3: Website Crawling

The system shall crawl approved website paths for sources without RSS feeds.

**Acceptance criteria:**

1. Crawler does not leave approved domains.
2. Crawler respects source-level path constraints.
3. Candidate pages are ranked by likelihood of being new postings.
4. Crawl budget is enforced per source.
5. HTML is cleaned into readable article text.

---

### FR4: Candidate Classification

The system shall classify each fetched page as likely lead or non-lead.

**Acceptance criteria:**

1. Non-property pages are rejected.
2. Market commentary and personnel moves are rejected.
3. Candidate classification includes reason and confidence.
4. All rejected pages retain a traceable rejection reason.

---

### FR5: Structured Extraction

The system shall extract lead fields into the required schema.

**Acceptance criteria:**

1. Output conforms to the JSON schema.
2. Unknown fields are null.
3. Contact fields are not fabricated.
4. Description is factual and short.
5. Extraction includes evidence snippets for key fields.

---

### FR6: Qualification Filter

The system shall qualify or reject leads using deterministic rules.

**Acceptance criteria:**

1. Only allowed property types pass.
2. Only Phoenix metro locations pass unless marked for review.
3. Recency rules are enforced.
4. Rejected records include a rejection reason.
5. Qualified records proceed to scoring.

---

### FR7: Grok Enrichment

The system shall enrich missing company/contact fields for qualified or reviewable leads.

**Acceptance criteria:**

1. Enrichment runs only after initial extraction.
2. Enriched fields include evidence.
3. Low-confidence enrichments are not auto-written to Pipedrive.
4. Article facts and enriched facts remain separate.
5. Enrichment status is written back to the sheet.

---

### FR8: Lead Scoring

The system shall score leads from 0 to 100.

**Acceptance criteria:**

1. Score calculation is logged.
2. Lead route is determined by score.
3. Score can be overridden by human reviewer.
4. Score weights are configurable.

---

### FR9: Human Review Queue

The system shall support manual review before CRM write.

**Acceptance criteria:**

1. Review queue shows extracted facts, enriched facts, score, and reason.
2. Reviewer can approve, reject, or request re-enrichment.
3. Reviewer decision is logged.
4. Approved leads move to Pipedrive write queue.

---

### FR10: Pipedrive Sync

The system shall create/update CRM records via Pipedrive MCP.

**Acceptance criteria:**

1. Duplicate search runs before create.
2. Organization is created/updated first.
3. Person is created/updated if contact exists.
4. Lead is linked to organization and/or person.
5. Note is attached with facts and evidence.
6. Follow-up activity is created.
7. Pipedrive IDs are written back to the sheet.

---

### FR11: Feedback Loop

The system shall capture outcomes for future scoring improvement.

**Acceptance criteria:**

1. User can mark lead outcome.
2. Outcomes include accepted, rejected, duplicate, contacted, meeting booked, converted, bad fit.
3. Outcome data is stored for model improvement.
4. Scoring report shows source-level lead quality.

---

## 9. Non-Functional Requirements

### 9.1 Reliability

1. Pipeline should tolerate single-source failures.
2. A failed crawl should not stop other sources.
3. Retries should use exponential backoff.
4. Every run should produce a run log.

### 9.2 Observability

The system shall log:

```text
run_id
source_id
source_url
pages_fetched
pages_rejected
leads_extracted
leads_qualified
leads_enriched
leads_pushed_to_pipedrive
errors
duration
```

### 9.3 Security

1. Store API keys in a secrets manager.
2. Never write secrets to logs or sheets.
3. Limit MCP tools to least-privilege CRM actions.
4. Require human approval for low-confidence CRM writes.
5. Record every CRM write action.

### 9.4 Compliance and Scraping Controls

1. Crawl only approved domains.
2. Enforce per-source crawl limits.
3. Respect source-specific permission notes.
4. Do not bypass access controls.
5. Keep source evidence for all extracted leads.

### 9.5 Performance

Initial v1 targets:

| Process | Target |
|---|---:|
| RSS source processing | < 30 seconds/source |
| Website source processing | < 5 minutes/source |
| Extraction per article | < 20 seconds |
| Enrichment per lead | < 60 seconds |
| Pipedrive write | < 15 seconds/lead |

---

## 10. System Architecture

```text
Google Sheet Source Registry
        ↓
Scheduler
        ↓
Source Loader
        ↓
RSS Poller ─────────────┐
                         ↓
Website Crawler Agent → Candidate Page Store
                         ↓
Content Cleaner
                         ↓
LLM Extraction Layer
                         ↓
Deterministic Qualification Filter
                         ↓
Lead Scoring
                         ↓
Grok Enrichment
                         ↓
Dedupe Engine
                         ↓
Human Review Queue
                         ↓
Pipedrive MCP Sync
                         ↓
Google Sheet Status Update
                         ↓
Outcome Feedback Dataset
```

---

## 11. Data Model

### Lead Record

```json
{
  "lead_id": "uuid",
  "source_id": "string",
  "source_site": "string",
  "source_url": "string",
  "article_title": "string|null",
  "published_at": "datetime|null",
  "first_seen_at": "datetime",
  "property_name": "string|null",
  "address": "string|null",
  "property_type": "office|retail|medical|industrial|null",
  "opening_date_or_status": "string|null",
  "contact_name": "string|null",
  "contact_title": "string|null",
  "contact_email": "string|null",
  "contact_phone": "string|null",
  "description": "string|null",
  "qualification_status": "qualified|review|rejected",
  "qualification_reason": "string",
  "confidence_score": "number",
  "enrichment_status": "not_needed|pending|complete|failed|review",
  "pipedrive_status": "not_ready|pending|created|updated|duplicate|failed",
  "pipedrive_org_id": "string|null",
  "pipedrive_person_id": "string|null",
  "pipedrive_lead_id": "string|null",
  "rejection_reason": "string|null",
  "created_at": "datetime",
  "updated_at": "datetime"
}
```

### Source Record

```json
{
  "source_id": "string",
  "site_name": "string",
  "source_type": "rss|website|hybrid|disabled",
  "source_url": "string",
  "rss_url": "string|null",
  "crawl_start_url": "string|null",
  "allowed_paths": ["string"],
  "status": "active|paused|failed|disabled",
  "last_checked_at": "datetime|null",
  "last_success_at": "datetime|null",
  "last_error": "string|null"
}
```

---

## 12. Review Queue Design

The review queue can live in the Google Sheet for v1.

### Recommended Columns

```csv
review_status,reviewer,reviewed_at,lead_score,property_name,address,property_type,opening_date_or_status,original_contact,enriched_contact,enrichment_confidence,source_url,qualification_reason,rejection_reason,pipedrive_status,pipedrive_lead_id,next_action
```

### Review Actions

| Action | Result |
|---|---|
| Approve | Send to Pipedrive sync |
| Reject | Mark rejected with reason |
| Re-enrich | Send back to Grok enrichment |
| Merge | Attach to existing CRM record |
| Hold | Keep in queue |

---

## 13. Pipedrive Note Template

```text
Aether Lead Intelligence

Property:
{property_name}

Type:
{property_type}

Address:
{address}

Opening / Status:
{opening_date_or_status}

Original Source:
{source_url}

Article-extracted facts:
{article_fact_summary}

Grok-enriched facts:
{enrichment_summary}

Best contact:
{name} — {title}
{email}
{phone}

Confidence:
Lead score: {confidence_score}
Enrichment confidence: {enrichment_confidence}

Why this matters:
Potential facility maintenance opportunity tied to a new or changing commercial asset. Recommended next step: contact ownership/property management to discuss Aether Assurance, service readiness, and asset-preserving cleaning support.
```

This template should stay direct, property-owner focused, and free of generic "cleaning vendor" language.

---

## 14. Machine Learning Feedback Loop

The first release should use rules plus LLM extraction. The ML layer becomes valuable once enough outcomes are collected.

### Labels to Capture

```text
approved
rejected_bad_fit
rejected_duplicate
rejected_wrong_market
rejected_bad_contact
contacted
meeting_booked
proposal_sent
deal_created
deal_won
deal_lost
```

### Features to Store

```text
source_site
property_type
city
opening_status
days_until_opening
has_contact
contact_source
enrichment_confidence
lead_score
article_title_embedding
article_text_embedding
property_size
source_historical_success_rate
reviewer_decision
```

### ML Use Cases

1. Predict likelihood a lead will be approved.
2. Predict likelihood a lead will result in a meeting.
3. Rank sources by lead quality.
4. Adjust scoring weights.
5. Recommend which sources to crawl more or less frequently.

---

## 15. Milestones

### Phase 1: Foundation

**Deliverables:**

1. Source registry reader from Google Sheets.
2. RSS poller.
3. Article fetcher.
4. Clean text extraction.
5. Claude-style extraction schema.
6. Deterministic qualification filter.
7. Output to review sheet.

**Exit criteria:** System can process RSS sources and produce a reviewable lead queue.

---

### Phase 2: Website Agent

**Deliverables:**

1. Approved-domain crawler.
2. Sitemap/news/blog discovery.
3. New posting detection.
4. Candidate ranking.
5. Source-level crawl budgets.

**Exit criteria:** System can process sources without RSS feeds.

---

### Phase 3: Grok Enrichment

**Deliverables:**

1. Enrichment prompt/schema.
2. Evidence-based enrichment output.
3. Contact confidence scoring.
4. Separate article facts vs. enriched facts.
5. Review queue integration.

**Exit criteria:** Missing contact/company fields can be enriched with evidence and confidence.

---

### Phase 4: Pipedrive MCP Sync

**Deliverables:**

1. Duplicate search.
2. Organization upsert.
3. Person upsert.
4. Lead create/update.
5. Note creation.
6. Follow-up activity creation.
7. Sheet status update.

**Exit criteria:** Approved leads can be pushed into Pipedrive without duplicate creation.

---

### Phase 5: Feedback and ML Scoring

**Deliverables:**

1. Outcome capture.
2. Source performance dashboard.
3. Scoring weight tuning.
4. Lightweight lead-ranking model.

**Exit criteria:** Pipeline uses historical outcomes to improve prioritization.

---

## 16. Acceptance Test Scenarios

### Scenario 1: RSS Source Produces a Qualified Lead

**Given** an approved RSS source has a new article about a Phoenix industrial facility opening next month  
**When** the pipeline runs  
**Then** the system extracts the property, qualifies it, scores it, and sends it to review or Pipedrive depending on score.

---

### Scenario 2: Article Has No Contact

**Given** a qualifying article does not name a facilities, operations, owner, developer, or manager contact  
**When** extraction runs  
**Then** contact fields remain null and the lead is flagged for enrichment.

---

### Scenario 3: Residential Project Is Found

**Given** a source article describes a multifamily-only residential development  
**When** extraction and qualification run  
**Then** the lead is rejected with reason `residential_only`.

---

### Scenario 4: Duplicate Lead Exists in Pipedrive

**Given** a property already exists in Pipedrive  
**When** the pipeline attempts CRM sync  
**Then** the system updates the existing record or adds a note instead of creating a duplicate.

---

### Scenario 5: Low-Confidence Enrichment

**Given** Grok finds a possible contact but evidence is weak  
**When** enrichment completes  
**Then** the lead remains in human review and is not auto-created in Pipedrive.

---

### Scenario 6: Website Source Has No RSS Feed

**Given** a source is marked `website`  
**When** the pipeline runs  
**Then** the crawler checks approved paths only and extracts candidate new postings without leaving the approved domain.

---

## 17. Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| LLM extracts false lead | Bad CRM data | Deterministic qualification + human review |
| Grok identifies wrong contact | Bad outreach | Evidence requirement + confidence threshold |
| Duplicate CRM entries | Sales confusion | Pipedrive search before create |
| Source site changes layout | Missed leads | Store raw HTML, add parser fallbacks |
| Crawl overreach | Compliance issue | Approved-domain/path enforcement |
| Low source volume | Few leads | Expand approved source list and tune crawler |
| Sheet becomes messy | Operational drift | Use database as system of record after v1 |
| Prompt drift | Inconsistent extraction | Version prompts and schemas |

---

## 18. Open Questions

1. Should v1 auto-create high-confidence leads, or should all leads require human approval initially?
2. What exact Pipedrive custom fields already exist?
3. Should the Google Sheet remain the long-term dashboard, or should the system move to a database-backed web app after v1?
4. What Grok model and search configuration should be used for enrichment?
5. Which source rows in the sheet are approved for RSS-only versus full website crawling?
6. What lead score threshold should trigger same-day outreach?
7. Should the system create a Pipedrive activity automatically for every approved lead?

---

## 19. Recommended v1 Decision

For the first production version, use this operating mode:

```text
Discovery: automated
Extraction: automated
Qualification: automated rules + LLM evidence
Grok enrichment: automated for missing fields
Pipedrive write: human-approved
Auto-create: disabled until precision is proven
```

Once reviewed leads show at least 85% precision and duplicate creation stays below 5%, enable auto-create only for leads scoring 80+ with no duplicate risk and strong source evidence.

---

## 20. Source Context

This PRD is based on the current Aether brand/voice context and the Claude scheduled lead-generation task. The implementation should preserve:

1. The Aether "Straight Shooter" brand voice.
2. The Claude scheduled task's qualification rules.
3. The no-hallucinated-contact rule.
4. The separation between article-extracted facts and Grok-enriched facts.
5. A human approval gate before low-confidence CRM writes.
