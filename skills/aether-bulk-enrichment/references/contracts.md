# Bulk Enrichment Contracts

## Archive discovery

- Traverse robots-listed and conventional `sitemap.xml`, `sitemap_index.xml`,
  `wp-sitemap.xml`, and compressed XML, to four levels.
- Safety limits are 100 sitemap documents and 10,000 URL entries per source. A limit
  hit is an incomplete-coverage review, not a successful zero-result source.
- Sitemap dates prioritize work; final acceptance uses page publication metadata.
- A regional path on a national domain must not expand into a whole-domain crawl.
  Out-of-scope sitemap URLs are skipped and the source is handed to bounded search
  fallback. Canonical pages already saved by another curated source are reused globally.
- Run one bounded Grok web-search fallback only for a source with no dated archive
  candidates or incomplete sitemap coverage.

## Screening and model batches

- Saved pages receive conservative offline Arizona/AEC screening before model spend.
- Qualification uses saved evidence, excludes people, performs no web search, and
  requires exact candidate-ID coverage in batches of at most 25.
- Fuzzy dedup and contact-independent scoring use deterministic batches of at most 40
  lead events. Invalid or incomplete batches enter review without dropping records.
- Resume configuration, curated-source hash, seed range, and run-scoped artifact paths
  are integrity boundaries.

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
  - `a`: recipient-facing copy using a timely sourced property event.
  - `b`: recipient-facing copy using a specific sourced operating detail.
  - `c`: a distinct recipient-facing combination of event and operating context.
- A/B/C are alternative cold-email opening sentences, not analyst explanations of
  timeliness, service fit, or supposed company needs. Each begins with `Saw`,
  `Noticed`, or `Your`, contains 25–45 whitespace-separated words, cites its evidence,
  contains no URL or en/em dash, and makes no facilities-services or sales inference.
- One model response must return all three variants for the company. Unsupported or
  invalid lines are blank and enter review. A sourced 17–24 word line may receive one
  deterministic, fact-free conversational completion before revalidation. There is
  no model repair call.
- A why-line-only refresh of a completed run uses its already deduplicated companies,
  preserves the source outputs, and writes a versioned revision with a separate
  `why_line_status` on lead rows.

## Outputs

- All exports are isolated under `<output>/<until>/runs/<run_id>/final/`.
- `leads.csv`: one row per final lead event, including `company_id`, score, evidence,
  and projected A/B/C why lines.
- `companies.csv`: one row per final company with domain, locations, event IDs/count,
  anchor event, employee count, A/B/C lines, confidence, sources, and provenance.
- JSONL equivalents, `reviews.jsonl`, `coverage.csv`, and a terminal manifest are
  required. No email/HTML/contact/Apollo artifact is part of this skill.
