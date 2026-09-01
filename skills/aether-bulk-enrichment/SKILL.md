---
name: aether-bulk-enrichment
description: Run an explicitly requested historical or bulk Aether AEC lead backfill and produce local lead/company datasets with one sourced, template-rendered why line per business. Do not use for ordinary daily pipeline runs.
---

# Aether Bulk Enrichment

Use this skill only when the user explicitly asks for **bulk enrichment**, a historical
backfill, or an archive-scale lead/company dataset. Never invoke it implicitly for a
daily V2 run, monitoring, email delivery, or ordinary contact enrichment.

## Required boundaries

- Run from the repository root and use `scripts/bulk_enrich.py` as the only entrypoint.
- Keep state and outputs under `results/backfills/`; they are local artifacts, not
  production daily state.
- Use Grok 4.3 for every model stage.
- Do not research person-level contacts, enable Apollo, generate an email, or call a
  delivery tool during the base bulk run. Recipient research and Apollo each require
  a separate explicit user request. Email generation and delivery remain out of scope.
- Treat the requested date range as inclusive and filter by article publication date.
- Prefer resuming the same run ID over restarting expensive archive or model work.
- Report per-source archive coverage and never describe an inaccessible source as
  having no leads.

## Workflow

1. Confirm the inclusive start/end dates from the request. Prefer the last complete
   day as the end date when the user does not specify one.
2. Read [references/contracts.md](references/contracts.md) before changing output
   fields, why-line rules, seed behavior, or archive limits.
3. Preflight the local Grok endpoint and any requested seed run. Do not request
   NewsAPI or Apollo credentials; sitemap/archive discovery and bounded Grok search
   fallback are built in.
   To process a bounded date slice from a previously completed archive discovery
   without crawling again, start a new run with `--corpus-db PATH
   --corpus-run-id ID`. The source run must have a completed discovery stage;
   every imported page must still exist and match its recorded hash or pass the
   dynamic-page canonical-URL and publication-date identity check. Accepted bytes
   are copied and re-hashed in the child run. This mode resets prior
   screening/qualification state and filters by page publication date.
4. Start or resume the backfill:

   ```bash
   uv run python skills/aether-bulk-enrichment/scripts/bulk_enrich.py \
     --since YYYY-MM-DD --until YYYY-MM-DD \
     --output results/backfills/YYYY-MM-DD_YYYY-MM-DD \
     --workers 5
   ```

   Add `--seed-db PATH --seed-run-id ID` only when a completed prior V2 run should be
   reused. When that seed supplies the final day, also pass `--archive-until` for the
   day before it so the expensive daily work is not repeated. Add `--run-id ID
   --resume` after interruption. Add `--reuse-discovery-corpus` only when an audited,
   interrupted run already contains saved article pages and the user wants those pages
   screened without another crawl. Resume rejects changed dates, sources, seed, model,
   batch contract, or recovery mode after those values have been recorded.
5. Discovery is followed by offline Arizona/AEC screening, person-free exact-ID batch
   qualification from saved evidence, bounded fuzzy dedup/scoring, and company work.
   Web search is reserved for scoped source fallback and company sourcing.
   Company work uses one Grok 4.3 request per deduplicated company to select one
   approved event-stage template and return only sourced insertion values. Local code
   renders the final sentence-case news-based opener and stage-appropriate janitorial
   question so the model cannot rewrite the brand voice. Company and project
   references are capped at three words and prefer recognizable short names. Local
   validation resolves every company insertion against the canonical name and aliases,
   then renders the verified brand capitalization; unknown company forms fail closed.
   Every non-company insertion remains lowercase. A location is deterministically
   reduced to one leaf locality or neighborhood of at most three words: no comma,
   state, county, region, parent city, second city, or road detail. Broad or unusable
   locations fail closed. Seller,
   broker, negative-event, and general-market signals route to an intentional skip.
   Invalid selections fail closed; do not spend a second model call on repair.
6. Verify the terminal manifest and the files under
   `<output>/<until>/runs/<run_id>/final/`, including `coverage.csv`, `leads.csv`, and
   `companies.csv`.
   Report lead/company counts, uncovered sources, review count, model usage, and paths.

The skill produces one sourced, brief news-based opener and question per sendable business. It
does not send the line or claim experimental results.

## Why-line-only revision

When the user explicitly asks to re-enrich why lines for a completed bulk run, reuse
the deduplicated company profiles and skip every other stage:

```bash
uv run python skills/aether-bulk-enrichment/scripts/bulk_enrich.py \
  --since YYYY-MM-DD --until YYYY-MM-DD \
  --output results/backfills/YYYY-MM-DD_YYYY-MM-DD \
  --run-id RUN_ID --resume --refresh-why-lines
```

The revision is interruption-safe and uses at most one model response per company.
Grok selects the approved template and slots in that response; deterministic code
renders the single why line. It preserves the original final directory and writes
the revised dataset under `final/recipient-outreach-v4/`. When v3 responses already
exist, v4 reparses and rerenders them locally under the stricter location contract;
do not spend another model call solely for this deterministic migration.

For a qualitative pilot, add `--why-limit 20`, inspect the cached profile artifacts,
then rerun the same command without the limit. Pilot responses are reused and do not
incur another company call.

## Explicit recipient enrichment

Only after the user explicitly asks to add real recipients, resume the completed v4
why-line revision with GPS-style person research:

```bash
uv run python skills/aether-bulk-enrichment/scripts/bulk_enrich.py \
  --since YYYY-MM-DD --until YYYY-MM-DD \
  --output results/backfills/YYYY-MM-DD_YYYY-MM-DD \
  --run-id RUN_ID --resume --enrich-recipients
```

This researches up to three current decision makers per company, then researches each
person's public professional email, phone, and LinkedIn once. It creates one row per
person and deterministically replaces `Hi [first name]` with the person's actual first
name. It never changes the company-level v4 source files and never generates or sends
email.

Apollo is a last fallback only when the user separately authorizes credit spend. Add
`--apollo-go --apollo-cap N`; phone reveal is always disabled in this skill. The cap is
a hard ceiling on new person-match API requests across resumes, and the default is 444.
Cached Apollo results do not consume the cap. Report local request/billable accounting
as an upper bound and use Apollo's account ledger for exact credits or dollar charges.
Recipient outputs are written beneath
`final/recipient-outreach-v4/recipients-v1/`.

## Local sales handoff

After recipient enrichment, build the typed, content-hashed provider boundary as a
separate local-only action:

```bash
uv run python skills/aether-bulk-enrichment/scripts/bulk_enrich.py \
  --since YYYY-MM-DD --until YYYY-MM-DD \
  --output results/backfills/YYYY-MM-DD_YYYY-MM-DD \
  --run-id RUN_ID --resume --build-sales-handoff
```

This action makes zero provider calls and writes `sales_handoff.json` under the
recipient revision. It includes only valid single-company routes. Recipient ranking,
the role-score threshold, authoritative-verification precheck, open-review blockers,
and immutable merge snapshot match the daily production handoff. Enqueuing that file
into the integration database and running the worker are distinct operator actions.
The worker may create Pipedrive organizations/leads/people and Warmy prospects, but
campaign enrollment remains protected by the immutable approval and activation gates.
