---
name: phoenix-new-property-leads-daily
description: Generate a short daily CSV+JSON of newly announced or newly opening commercial properties (office, retail, medical, industrial) in the Phoenix, AZ metro area that a commercial cleaning company could pitch for a cleaning contract. Use this skill whenever the user asks for "today's leads", "Phoenix property leads", "new property openings", "cleaning contract leads", or runs the scheduled morning lead pull — even if they don't explicitly name the skill. Pulls from a single Arizona Commerce Center RSS feed only; output is raw data (CSV + JSON), no commentary.
---

# Phoenix New Property Leads — Daily

Generate a daily lead list for a commercial cleaning company owner based in Phoenix, AZ. Output a CSV **and** a JSON list of newly announced or newly opening commercial properties in the Phoenix, AZ metro area (Phoenix, Scottsdale, Mesa, Tempe, Chandler, Gilbert, Glendale, Peoria, Surprise, Goodyear) that they could pitch for a cleaning contract.

**Output only the raw data — no summary, no commentary, no explanations.**

## Property types to include

- **Office buildings** — new towers, coworking, corporate HQs, build-to-suits
- **Retail / shopping centers** — new plazas, strip malls, big-box openings. Restaurant pad sites count only if part of a larger center.
- **Medical / healthcare facilities** — clinics, urgent care, dental, surgical centers, new hospital wings
- **Industrial / warehouses** — distribution centers, light-industrial, flex space

## Recency

Only include properties announced, topped-out, leased-up, or opening within roughly the next 90 days, or that opened within the last 14 days. Skip properties whose openings are >90 days out unless they are particularly large (>100k sqft) **and** have a named facilities/operations contact already public.

## How to gather

Use WebFetch to poll the following single RSS feed on each run:

**Feed URL:** `https://www.arizcc.com/blog-feed.xml`

1. Fetch the RSS feed above using WebFetch.
2. Parse the returned XML for `<item>` entries published within the last 24 hours.
3. For each item, read the `<link>` URL with WebFetch to get the full article text.
4. Filter articles to only those describing a qualifying property (see property types and recency rules above). Discard anything that is purely market commentary, an opinion piece, a personnel move, or a residential-only story.
5. Extract property details from each qualifying article to populate the CSV columns below.

**Do not** run any web searches. **Do not** visit any site other than the RSS feed and the article links it contains. All leads must originate from this single feed.

If the feed returns zero qualifying articles on a given day, follow the zero-results rule in the Output format section below. Do not pad with stale or off-topic entries.

## Contact person handling

For each property, try to identify the person most likely responsible for facility/janitorial decisions (facilities manager, general manager, operations director, owner/principal, or developer project lead) **using only information present in the article itself**.

- **Do not** visit external sites (LinkedIn, company pages, etc.) to look up contacts.
- **If the article does not name a relevant contact**, leave the contact fields blank and put `LOW_CONFIDENCE - run enrichment` in the description so the user knows to run it through their deterministic enrichment pipeline.
- **Do not fabricate names, emails, or phone numbers.** If a field is unknown, leave it blank (CSV) or `null` (JSON).

## Output format

Output **only** raw data — no summary line, no commentary, no explanations, no markdown headings. The entire response must consist of exactly two fenced code blocks and nothing else.

### Block 1 — CSV (labeled `csv`)

A CSV with exactly these columns as the header row, followed by one row per lead:

```
property_name,address,property_type,opening_date_or_status,contact_name,contact_title,contact_email,contact_phone,description,source_url
```

### Block 2 — JSON (labeled `json`)

A JSON array of objects with the same fields as the CSV. Each object uses the column names above as keys. Empty/unknown values must be `null`, not empty strings.

### Rules for column values (apply to both CSV and JSON)

- **property_name** — name of the building/development
- **address** — full street address or nearest cross-streets if exact address isn't published
- **property_type** — one of `office`, `retail`, `medical`, `industrial`
- **opening_date_or_status** — e.g. `Opens June 2026`, `Under construction, Q3 2026`, `Opened May 5, 2026`
- **contact_name / contact_title / contact_email / contact_phone** — leave blank (CSV) or `null` (JSON) if not confidently found in the article
- **description** — max 100 characters, factual, no marketing fluff. Only use facts present in the source article. If nothing meaningful is available, leave blank/null.
- **source_url** — the article URL from the RSS feed `<link>` element

### Quality and edge cases

- Keep output short — quality over quantity. **3–10 strong leads is better than 30 thin ones.**
- Escape commas inside CSV fields by quoting the field with double quotes.
- If the feed returns zero qualifying articles, output an empty CSV (header row only) and an empty JSON array (`[]`). Do not add any explanatory text.
