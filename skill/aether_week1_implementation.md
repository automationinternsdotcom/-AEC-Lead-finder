# Aether Lead Pipeline — Week 1 Implementation

**Build window:** Wed 2026-05-20 → Mon 2026-05-25
**Demo:** Mon 6pm

---

## 1. What we're building

Daily 7am cron reads ~15 AZ commercial real-estate news sources, extracts new opportunities (openings, developments, acquisitions, expansions), enriches with 1 decision-maker via Apollo, pushes Org + Person + Deal into Pipedrive under a new "AI-Sourced Leads" pipeline.

---

## 2. Architecture

Deterministic ETL. LLM only at extraction step.

```
GitHub Actions cron (7am MST daily)
  ├─ fetch.py     RSS per source → new URLs (diff vs SQLite seen_urls)
  ├─ extract.py   HTML → trafilatura → 1 Haiku call → pydantic schema
  ├─ filter       drop if az_relevant=false OR (signal=other AND conf<0.6)
  ├─ rate calc    rates.yaml × sqft/units/$ → est_deal_size_usd
  ├─ enrich.py    Apollo people_search(domain, titles) → top 1 lead
  ├─ push.py      Pipedrive MCP → Org + Person + Deal w/ dedup
  └─ digest       append row to daily_digest.md, commit db.sqlite back
```

---

## 3. Stack

| Layer | Choice |
|---|---|
| Language | Python 3.12 |
| Pkg mgr | `uv` |
| HTTP | `httpx` |
| RSS | `feedparser` |
| HTML clean | `trafilatura` |
| Schema | `pydantic` |
| LLM | Anthropic SDK, model `claude-haiku-4-5-20251001` |
| Enrich | Apollo.io free-tier API |
| CRM push | Pipedrive MCP (fallback REST if MCP unstable) |
| Storage | SQLite file, committed back to repo per run |
| Host | GitHub Actions cron |
| Secrets | GHA secrets + local `.env` |

---

## 4. Repo Layout

```
aether-leads/
├── sources.yaml           # 15 RSS endpoints
├── rates.yaml             # janitorial rate table
├── pipeline/
│   ├── __init__.py
│   ├── fetch.py
│   ├── extract.py
│   ├── enrich.py
│   └── push.py
├── main.py                # orchestrator
├── schema.py              # pydantic models
├── db.sqlite              # committed back each run
├── daily_digest.md        # appended each run
├── .github/workflows/
│   └── daily.yml
├── pyproject.toml
├── .env.example
└── README.md
```

---

## 5. Data Schema

### SQLite tables

```sql
CREATE TABLE sources (
  name TEXT PRIMARY KEY,
  method TEXT,
  endpoint TEXT,
  enabled INTEGER DEFAULT 1
);

CREATE TABLE seen_urls (
  url_hash TEXT PRIMARY KEY,
  url TEXT,
  source TEXT,
  first_seen_at TEXT,
  title TEXT,
  status TEXT
);

CREATE TABLE articles (
  url_hash TEXT PRIMARY KEY,
  extracted_json TEXT,
  est_deal_size_usd INTEGER,
  pipedrive_deal_id INTEGER,
  pushed_at TEXT
);

CREATE TABLE runs (
  started_at TEXT PRIMARY KEY,
  finished_at TEXT,
  sources_ok INTEGER,
  sources_failed INTEGER,
  articles_seen INTEGER,
  articles_new INTEGER,
  articles_az INTEGER,
  articles_pushed INTEGER,
  duration_sec INTEGER
);
```

### Extraction schema (pydantic)

```python
class ExtractedArticle(BaseModel):
    title: str
    published_date: date | None
    summary_2sent: str
    signal_type: Literal["opening","development","acquisition",
                         "expansion","lease","construction","other"]
    company_name: str
    company_domain_guess: str | None
    property_type: Literal["office","industrial","multifamily",
                           "retail","medical","mixed","other"]
    address: str | None
    city: str | None
    square_footage: int | None
    dollar_value: int | None
    unit_count: int | None
    az_relevant: bool
    confidence: float
```

---

## 6. Deal-Size Calculation

Deterministic, not LLM:

```python
if sqft and property_type in rates:
    return sqft * rates[property_type]
elif unit_count:
    return unit_count * 120
elif dollar_value:
    return dollar_value * 0.002
else:
    return None
```

`rates.yaml`:
```yaml
office:       2.25
industrial:   0.55
multifamily:  100      # per unit, not sqft
retail:       1.50
medical:      3.00
mixed:        1.75
other:        1.00
```

---

## 7. Pipedrive Schema

### New pipeline
- Name: **AI-Sourced Leads**
- Stages: `Auto-Detected` → `Reviewed` → `Contacted` → `Qualified` → `Won/Lost`
- All pushes land in stage 1.

### Org (dedup by domain)
- Standard: name, domain, address, city
- Custom: `source_url`, `signal_type`, `first_seen_at`, `article_age_days`

### Person (dedup by email)
- Standard: name, email, phone, title, org_id
- Custom: `linkedin_url`, `seniority`, `enrichment_source`

### Deal (dedup by article_url)
- title: `{company} — {signal_type} — {city}`
- value: `est_deal_size_usd`
- pipeline: `AI-Sourced Leads`, stage: `Auto-Detected`
- org_id, person_id linked
- Custom: `article_url` (unique), `article_title`, `deal_size_basis`, `lead_gap`, `article_age_days`

---

## 8. Tier 1 Sources (~15, audit Day 1)

1. Bisnow — Phoenix CRE
2. AZ Big Media — Commercial RE
3. AZ Big Media — Site Selection
4. AZBEX (Arizona Builder's Exchange)
5. Arizona Commerce Authority — News
6. ABC15 Arizona — Business
7. Arizona Daily Star — Tucson Business
8. Arizona Digital Free Press
9. Arizona Progress Gazette
10. AZ Big Media — AZRE
11. Business Facilities — Southwest
12. ARIZCC blog
13. Avison Young — Phoenix
14. BuildCentral blog
15. Arizona Multihousing Association

Each entry in `sources.yaml`:
```yaml
- name: bisnow_phoenix
  method: rss
  endpoint: https://www.bisnow.com/phoenix/rss
  enabled: true
```

---

## 9. Filter Rules

Hard-drop:
- `az_relevant == false`
- `signal_type == "other"` AND `confidence < 0.6`

No deal-size floor. All else passes → ranked by `est_deal_size_usd` desc.

---

## 10. Failure Handling

Hard-fail. No try/except. Crash on first error → GHA red X → fix and re-run.

End-of-run stdout summary captured by GHA logs:
```
2026-05-25 07:03 | sources:15 ok:15 fail:0
                 | articles_seen:42 new:11 az:7 pushed:7 lead_gap:2
                 | duration:3m41s
```

---

## 11. Output

**Primary:** Pipedrive Org + Person + Deal records.

**Secondary:** `daily_digest.md` appended each run, committed to repo:

```
## 2026-05-25
<url>Title here</url> | 2026-05-23 | $48,000 | Jane Doe / Property Mgr / Acme Realty / jane@acme.com / 602-555-0100 / linkedin.com/in/janedoe | N/A | N/A
```

---

## 12. Day-1 Blocking Verifications (Wed AM)

Before any code:

1. **Pipedrive MCP** — confirm Jon's installed server, test create-deal end-to-end. If unstable → fall back to Pipedrive REST API.
2. **Apollo free tier** — verify API access (not just web extension). If web-only → ship w/ leads=N/A, Jordan manual lookup post-push.
3. **Anthropic API key** — provisioned, billing enabled.
4. **Pipedrive API token** — issued from Jon's spare seat.

---

## 13. Build Sequence

| Day | Date | Deliverable |
|---|---|---|
| 1 | Wed 5/20 | Repo + GHA skeleton + secrets + Day-1 verifications + `sources.yaml` audit |
| 2 | Thu 5/21 | `fetch.py` + `extract.py` w/ 20-article test set |
| 3 | Fri 5/22 | `enrich.py` + `rates.yaml` + AZ filter |
| 4 | Sat 5/23 | `push.py` + Pipedrive custom pipeline + `daily_digest.md` |
| 5 | Sun 5/24 | `main.py` orchestrator + 7-day backfill run → Pipedrive populates |
| 6 | Mon 5/25 | Cron enabled 7am MST, final QA, 6pm demo |

---

## 14. Cost

| Item | Monthly |
|---|---|
| GitHub Actions | $0 |
| Anthropic Haiku 4.5 | ~$1.50 |
| Apollo free tier | $0 |
| Pipedrive | $0 marginal |
| GitHub private repo | $0 |
| **Total** | **~$1.50/mo** |

---

## 15. Pre-Build Questions for Jordan

Send before Wed AM:

1. Pipedrive custom-pipeline name OK as **"AI-Sourced Leads"**?
2. 7am MST or AZ-local? (AZ doesn't do DST — matters in Nov.)
3. Email digest of daily run summary wanted, or just Pipedrive?
4. Phase 1 = push-only, no outreach copy yet. Confirm.

---

## 16. Comms

- Group text: Jon + Jordan + Jacob + RT
- Code review: Jacob → RT before commits to main
- Demo: Mon 5/25 6pm at Workuity
- Async: RT posts EOD progress in group text
