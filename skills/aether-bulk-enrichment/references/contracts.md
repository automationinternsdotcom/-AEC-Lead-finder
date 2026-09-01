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

## Saved discovery corpus

- A new bounded run may reuse candidates from an earlier bulk run only when that
  source run completed discovery.
- Filter imported candidates by their saved page publication date before screening;
  omit undated and out-of-window pages.
- Verify every imported raw article against its recorded hash. When a dynamic page
  was overwritten at the same run-scoped path, accept it only if the saved HTML still
  contains the exact canonical URL and its parsed publication date matches the
  candidate. Copy the accepted bytes into the child run and record their current
  hash. Missing evidence or failed identity checks stop the import; never refetch.
- Reset prior screening and qualification state. Corpus reuse saves archive crawling,
  not model judgments or downstream events.
- Carry the parent run's per-source incomplete-coverage state into the child run and
  identify the source corpus run in coverage errors and manifest configuration.

## Company profile and why line

- Resolve preliminary organizations to final companies by canonical website host,
  falling back to normalized company name.
- Select the anchor event by score, then priority (`high`, `medium`, `low`), newest
  event date, and stable event ID.
- One Grok 4.3 company request selects exactly one approved event-stage template,
  one supporting lead event, short insertion slots, confidence, and source URLs.
- The approved sendable templates cover acquisition by a verified new owner, opening,
  proposed development, approval, construction start, lease/relocation, site
  acquisition, expansion, facility-tied funding, renovation/conversion, construction
  progress, and completion.
- Seller/broker/listing signals route to `route_new_owner`; closures, bankruptcy,
  lawsuits, stalled or abandoned projects route to `skip_negative`; general market or
  portfolio signals without a property trigger route to `skip_general`.
- The model never writes the final sentence. Deterministic code renders the approved
  `Hi [first name] just wanted to reach out since I saw on the news that ...` wording
  from the returned slots, followed by one approved question. All templates use the
  future-needs question except renovation/conversion, which uses the review question,
  and expansion, which uses the additional-space question. Validation requires one sourced event
  ID, exact slot coverage, short URL-free values, exactly two sentences, and a 20–55
  word final line. Non-company slots are lowercase. Uppercase letters are allowed only
  at sentence starts, in the standalone pronoun `I`, and within a company reference
  whose casing was resolved from the canonical company name or a known alias.
- `company`, `project`, and `project_or_expansion` references
  are hard-capped at three words. Prefer a supplied, recognizable abbreviation or
  casual short name when it can stand alone without confusing the recipient; never
  invent an obscure acronym. A company reference must match a contiguous phrase from
  a supplied canonical name or alias; deterministic code restores its verified brand
  capitalization. Unknown and overlong references fail closed without a repair call.
- A `location` slot contains exactly one smallest useful locality or neighborhood and
  no more than three words. Deterministic normalization keeps only the first leaf
  place, removes state suffixes and road-detail clauses, and rejects commas, counties,
  regions, multiple cities, and broad state-only values. For example, `tempe, arizona`
  becomes `tempe`, `deer valley, north phoenix` becomes `deer valley`, and `tucson and
  gilbert` becomes `tucson`. A location that cannot be reduced safely fails closed.
- Unsupported or invalid selections are blank and enter review. Intentional routing
  outcomes are blank with `why_line_status=skip`; they are not validation failures.
  There is no model repair call.
- A why-line-only refresh of a completed run uses its already deduplicated companies,
  preserves the source outputs, and writes a versioned revision with a separate
  `why_line_status` on lead rows. A deterministic contract-only revision may migrate
  and rerender compatible cached responses without another model call. This includes
  the base `raw/company-profiles/` response because the base company stage and v4
  revision use the same template-selection contract.

## Outputs

- All exports are isolated under `<output>/<until>/runs/<run_id>/final/`.
- `leads.csv`: one row per final lead event, including `company_id`, score, evidence,
  and the projected `why_line`, `why_template_key`, confidence, sources, and status.
- `companies.csv`: one row per final company with domain, locations, event IDs/count,
  anchor event, employee count, one why line, template key, confidence, sources,
  status, and provenance.
- JSONL equivalents, `reviews.jsonl`, `coverage.csv`, and a terminal manifest are
  required. No email/HTML/contact/Apollo artifact is part of the base bulk run.

## Explicit recipient add-on

- Recipient enrichment is a separate, explicit-only resume mode over the completed
  `recipient-outreach-v4` company revision. Only companies with a valid why line enter
  the recipient stage.
- A recipient row must map to one company. Combined model labels using `and` for two
  organizations enter review before person research and never reach the handoff.
- Research up to three sourced current decision makers per company with one Grok 4.3
  request per company, then make one public-contact request per identified person.
  Persist stable `Person` and `ContactCandidate` records so resume does not repeat
  completed provider attempts.
- Emit one `recipients.csv` row per person. Derive `first_name` from the sourced full
  name, ignoring common honorifics, and replace the exact `Hi [first name]` prefix
  deterministically. Never leave an unresolved placeholder.
- Apollo requires its own explicit authorization and is used only for people without a
  non-rejected public email or phone. Phone reveal is disabled. Enforce the supplied
  hard cap across resumes, count only new API requests against it, and preserve cached
  null results.
- Apollo request and local billable flags are auditable upper-bound accounting; exact
  credits and dollar charges remain provider-ledger facts. Recipient enrichment never
  generates or sends an email.
- Write `recipients.csv`, recipient-level `companies.csv`, `people.jsonl`,
  `contacts.jsonl`, `reviews.jsonl`, and `summary.json` under
  `final/recipient-outreach-v4/recipients-v1/` without changing the v4 source files.

## Sales handoff add-on

- `--build-sales-handoff` is a local projection only: it performs no Pipedrive,
  Warmy, Gmail, Apollo, or model call.
- Accept only valid v4 single-company profiles. Preserve event-level Pipedrive Lead
  identity, and fail closed on invalid events, zero scores, non-high event confidence,
  or blocking open reviews.
- A contact may reach Warmy's authoritative verification only when locally verified,
  or when its status is `unknown` with reason exactly
  `domain_mx_valid_mailbox_unverified`. This precheck is not mailbox verification.
- Rank recipients with the daily production role scorer. Only rank 1 with score 70+
  can yield a ready sequence; retain lower-ranked or lower-score candidates as blocked
  audit records when they otherwise pass the source precheck.
- Write and reload-validate a schema-versioned, protocol-versioned, content-hashed
  `sales_handoff.json`. Set the unsubscribe merge value to
  `__integration_generated__`; the integration worker creates the real signed URL.
- Handoff generation does not enqueue work. Provider synchronization and campaign
  enrollment are separate actions, and enrollment still requires a matching immutable
  approval batch plus every activation flag.
