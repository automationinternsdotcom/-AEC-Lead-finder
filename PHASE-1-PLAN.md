# Phase 1 Plan — Lead Engine Productization

**Status:** Final (Approved)  
**Branch:** `Productize-pipeline`  
**Goal:** Make the existing cleaning pipeline driven by `CampaignSpec` while maintaining parity with current results.  
**Date:** June 16, 2026

---

## Phase 1 Goal

Refactor the current cleaning pipeline so it reads from a loaded `CampaignSpec` (`campaigns/aether-cleaning-az.yaml`) and produces **the same or better results** than today.

This proves that the general engine architecture works on a real vertical before expanding to more verticals or building the planner.

---

## What We Are NOT Doing in Phase 1

- No heavy multi-tenant isolation (per-campaign database separation, concurrent execution safety, advanced rate limiting, etc.)
- No changes to scheduling or orchestration logic
- No new verticals
- No Planner implementation
- No UI or customer-facing changes

We are intentionally keeping Phase 1 focused and low-risk.

---

## Light Future-Proofing (Approved)

We will apply these three lightweight practices during Phase 1:

1. **Do not add a `campaign_id` column to the dedup tables.** Isolation will be implemented as **separate per-campaign storage files** in Phase 7, so a `campaign_id` column on a shared table would future-proof a design we are not using. For Phase 1 (single campaign) make no change to dedup storage; instead **document** that the current state provides **no** multi-campaign safety (see Step 6).
2. **Avoid hardcoding paths or global variables** that would make supporting multiple campaigns difficult later.
3. **Keep spec loading clean and reusable** so adding more campaigns in the future is easier.

---

## Detailed Steps

| Step | Action | Key Details | Priority |
|------|--------|-------------|----------|
| 0 | Capture the parity corpus (build the harness) | **New — do this first.** Build a small parity harness `tests/parity/harness.py` with three modes, invoked via `uv run python -m tests.parity.harness {capture\|golden\|compare}` (matches the repo's `uv run` convention). **The harness does deterministic I/O and comparison only — it never calls a model** (Path B: no `anthropic` dep, no in-code model call).<br>• `capture` — **real automated Python, no model.** Pull 30–50 real article extractions today into `tests/fixtures/corpus/` (one `NNN.txt` per article + a `manifest.json` of url/title/source/fetched_at), weighted deliberately toward known HIGH / MED leads so the regression check has teeth.<br>• `golden` — **in-session scaffold, not an automated model run.** The harness loads each corpus article and assembles the exact runtime (hardcoded) prompt, and gives a clean place to record the judgment. The judgment itself is produced **in-session** by Claude running `skill/aether_daily_routine.md` over each article — exactly today's path — and the resulting `priority` / `az_relevant` / `confidence` are recorded to `tests/fixtures/golden_old.json`. Do this **before** the Step 2 refactor, while the old prompt still exists — the baseline is unrecoverable afterward without a git revert.<br>• `compare` — used in Step 7 (scaffolds the in-session run of the **new spec-driven** prompt over the corpus, then diffs the recorded outputs against `golden_old.json`).<br><br>**Measure the jitter floor on the metric that matters:** while capturing `golden`, produce the in-session judgment for the corpus 2–3× and record the old prompt's self-disagreement — specifically how many HIGH leads flip to dropped across its own in-session reruns. That HIGH→dropped self-flip count is the **jitter floor**, and it (not a generic distribution number) defines the Step 7 accept threshold. | Highest |
| 1 | Load the spec cleanly at runtime | Create a simple, reusable loader that accepts a campaign identifier or YAML path and returns a `CampaignSpec` object. Pass the spec (or relevant sections) to downstream functions. | High |
| 2 | Make the daily routine prompt spec-driven | Update `skill/aether_daily_routine.md` so the following values come from the spec instead of being hardcoded in the prompt:<br>• `qualification.relevance_rubric`<br>• `targeting.buyer_personas`<br>• `targeting.trigger_signals`<br>• `targeting.negative_keywords`<br>• `client.service_area`<br>• `enrichment.outreach_angle`<br>• `enrichment.buyer_persona`<br><br>Preserve the existing HIGH / MEDIUM / LOW judgment structure and brand voice.<br><br>**Full fidelity required:** the spec must carry the *complete* prompt substance verbatim (the detailed HIGH/MED/LOW protocol, geographic specifics, examples, ICP/lease-up framing) — **not** a compressed summary. Decompose it into the correct fields (`relevance_rubric`, `trigger_signals`, geography); park anything with no clean field in `relevance_rubric` and note the schema gap. The template keeps only structure and interpolates the substance — single source of truth is the spec, do **not** keep both.<br><br>**Primary parity gate — the prompt diff:** the cheapest and strongest parity proof is a deterministic string diff, not the LLM corpus run. Render the new spec-driven prompt and diff it character-for-character against today's hardcoded prompt — for **both** the assess prompt and the enrich prompt (both are model-driven and both change here). If the diff is **empty**, the model receives identical input, so behavior is unchanged *by construction* — parity is proven for free, with no model run, no jitter, no cost. Drive the diff toward empty by matching the interpolation formatting (list rendering, ordering, punctuation) to the old text, and keep the rendering itself deterministic (stable list ordering) so the diff is reproducible. Only the **residual differences** the diff surfaces need further checking; the Step 7 corpus run then serves as confirmation/backstop (catching interpolation bugs and verifying any genuine leftover deltas don't shift judgment), not the primary proof. Do this change in isolation.<br><br>**Pre-execution audit:** before refactoring, pass over every field in `aether-cleaning-az.yaml` and confirm it holds the real value the code uses, not a paraphrase (check `trigger_signals` and `source_tags` first). | Highest |
| 3 | (Mostly skipped) Mark cleaning-specific logic as legacy | **No code behavior change.** Do **not** drive the Maricopa city list from `spec.discovery.geography` — you cannot rebuild a fixed data-source coverage list from `"AZ"` (category error), and it is unnecessary for parity. Leave the city list hardcoded and producing today's results. Optionally add a one-line guard comment (`# cleaning-specific coverage list; do NOT generalize from geo — see Phase 3/4 source-plugin plan`). Real generalization (a tagged source plugin selected via `source_tags`) is deferred to Phase 3/4.<br>• `schema.py`: marking cleaning-specific enums as legacy is fine (comment only, no behavior change). | Low |
| 4 | Wire discovery / fetch to the spec | **Baseline first**: Before changing code, document exactly what the current fetch mechanism calls today (e.g. Google News RSS feeds, specific URLs in `sources.yaml`, sources table queries, etc.). Record this baseline clearly.<br><br>Then make search queries and source selection driven by `spec.discovery.search_queries` and `spec.discovery.source_tags`. | Medium |
| 5 | Make destination handling spec-aware | **Minimal but load-bearing**: At minimum, introduce reading from `spec.destination` (even if the actual behavior stays exactly the same as today and still uses Pipedrive). This prepares a clean slot-in path for Phase 3 destination implementations.<br><br>Full destination abstraction can stay minimal in Phase 1. | Low |
| 6 | Apply light future-proofing (documentation-only for isolation) | • **Do not** add a `campaign_id` column to `seen_urls` / `enriched_orgs`, and do not change dedup storage — isolation will be per-campaign files in Phase 7, so the column future-proofs a design we are not using and any storage change risks parity. Instead **document**: (a) isolation design = separate per-campaign storage files (Phase 7); (b) current state provides **no** multi-campaign safety; (c) `enriched_orgs`' name-only cache failure mode is a plausible-looking **wrong contact** (persona-dependent), not just a swallowed lead.<br>• Avoid hardcoding file paths or global mutable state.<br>• Keep the spec loading mechanism clean and decoupled. | Low |
| 7 | Execute parity test (frozen-corpus, judgment-only) | **Concrete parity check** — replaces the historical date-range method, which cannot work (live fetch is not reproducible + LLM nondeterminism). **Scale this to the Step 2 diff's residual:** run `compare` over the corpus items affected by the diff's leftover deltas. If the diff is empty, a spot-check of the HIGH leads is enough to confirm the runtime assembly matches the diff — a full re-run on an empty diff only re-measures jitter. This keeps the Step 2 diff as the genuine primary gate and makes this step its scoped confirmation:
<br>1. Run `uv run python -m tests.parity.harness compare` — it **scaffolds the in-session run** of the spec-driven prompt over the **frozen Step 0 corpus** (standing in for fetch+extract; the assess judgment is produced in-session by Claude running the skill, exactly as in Step 0 `golden`, then recorded). The harness performs the deterministic I/O and comparison only — it does not call a model.
<br>2. The harness diffs each article's recorded `priority` / `az_relevant` / `confidence` against `tests/fixtures/golden_old.json` (the Step 0 baseline).
<br>3. **"Dropped" is defined exactly as the pipeline's `is_qualifying()` gate (`pipeline/extract.py`):** an article is *dropped* iff `az_relevant` is false **OR** `priority == 'low'` **OR** `confidence < 0.5` (`GENERAL_MIN_CONFIDENCE`, the general floor) **OR** `signal_type == 'other'` **and** `confidence < 0.6` (`OTHER_MIN_CONFIDENCE`). The spec's single `min_confidence: 0.6` corresponds to the `'other'`-signal floor **only**; the general floor is `0.5`. `compare` evaluates drops against these actual code floors, **not** a single spec value.
<br>4. **Accept criterion (reconciled with the jitter floor): zero HIGH→dropped regressions *beyond* the Step 0 jitter floor.** A lead kept + HIGH under the old prompt must not become dropped (per the definition above) — *unless* the old prompt itself flips that same lead across its own in-session reruns (within noise, excluded). Aggregate HIGH / MED / LOW counts must stay within the same jitter band. Inspect every disagreement. Not "identical," not "equal or better."
<br>5. Generate `PARITY-RESULTS.md` **from the comparison** (not a hand-written narrative).
<br><br>Human preview step must remain functional. | High |
| 8 | Review, commit, and push | Show full diff to user for approval.<br>Commit locally on `Productize-pipeline`.<br>User decides when to push to remote. | - |

---

## Success Criteria

Phase 1 is considered complete when:

- The pipeline successfully loads and runs using `campaigns/aether-cleaning-az.yaml`
- Lead quality and qualification judgments (HIGH / MEDIUM / LOW) on the frozen corpus are **distribution-comparable with zero HIGH→dropped regressions** versus the golden baseline, as defined in Step 7 and recorded in `PARITY-RESULTS.md`
- The human preview / confirmation step continues to work
- The light future-proofing items are applied (no hardcoded paths/globals, clean spec loading) and the isolation design + no-multi-campaign-safety caveat are documented
- All existing tests continue to pass
- New parity validation passes

---

## Files Expected to Change

| File | Expected Change | Notes |
|------|------------------|-------|
| `skill/aether_daily_routine.md` | Major — prompt becomes partially dynamic using spec values | Most important change in Phase 1 |
| `pipeline/assessor.py` | None (optional one-line legacy comment) | City list stays hardcoded; do NOT generalize from geo — see Step 3 |
| `schema.py` | Minor — review and mark legacy enums | Optional cleanup, comment only |
| Main pipeline runner / daily routine entrypoint | Moderate — add clean spec loading | New reusable loading logic |
| (New) Spec loading helper (if needed) | New file or function | Keep it simple and reusable |
| (New) `tests/fixtures/corpus/` + golden baseline | New — frozen parity corpus + golden outputs | Captured in Step 0, before the refactor |
| `PARITY-RESULTS.md` | New file | Generated from the frozen-corpus comparison (Step 7) |

---

## Multi-Tenancy Note

Full multi-tenant hardening (per-campaign state isolation, secrets, rate limits, concurrent safety) remains planned for **Phase 7**.

For now, staggered scheduling (different run times per vertical) is acceptable. Phase 1 only adds light future-proofing to reduce future refactoring effort.

---

## Next Steps After Phase 1

Once Phase 1 is complete and parity is proven:

- Phase 2: Build the Planner (company description → CampaignSpec)
- Phase 3: Destination abstraction (Excel + Pipedrive)
- Phase 4: Add a second vertical to prove generalization
- etc.

---

**Approved by:** User  
**Ready for execution**
