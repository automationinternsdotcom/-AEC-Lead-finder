# Phase 1 Productization Notes

## Ultimate Goal Fit

The current product direction is one Aether cleaning campaign driven by
`CampaignSpec`: fetch, qualify, enrich, and preview should stay configurable
without branching the pipeline. Phase 1 moved the current Aether cleaning
pipeline in that direction by making the runtime load
`campaigns/aether-cleaning-az.yaml` and by rendering the
qualification/enrichment prompts from that spec.

This is intentionally not a full platform abstraction. Extraction,
deduplication, Pipedrive writing, and contact caching remain stable so the
working Aether path can keep parity while Phase 3 validates live discovery.

## Current Fetch Baseline

Before Phase 1 wiring, fetch read enabled rows from `sources.yaml` directly:

- `google_news_az_cre` -> `Arizona commercial real estate when:30d`
- `google_news_phoenix_dev` -> `Phoenix office industrial retail development when:30d`
- `google_news_tucson_cre` -> `Tucson commercial real estate development when:30d`
- `azbex` -> `https://azbex.com/feed/`
- `arizona_digital_free_press` -> `https://arizonadigitalfreepress.com/feed/`

After Phase 1 wiring, `sources.yaml` acts as the source registry and
`CampaignSpec.discovery.source_tags` / `CampaignSpec.discovery.search_queries`
select the active source set. The default Aether spec selects the same five
sources for parity.

## Isolation Caveat

Phase 1 does not add `campaign_id` columns to `seen_urls` or `enriched_orgs`.
That is deliberate: the planned isolation model is separate per-campaign storage
files in a later phase, not a shared table with a campaign discriminator.

Current state provides no multi-campaign safety. Running two campaigns against
the same SQLite database can cause:

- URL dedup cross-talk in `seen_urls`, where one campaign suppresses another
  campaign's article.
- Contact-cache cross-talk in `enriched_orgs`, where a cached contact for one
  buyer persona appears plausible but is wrong for another persona.

For now, run only the Aether cleaning campaign against the default state file.
If another campaign is ever introduced, add per-campaign storage before running
it against the same SQLite database.

## Deferred Generalization

The Maricopa assessor city/coverage logic remains cleaning/AZ-specific. Do not
derive that fixed coverage list from `spec.discovery.geography`; `"AZ"` is a
geographic scope, not a source-coverage model. A real source-plugin layer belongs
in the later discovery generalization phase.
