---
name: aether-leads
description: Use when Jacob asks to run the Aether lead pipeline, find AZ CRE leads, update the Aether sheet, or check for new commercial real estate news for Jordan. Triggers on "aether leads", "run aether", "CRE leads", "aether pipeline", "Jordan's leads", "aether sheet".
---

# Aether CRE Lead Finder

Fetches Arizona commercial real estate news, scores each story for cleaning/facility service opportunity, and writes ranked leads to Google Sheets.

**Client:** Jordan Whitehurst, Aether Facility Services (Phoenix, AZ)
**Sheet ID:** `1DM5qOV3mfPcVbgx_Fj3gVEKS_PPYtAsinIu9Zg5Oxy4`
**Tabs:** `Leads` (filtered results), `Feed History` (full audit log)

## Prerequisites

Requires `gog` CLI authenticated. All gog commands must include `GOG_KEYRING_PASSWORD=aether` as an env prefix. Verify before running:

```bash
GOG_KEYRING_PASSWORD=aether gog auth list --no-input
```

If "No tokens stored", tell Jacob to run `GOG_KEYRING_PASSWORD=aether gog login norgordjacob@gmail.com` first.

## Step 1: Fetch RSS Feeds

Run the feed fetcher script (stdlib Python, fetches all 3 feeds in parallel, ~1-2 seconds):

```bash
python3 ~/.claude/skills/aether-leads/fetch_feeds.py > /tmp/aether_feeds.json
```

Then read `/tmp/aether_feeds.json`. It contains:
- `total_fetched`: raw item count before dedup
- `unique_items`: count after dedup
- `errors`: array of any feed-level errors
- `items`: array of `{source, title, link, published, description}`

If errors are present, note them but continue with whatever items were fetched.

## Step 2: Score and Enrich

For each article, assess whether it represents a real opportunity for commercial cleaning and facility services outreach. These news stories provide Jordan with context on business happenings to find potential clients. Apply Jordan's domain logic:

### What counts as a hit (score HIGH, 70-100)
- New tenant occupancy or lease signing at a commercial property
- Renovation, redevelopment, adaptive reuse, or construction completion
- New business openings (restaurants, bars, coffee shops, cannabis dispensaries, retail)
- Property management company changes or transitions
- Major expansion or buildout (e.g., TSMC north Phoenix)
- New apartment/condo towers reaching lease-up phase
- HOA stand-ups for new communities

### What counts as moderate (score MEDIUM, 40-69)
- Developer land acquisitions (lead is real but timeline is long)
- Industrial/warehouse deals (cleaning opportunity exists but smaller)
- General commercial property transactions without clear physical activity signal

### What to filter out (score LOW, under 40)
- Macro market commentary, trend pieces, "state of the market" articles
- Mortgage rate news, housing market opinions, editorials
- Residential consumer coverage (homebuyers, single-family)
- National stories that mention Arizona in passing
- Rankings, awards, people moves without property activity

### Geographic scope
Arizona only: Goodyear east to Apache Junction, plus Tucson. Penalize stories about other states.

### For each scored lead, extract:
- **article_link**: The article URL
- **article_title**: The article headline (will be displayed as a hyperlink in sheets)
- **date_posted**: Publication date
- **deal_size**: Estimated property/deal value if mentioned in article (e.g., "$45M", "$120M redevelopment", "N/A" if not stated)
- **score**: 0-100 opportunity score
- **priority**: HIGH, MEDIUM, or LOW
- **filter_reason**: One sentence explaining why this article scored the way it did (e.g., "New retail tenants actively leasing in high-growth corridor" or "Macro market commentary with no specific property activity")
- **lead_1_name**: Best-guess name of a specific decision maker (search for actual people, not just roles). Use format "FirstName LastName, Title at Company". If no specific person can be identified, use "Property Manager at [Company]" as placeholder.
- **lead_1_source**: Where the lead info came from or a Google search query to find/verify them
- **lead_2_name**: Second potential contact (different role or company), same format
- **lead_2_source**: Source/search query for lead 2
- **lead_3_name**: Third potential contact, same format
- **lead_3_source**: Source/search query for lead 3
- **service_angle**: Why Aether should reach out, in one sentence using their voice (asset preservation, not "cleaning services")

Sort results by score descending. Keep the top 20-25 leads (score >= 40).

## Step 3: Write to Google Sheets

### Sheet 1: "Leads" (filtered results)

#### Clear existing data (preserve header row)

```bash
GOG_KEYRING_PASSWORD=aether gog sheets clear 1DM5qOV3mfPcVbgx_Fj3gVEKS_PPYtAsinIu9Zg5Oxy4 "Leads!A2:Z1000" --no-input -a norgordjacob@gmail.com
```

#### Write header row (if tab is new)

```bash
GOG_KEYRING_PASSWORD=aether gog sheets update 1DM5qOV3mfPcVbgx_Fj3gVEKS_PPYtAsinIu9Zg5Oxy4 "Leads!A1" --values-json '[["Article","Date Posted","Deal Size","Score","Priority","Filter Reason","Lead 1","Lead 1 Source","Lead 2","Lead 2 Source","Lead 3","Lead 3 Source","Service Angle"]]' --input USER_ENTERED --no-input -a norgordjacob@gmail.com
```

#### Write leads

Build a JSON 2D array. For the Article column, use a Google Sheets HYPERLINK formula: `=HYPERLINK("url","title")`

Column order:
```
Article (hyperlink) | Date Posted | Deal Size | Score | Priority | Filter Reason | Lead 1 | Lead 1 Source | Lead 2 | Lead 2 Source | Lead 3 | Lead 3 Source | Service Angle
```

```bash
GOG_KEYRING_PASSWORD=aether gog sheets update 1DM5qOV3mfPcVbgx_Fj3gVEKS_PPYtAsinIu9Zg5Oxy4 "Leads!A2" --values-json '<JSON_ARRAY>' --input USER_ENTERED --no-input -a norgordjacob@gmail.com
```

### Sheet 2: "Feed History" (full audit log)

This sheet keeps ALL articles from every run, both filtered and unfiltered, so we can audit the filtering logic.

#### Write header row (if tab is new)

```bash
GOG_KEYRING_PASSWORD=aether gog sheets update 1DM5qOV3mfPcVbgx_Fj3gVEKS_PPYtAsinIu9Zg5Oxy4 "'Feed History'!A1" --values-json '[["Run Date","Article","Date Posted","Source Feed","Score","Priority","Filter Reason","Included in Leads"]]' --input USER_ENTERED --no-input -a norgordjacob@gmail.com
```

#### Append all articles (never clear this sheet, it accumulates history)

For EVERY article from the feed (not just filtered ones), append a row with:
- Run Date: today's date
- Article: `=HYPERLINK("url","title")`
- Date Posted: publication date
- Source Feed: which RSS feed it came from (az-cre, phoenix-dev, tucson-cre)
- Score: the assigned score
- Priority: HIGH/MEDIUM/LOW
- Filter Reason: why it scored this way
- Included in Leads: "Yes" or "No"

```bash
GOG_KEYRING_PASSWORD=aether gog sheets append 1DM5qOV3mfPcVbgx_Fj3gVEKS_PPYtAsinIu9Zg5Oxy4 "'Feed History'!A1" --values-json '<JSON_ARRAY>' --input USER_ENTERED --no-input -a norgordjacob@gmail.com
```

### Verify

```bash
GOG_KEYRING_PASSWORD=aether gog sheets get 1DM5qOV3mfPcVbgx_Fj3gVEKS_PPYtAsinIu9Zg5Oxy4 "Leads!A1:E5" --no-input -a norgordjacob@gmail.com
```

## Output

After writing, report to Jacob:
- How many leads were written to the Leads sheet
- How many total articles were logged to Feed History
- The top 3 leads with article title, deal size, score, and service angle
- Any feed fetch errors
- Link to the sheet: https://docs.google.com/spreadsheets/d/1DM5qOV3mfPcVbgx_Fj3gVEKS_PPYtAsinIu9Zg5Oxy4/edit

## Notes

- Jordan's brand voice is "Straight Shooter": direct, asset-minded, ROI/NOI-focused. Use terms like "asset preservation" and "strategic partner", not "cleaning" or "janitor".
- Sales cycle is long. A lead that seems early-stage (land acquisition, construction start) is still valuable since it may take 1-2 years to convert.
- The existing prospect lists in the Claude project have the ICP: locally-owned 20-600 unit multifamily properties.
- For contact enrichment: Claude provides best-guess leads based on article context. For verified contact info (emails, phone numbers, LinkedIn profiles), a dedicated enrichment tool like Apollo.io, Vayne.io, or LinkedIn Sales Navigator should be used as a follow-up step.
- If gog commands fail, fall back to exporting the leads as a CSV and tell Jacob to import manually.
