---
name: aether-leads
description: Use when Jacob asks to run the Aether lead pipeline, find AZ CRE leads, update the Aether sheet, or check for new commercial real estate news for Jordan. Triggers on "aether leads", "run aether", "CRE leads", "aether pipeline", "Jordan's leads", "aether sheet".
---

# Aether CRE Lead Finder

Fetches Arizona commercial real estate news, scores each story for cleaning/facility service opportunity, and writes ranked leads to Google Sheets.

**Client:** Jordan Whitehurst, Aether Facility Services (Phoenix, AZ)
**Sheet ID:** `1DM5qOV3mfPcVbgx_Fj3gVEKS_PPYtAsinIu9Zg5Oxy4`
**Tab:** `Final Sheet`

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
- **company**: The company or developer involved
- **property_name**: Specific property or project name if mentioned
- **market**: Phoenix, Scottsdale, Tempe, Mesa, Chandler, Gilbert, Goodyear, Glendale, Apache Junction, Tucson, or "Arizona"
- **asset_type**: industrial, office, retail, multifamily, hospitality, medical office, mixed-use, or other
- **decision_maker_role**: Who to contact (Property manager, Facilities manager, Project manager, Operations manager)
- **contact_search_query**: A Google search string to find the decision maker (e.g., "Lincoln Property Phoenix facilities manager")
- **service_angle**: Why Aether should reach out, in one sentence using their voice (asset preservation, not "cleaning services")
- **news_context**: One-sentence summary of the article's relevance

Sort results by score descending. Keep the top 20-25 leads.

## Step 3: Write to Google Sheets

### Clear existing data (preserve header row)

```bash
GOG_KEYRING_PASSWORD=aether gog sheets clear 1DM5qOV3mfPcVbgx_Fj3gVEKS_PPYtAsinIu9Zg5Oxy4 "Sheet1!A2:Z1000" --no-input -a norgordjacob@gmail.com
```

### Write leads

Build a JSON 2D array of rows. Column order:

```
Company | Property | Market | Asset Type | Score | Priority | Decision Maker | Contact Search | Service Angle | News Context | Title | Link | Published
```

```bash
GOG_KEYRING_PASSWORD=aether gog sheets update 1DM5qOV3mfPcVbgx_Fj3gVEKS_PPYtAsinIu9Zg5Oxy4 "Sheet1!A2" --values-json '<JSON_ARRAY>' --input USER_ENTERED --no-input -a norgordjacob@gmail.com
```

If the Leads tab doesn't exist yet or has no headers, write the header row first:

```bash
GOG_KEYRING_PASSWORD=aether gog sheets update 1DM5qOV3mfPcVbgx_Fj3gVEKS_PPYtAsinIu9Zg5Oxy4 "Sheet1!A1" --values-json '[["Company","Property","Market","Asset Type","Score","Priority","Decision Maker","Contact Search","Service Angle","News Context","Title","Link","Published"]]' --input USER_ENTERED --no-input -a norgordjacob@gmail.com
```

### Verify

```bash
GOG_KEYRING_PASSWORD=aether gog sheets get 1DM5qOV3mfPcVbgx_Fj3gVEKS_PPYtAsinIu9Zg5Oxy4 "Sheet1!A1:F5" --no-input -a norgordjacob@gmail.com
```

## Output

After writing, report to Jacob:
- How many leads were written
- The top 3 leads with company, market, score, and service angle
- Any feed fetch errors
- Link to the sheet: https://docs.google.com/spreadsheets/d/1DM5qOV3mfPcVbgx_Fj3gVEKS_PPYtAsinIu9Zg5Oxy4/edit

## Notes

- Jordan's brand voice is "Straight Shooter": direct, asset-minded, ROI/NOI-focused. Use terms like "asset preservation" and "strategic partner", not "cleaning" or "janitor".
- Sales cycle is long. A lead that seems early-stage (land acquisition, construction start) is still valuable since it may take 1-2 years to convert.
- The existing prospect lists in the Claude project have the ICP: locally-owned 20-600 unit multifamily properties.
- If gog commands fail, fall back to exporting the leads as a CSV and tell Jacob to import manually.
