# Lead Event-Deduplication Design

**Status:** Approved design — ready for implementation planning.
**Date:** 2026-06-17

**Goal:** Stop the pipeline from creating multiple Pipedrive Leads for the *same
real-world news event* when that event is covered by several articles at
different URLs. Two surfaces share one core:

1. **Going forward** (push time, no deletion): before creating a Lead, detect
   that the article describes the same event as a recent Lead and **merge its
   contacts into that Lead** instead of creating a new one.
2. **One-time backfill** (supervised, with deletion): cluster the existing Leads
   since 2026-05-29, pick a keeper per event, merge contacts into it, and delete
   the redundant Leads.

**Concrete target:** the 2026-05-29 backfill digest drops from **83 → ~70**
Leads (≈12 merges), with **every unique contact preserved** on the keepers.

---

## Background

The current dedup gate (`push.find_lead_by_url`) keys on the **Article URL**
custom field: if a Lead with that exact URL exists, push skips it. That catches
the *same article pushed twice* but cannot catch *different articles about the
same event* — e.g. the SkySong leasing story syndicated across azbex, Arizona
Digital Free Press, and Google News becomes 3–4 separate Leads.

Investigation of the 83 Leads created since 2026-05-29 found:

- **1 true URL-duplicate** (same Article URL, two Leads 3 min apart — a
  `leads/search` index-latency miss): the "JLL / Mohr Capital West Summit" pair.
- **11 same-event clusters** (distinct URLs, same story across feeds), ~12
  redundant Leads. URL dedup cannot catch these by design.

## Decisions (from brainstorming)

- **Event detection = hybrid:** a cheap deterministic heuristic narrows
  candidates; **Claude makes the final same-event judgment**. Judgment lives in
  the daily routine (where the architecture already puts all judgment calls),
  with CLI helpers for candidate lookup and contact merge.
- **Error bias = keep separate when unsure.** A duplicate slipping into the
  digest is recoverable (Jordan archives it); silently merging away a genuinely
  distinct Lead is not. Claude only merges when confident.
- **Merge rule = contacts only.** Take all unique contacts from the duplicates
  and add them to the kept Lead. Do **not** merge title, notes, custom fields,
  or Article URL.
- **Keeper selection = most complete data:** most contacts, then most non-empty
  fields, earliest-created as the tiebreak.
- **Applies to the live pipeline going forward, not just the one-time digest.**

---

## Architecture

One new pure-logic module plus three thin CLIs (extending the existing
"small stdlib CLI tools + Claude orchestrates judgment" pattern) and one new
routine step.

```
pipeline/dedup.py                     — pure logic, no I/O, unit-tested
pipeline/cli/find_event_candidates.py — read-only: extracted article -> scored candidate Leads (JSON)
pipeline/cli/merge_contacts.py        — merge contacts into a keeper Lead (no delete)
pipeline/cli/dedup_backfill.py        — one-time cluster + merge + delete (dry-run by default)
skill/aether_daily_routine.md         — one new step between qualify and push
```

### `pipeline/dedup.py` (core, pure functions)

- `normalize_company(name) -> str` — lowercase; strip legal/suffix noise
  (`LLC, Inc, Companies, Group, Partners, Development`, etc.) and parenthetical
  aliases (`"Plaza Companies (SkySong)" -> "plaza"`). **Conservative** —
  under-stripping is safer than over-merging.
- `title_tokens(title) -> frozenset[str]` — lowercase; drop digits, units, and
  stopwords; keep significant tokens.
- `candidate_score(article, lead) -> float` (0..1) — blend of title-token
  Jaccard + company match + city match + signal_type match.
- `find_candidates(article, recent_leads, window_days, threshold) -> list[lead]`
  — the cheap narrower: recent Leads scoring `>= threshold`. Claude confirms.
- `completeness_key(lead) -> tuple` — sort key
  `(num_contacts, num_nonempty_fields, earliest_add_time)`; "most contacts, then
  most filled, earliest wins ties." Drives keeper selection.
- `parse_contacts(lead) -> list[Contact]` — extract contacts from the linked
  Person + `Lead 1/2/3` fields.
- `merge_contacts(keeper_contacts, *other_contacts) -> MergeResult` — union,
  dedup by `(normalized_name, email)`, keeper's contacts first, capped at
  capacity; reports any overflow.

**Contact-capacity caveat:** a Pipedrive Lead represents contacts in the
`Lead 1/2/3` custom fields (**3 slots**); the linked Person mirrors `Lead 1`
(per `push.py`, `contacts[0]` becomes both the Person and `Lead 1`). So capacity
is **3 distinct contacts**. If a merge yields more than 3 unique contacts, keep
the keeper's plus as many unique others as fit (highest-priority first) and **log
the overflow** — no contact is dropped without a log line. Overflow is expected
to be rare for this data.

### CLIs

- **`find_event_candidates`** — stdin: extracted-article JSON. Reads recent
  Leads from Pipedrive (within `DEDUP_WINDOW_DAYS`), runs `find_candidates`,
  prints scored candidates (id, title, url, contacts, filled fields) as JSON for
  Claude to judge. **Read-only.** On any Pipedrive error → returns empty
  candidates (fail-open → create the Lead; consistent with the keep-separate
  bias).
- **`merge_contacts`** — stdin: `{keeper_lead_id, contacts:[...]}`. PATCHes the
  keeper's Person + `Lead 1/2/3` fields with the merged contact set. Honors
  `DRY_RUN`. Marks the merged-away URL as `merged` in `seen_urls`. It also
  appends a single operational breadcrumb line to the keeper's note
  (`merged via event-dedup: <url>`) — this is an audit trail of the merge action,
  **not** merging the loser's note *content* (the "contacts only" rule stands:
  the loser's title/notes/value/Article URL are never copied onto the keeper).
  **Never deletes.**
- **`dedup_backfill --since YYYY-MM-DD [--apply]`** — **dry-run by default.**
  - Dry-run: fetch Leads since the date, cheap-cluster them (connected
    components by `candidate_score >= threshold`), and emit the proposed plan as
    JSON — per cluster: chosen keeper, loser IDs, and the merged contact set —
    for human/Claude review.
  - `--apply`: consume the **reviewed plan** and execute. For each confirmed
    cluster: **merge contacts into the keeper first, verify, then delete the
    losers** (`DELETE /leads/{id}`), marking loser URLs `merged`. If a merge
    fails, skip that cluster and **delete nothing**.

### Routine change — `skill/aether_daily_routine.md`

One new step between qualify and push:

1. For an article that passes qualification, run `find_event_candidates`.
2. If candidates return, Claude reads them + the current article and decides
   same-event (**conservative — only merge when confident**).
3. On a confident match → `merge_contacts` (merge this article's contacts into
   the matched Lead) + mark URL `merged` + **skip push**.
4. Otherwise → push as today.

---

## Data / config

- `seen_urls` gains a `merged` state alongside `pushed / filtered / failed`
  (update `mark.py`'s accepted states and any validation).
- Two optional settings with defaults (in `config.Settings`):
  - `DEDUP_WINDOW_DAYS` (default `14`) — how far back to look for candidates.
  - `DEDUP_SCORE_THRESHOLD` (default `0.5`) — heuristic narrowing cutoff. Tuned
    against the real 11 clusters so the backfill dry-run reproduces 83 → ~70.

---

## Error handling & safety

- Deletion happens **only** in `dedup_backfill --apply`, **only after** contacts
  are confirmed on the keeper, and **only** for clusters in the reviewed plan.
- The going-forward path performs **no deletion** at all.
- Everything honors `DRY_RUN`; `dedup_backfill` additionally defaults to dry-run.
- `find_event_candidates` fails open (empty candidates) so a Pipedrive outage
  degrades to today's behavior (create the Lead), never to a wrong merge.

---

## Testing

- **Unit** (stdlib `unittest`, mirroring existing test style):
  - `normalize_company`, `title_tokens` normalization.
  - `candidate_score` against positive/negative pairs drawn from the **real 11
    clusters** as fixtures (e.g. the four SkySong variants score high; two
    unrelated Phoenix industrial deals score low).
  - `completeness_key` ordering.
  - `parse_contacts` / `merge_contacts`: union, dedup by `(name, email)`,
    keeper-first ordering, overflow logging.
- **Integration** (mocked Pipedrive client):
  - `dedup_backfill` clustering on a Lead fixture → expected clusters.
  - merge-before-delete ordering; **no deletion when a merge fails**.

---

## Success criteria

1. `dedup_backfill --since 2026-05-29` dry-run proposes clusters that take the
   digest from **83 → ~70**, and `--apply` achieves it with every unique contact
   preserved on the keepers.
2. Going forward, the routine creates **no same-event duplicate** when Claude is
   confident, and merges that article's contacts into the existing Lead.
3. **Zero deletions** occur outside the supervised backfill.

## Out of scope

- Merging fields other than contacts (title/notes/value/Article URL stay as the
  keeper's).
- De-duplicating Leads created before 2026-05-29 (the existing ~900-Lead
  graveyard).
- Any change to the URL-based gate, which still runs first and unchanged.
