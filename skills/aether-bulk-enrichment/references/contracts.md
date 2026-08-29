# Bulk Enrichment Contracts

## Archive discovery

- Traverse robots-listed and conventional `sitemap.xml`, `sitemap_index.xml`,
  `wp-sitemap.xml`, and compressed XML, to four levels.
- Safety limits are 100 sitemap documents and 10,000 URL entries per source. A limit
  hit is an incomplete-coverage review, not a successful zero-result source.
- Sitemap dates prioritize work; final acceptance uses page publication metadata.
- Run one bounded Grok web-search fallback only for a source with no dated archive
  candidates or incomplete sitemap coverage.

## Seed runs

- A seed manifest must be terminal and its recorded artifacts must match their hashes.
- Import only valid seed events and their supporting candidates/organizations.
- Clone imported records to the bulk run ID, then fuzzy-deduplicate across seed and
  archive events before scoring.
- If seed integrity fails, stop. The operator may omit the seed and cover that date in
  archive discovery instead.

## Company profile and why lines

- Resolve preliminary organizations to final companies by canonical website host,
  falling back to normalized company name.
- Select the anchor event by score, then priority (`high`, `medium`, `low`), newest
  event date, and stable event ID.
- One Grok 4.3 company request returns all variants:
  - `a`: why the anchor property event makes outreach timely.
  - `b`: why ongoing company operations fit Aether facilities services.
  - `c`: a specific blend of the anchor event and operating context.
- Each nonblank line is one sourced sentence of 25–45 words with no en/em dash.
  Unsupported lines are blank and enter review. Sourced format failures receive one
  zero-search repair batch with exact ID coverage.

## Outputs

- `leads.csv`: one row per final lead event, including `company_id`, score, evidence,
  and projected A/B/C why lines.
- `companies.csv`: one row per final company with domain, locations, event IDs/count,
  anchor event, employee count, A/B/C lines, confidence, sources, and provenance.
- JSONL equivalents, `reviews.jsonl`, `coverage.csv`, and a terminal manifest are
  required. No email/HTML/contact/Apollo artifact is part of this skill.
