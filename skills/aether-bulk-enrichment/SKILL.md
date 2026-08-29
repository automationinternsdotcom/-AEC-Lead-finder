---
name: aether-bulk-enrichment
description: Run an explicitly requested historical or bulk Aether AEC lead backfill and produce local lead/company datasets with sourced A/B/C why lines. Do not use for ordinary daily pipeline runs.
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
- Do not enable Apollo, research person-level contacts, generate an email, or call a
  delivery tool. A separate explicit request is required for any later delivery.
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
6. Verify the terminal manifest and the files under
   `<output>/<until>/runs/<run_id>/final/`, including `coverage.csv`, `leads.csv`, and
   `companies.csv`.
   Report lead/company counts, uncovered sources, review count, model usage, and paths.

The skill produces all three labeled why-line variants for later testing; it does not
send them or claim experimental results.
