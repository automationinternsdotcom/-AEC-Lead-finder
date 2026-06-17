# Lead Event-Deduplication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the pipeline from creating multiple Pipedrive Leads for the same news event covered by different-URL articles — by merging contacts into an existing Lead at push time (no deletion) and via a supervised one-time backfill (cluster → merge → delete) that takes the 2026-05-29 digest from 83 → ~70 Leads with every unique contact preserved.

**Architecture:** One pure-logic module (`pipeline/dedup.py`, no I/O, unit-tested) holds normalization, same-event scoring, clustering, completeness ranking, and contact-string merging. Three thin CLIs wrap it: `find_event_candidates` (read-only candidate lookup), `merge_contacts` (merge contacts into a keeper Lead, never deletes), and `dedup_backfill` (one-time cluster/merge/delete, dry-run by default). The daily routine gains one step where Claude makes the final same-event judgment.

**Tech Stack:** Python 3.12 stdlib + `httpx` (already used). Tests with stdlib `unittest` (run via `uv run python -m unittest`). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-06-17-lead-event-dedup-design.md`

---

## File Structure

- **Create** `pipeline/dedup.py` — pure logic: `title_tokens`, `normalize_company`, `same_event_score`, `cluster_leads`, `completeness_key`, `merge_contact_strings`, `LeadRecord`, `lead_record_from_dict`.
- **Create** `pipeline/cli/find_event_candidates.py` — read-only candidate lookup CLI.
- **Create** `pipeline/cli/merge_contacts.py` — merge contacts into a keeper Lead.
- **Create** `pipeline/cli/dedup_backfill.py` — one-time cluster/merge/delete CLI.
- **Modify** `pipeline/email_digest.py` — add `list_raw_leads_since` (lean raw-dict fetch reused by the new CLIs).
- **Modify** `pipeline/cli/mark.py` + `pipeline/db.py` (docstring) — add `merged` status.
- **Modify** `pipeline/config.py` — add `dedup_window_days`, `dedup_score_threshold` settings.
- **Modify** `skill/aether_daily_routine.md` — add the same-event step.
- **Create** tests: `tests/test_dedup.py`, `tests/test_cli_find_event_candidates.py`, `tests/test_cli_merge_contacts.py`, `tests/test_cli_dedup_backfill.py`; extend `tests/test_cli_mark.py`.

Contacts in Pipedrive are stored as the formatted strings `Name | Title | Email | Phone` in the `Lead 1/2/3` custom fields (see `push._fmt_contact`). The merge logic therefore operates on **contact strings**, deduping by the name segment — it never touches the linked Person, Article URL, title, notes, or value (the "contacts only" rule).

---

## Task 1: Add `merged` status to the seen_urls vocabulary

**Files:**
- Modify: `pipeline/cli/mark.py`
- Modify: `pipeline/db.py:6-7` (docstring invariant only)
- Test: `tests/test_cli_mark.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cli_mark.py`:

```python
def test_merged_is_a_valid_status(self):
    from pipeline.cli import mark
    self.assertIn("merged", mark.VALID_STATUSES)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m unittest tests.test_cli_mark -v`
Expected: FAIL — `'merged' not found in {...}`.

- [ ] **Step 3: Add the status**

In `pipeline/cli/mark.py` change:

```python
VALID_STATUSES = {"new", "extracted", "filtered", "pushed", "failed"}
```
to:
```python
VALID_STATUSES = {"new", "extracted", "filtered", "pushed", "failed", "merged"}
```

And update the module docstring line `Valid statuses: ...` to include `merged`. In `pipeline/db.py` update the docstring invariant on lines 6-7 to read `{new, extracted, filtered, pushed, failed, merged}` (no schema change — the column is free-form `TEXT`).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m unittest tests.test_cli_mark -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline/cli/mark.py pipeline/db.py tests/test_cli_mark.py
git commit -m "feat: add 'merged' seen_urls status for event-dedup"
```

---

## Task 2: Add dedup config settings

**Files:**
- Modify: `pipeline/config.py` (the `Settings` dataclass + `settings()` factory)
- Test: `tests/test_dedup.py` (new file — config smoke test lives here to avoid a new test module for two fields)

- [ ] **Step 1: Write the failing test**

Create `tests/test_dedup.py`:

```python
"""Tests for pipeline/dedup.py and its config knobs."""
from __future__ import annotations

import os
import unittest


class TestDedupConfig(unittest.TestCase):
    def test_defaults(self):
        # Re-import with a clean lru_cache so env defaults apply.
        from pipeline import config
        config.settings.cache_clear()
        for k in ("DEDUP_WINDOW_DAYS", "DEDUP_SCORE_THRESHOLD"):
            os.environ.pop(k, None)
        os.environ.setdefault("PIPEDRIVE_API_TOKEN", "t")
        os.environ.setdefault("PIPEDRIVE_DOMAIN", "d")
        os.environ.setdefault("PIPEDRIVE_FIELD_ARTICLE_URL", "f")
        s = config.settings()
        self.assertEqual(s.dedup_window_days, 14)
        self.assertAlmostEqual(s.dedup_score_threshold, 0.5)
        config.settings.cache_clear()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m unittest tests.test_dedup -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'dedup_window_days'`.

- [ ] **Step 3: Add the settings**

In `pipeline/config.py`, add two fields to the `Settings` dataclass (after `max_articles_per_run`):

```python
    dedup_window_days: int = 14
    dedup_score_threshold: float = 0.5
```

And in the `settings()` factory's `return Settings(...)`, add:

```python
        dedup_window_days=int(env.get("DEDUP_WINDOW_DAYS") or 14),
        dedup_score_threshold=float(env.get("DEDUP_SCORE_THRESHOLD") or 0.5),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m unittest tests.test_dedup -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline/config.py tests/test_dedup.py
git commit -m "feat: add DEDUP_WINDOW_DAYS / DEDUP_SCORE_THRESHOLD settings"
```

---

## Task 3: Core normalization — `title_tokens` + `normalize_company`

**Files:**
- Create: `pipeline/dedup.py`
- Test: `tests/test_dedup.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_dedup.py`:

```python
class TestNormalization(unittest.TestCase):
    def test_title_tokens_drops_digits_units_stopwords(self):
        from pipeline import dedup
        toks = dedup.title_tokens(
            "SkySong leasing activity tops 28,000 square feet as ASU Innovation Center"
        )
        self.assertIn("skysong", toks)
        self.assertIn("leasing", toks)
        self.assertIn("innovation", toks)
        self.assertNotIn("28", toks)          # digits dropped
        self.assertNotIn("square", toks)      # unit word dropped
        self.assertNotIn("feet", toks)
        self.assertNotIn("as", toks)          # stopword + too short

    def test_normalize_company_strips_suffix_and_parens(self):
        from pipeline import dedup
        self.assertEqual(
            dedup.normalize_company("Plaza Companies (SkySong)"), "plaza"
        )
        self.assertEqual(dedup.normalize_company("Foundation 8 LLC"), "foundation 8")
        self.assertEqual(
            dedup.normalize_company("Stevens-Leinweber Construction"),
            "stevens-leinweber",
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m unittest tests.test_dedup.TestNormalization -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pipeline.dedup'`.

- [ ] **Step 3: Write the implementation**

Create `pipeline/dedup.py`:

```python
"""Event-level deduplication core — pure logic, no I/O.

Same-event detection across different-URL articles: a cheap title-token
similarity narrows candidates; Claude (in the daily routine) makes the final
call. Also powers the one-time backfill clustering. See
docs/superpowers/specs/2026-06-17-lead-event-dedup-design.md.
"""
from __future__ import annotations

import re

# Unit/measure words and generic filler that carry no event identity.
_NOISE_WORDS = frozenset({
    "square", "feet", "foot", "sqft", "million", "billion", "units", "unit",
    "acre", "acres", "story", "stories", "from", "with", "into", "near",
    "that", "this", "they", "their", "will", "have", "more", "than", "tops",
    "for", "the", "and", "new",
})
# Company suffixes stripped by normalize_company (longest-first matching).
_COMPANY_SUFFIXES = (
    "companies", "company", "construction", "development", "developments",
    "partners", "group", "ventures", "capital", "holdings", "properties",
    "residential", "investments", "associates", "llc", "inc", "lp", "co",
)


def title_tokens(title: str) -> frozenset[str]:
    """Lowercase significant tokens of a headline: drop digits, units, stopwords,
    and tokens shorter than 3 chars."""
    words = re.findall(r"[a-zA-Z][a-zA-Z\-]+", (title or "").lower())
    return frozenset(
        w for w in words if len(w) >= 3 and w not in _NOISE_WORDS
    )


def normalize_company(name: str) -> str:
    """Lowercase a company name, drop parenthetical aliases and legal/suffix
    noise. Conservative: only strips trailing suffix words, never interior ones."""
    n = re.sub(r"\(.*?\)", " ", (name or "").lower())   # drop "(SkySong)"
    n = re.sub(r"[.,]", " ", n)
    parts = [p for p in n.split() if p]
    while parts and parts[-1] in _COMPANY_SUFFIXES:
        parts.pop()
    return " ".join(parts).strip()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m unittest tests.test_dedup.TestNormalization -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline/dedup.py tests/test_dedup.py
git commit -m "feat: dedup core normalization (title_tokens, normalize_company)"
```

---

## Task 4: Same-event scoring + clustering

**Files:**
- Modify: `pipeline/dedup.py`
- Test: `tests/test_dedup.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_dedup.py`:

```python
class TestScoringAndClustering(unittest.TestCase):
    # Real headlines from the 2026-05-29 backfill (same-event vs unrelated).
    SKYSONG_A = "SkySong leasing activity tops 28,000 square feet as ASU Scottsdale Innovation Center marks 20 years"
    SKYSONG_B = "SkySong adds 28,000 square feet of new leases and expansions"
    UNRELATED = "Creation buys 38-acre site to build Avondale Tech Center"

    def test_same_event_scores_high(self):
        from pipeline import dedup
        self.assertGreaterEqual(
            dedup.same_event_score(self.SKYSONG_A, self.SKYSONG_B), 0.5
        )

    def test_unrelated_scores_low(self):
        from pipeline import dedup
        self.assertLess(
            dedup.same_event_score(self.SKYSONG_A, self.UNRELATED), 0.5
        )

    def test_cluster_groups_same_event(self):
        from pipeline import dedup
        recs = [
            dedup.LeadRecord("1", self.SKYSONG_A, None, [], None, 0),
            dedup.LeadRecord("2", self.SKYSONG_B, None, [], None, 0),
            dedup.LeadRecord("3", self.UNRELATED, None, [], None, 0),
        ]
        clusters = dedup.cluster_leads(recs, threshold=0.5)
        sizes = sorted(len(c) for c in clusters)
        self.assertEqual(sizes, [1, 2])  # SkySong pair + lone unrelated
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m unittest tests.test_dedup.TestScoringAndClustering -v`
Expected: FAIL — `AttributeError: module 'pipeline.dedup' has no attribute 'same_event_score'` (and `LeadRecord`).

- [ ] **Step 3: Write the implementation**

Add to `pipeline/dedup.py` (imports at top: add `from dataclasses import dataclass` and `from datetime import datetime`):

```python
@dataclass(slots=True)
class LeadRecord:
    """Minimal projection of a Pipedrive Lead for dedup. `add_epoch` is the
    add_time as a UTC epoch int (0 when unknown); `num_filled` counts non-empty
    meaningful fields for completeness ranking."""
    lead_id: str
    title: str
    url: str | None
    contacts: list[str]
    add_dt: datetime | None
    num_filled: int


def same_event_score(title_a: str, title_b: str) -> float:
    """Jaccard overlap of significant title tokens (0..1)."""
    a, b = title_tokens(title_a), title_tokens(title_b)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def cluster_leads(leads: list[LeadRecord], threshold: float) -> list[list[LeadRecord]]:
    """Connected-components clustering: leads are in the same cluster if a chain
    of pairwise same_event_score >= threshold links them."""
    n = len(leads)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        for j in range(i + 1, n):
            if same_event_score(leads[i].title, leads[j].title) >= threshold:
                parent[find(i)] = find(j)

    groups: dict[int, list[LeadRecord]] = {}
    for i, lead in enumerate(leads):
        groups.setdefault(find(i), []).append(lead)
    return list(groups.values())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m unittest tests.test_dedup.TestScoringAndClustering -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline/dedup.py tests/test_dedup.py
git commit -m "feat: same-event scoring + connected-components clustering"
```

---

## Task 5: Completeness ranking + contact-string merge

**Files:**
- Modify: `pipeline/dedup.py`
- Test: `tests/test_dedup.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_dedup.py` (add `from datetime import datetime, timezone` to the test imports):

```python
class TestKeeperAndMerge(unittest.TestCase):
    def _rec(self, lid, contacts, num_filled, day):
        from pipeline import dedup
        return dedup.LeadRecord(
            lid, "t", "u", contacts,
            datetime(2026, 5, day, tzinfo=timezone.utc), num_filled,
        )

    def test_keeper_prefers_most_contacts_then_fields_then_earliest(self):
        from pipeline import dedup
        a = self._rec("a", ["X | CEO"], num_filled=3, day=30)
        b = self._rec("b", ["X | CEO", "Y | COO"], num_filled=2, day=31)  # more contacts
        c = self._rec("c", ["X | CEO", "Y | COO"], num_filled=2, day=29)  # tie -> earliest
        keeper = max([a, b, c], key=dedup.completeness_key)
        self.assertEqual(keeper.lead_id, "c")

    def test_merge_dedups_by_name_keeps_keeper_first_caps_at_3(self):
        from pipeline import dedup
        res = dedup.merge_contact_strings(
            existing=["Jane Doe | CEO | jane@x.com"],
            incoming=[
                "Jane Doe | Chief Executive",          # same person, diff text -> dropped
                "Bob Smith | COO | bob@x.com",
                "Cara Lee | VP",
                "Dan Poe | Director",                  # 4th unique -> overflow
            ],
        )
        self.assertEqual(res.kept[0], "Jane Doe | CEO | jane@x.com")  # keeper first
        self.assertEqual(len(res.kept), 3)
        self.assertEqual([c.split(" | ")[0] for c in res.kept],
                         ["Jane Doe", "Bob Smith", "Cara Lee"])
        self.assertEqual([c.split(" | ")[0] for c in res.overflow], ["Dan Poe"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m unittest tests.test_dedup.TestKeeperAndMerge -v`
Expected: FAIL — `AttributeError: ... 'completeness_key'`.

- [ ] **Step 3: Write the implementation**

Add to `pipeline/dedup.py`:

```python
_MAX_CONTACTS = 3  # Pipedrive Lead 1/2/3 fields


@dataclass(slots=True)
class MergeResult:
    kept: list[str]       # final contact strings to write to Lead 1/2/3
    overflow: list[str]   # unique contacts that didn't fit (logged, not written)


def completeness_key(lead: LeadRecord):
    """Sort key for keeper selection. max() picks: most contacts, then most
    filled fields, then EARLIEST add_time (negated epoch makes earliest largest)."""
    epoch = lead.add_dt.timestamp() if lead.add_dt else 0.0
    return (len(lead.contacts), lead.num_filled, -epoch)


def _contact_name_key(contact: str) -> str:
    """Identity for contact dedup: normalized name segment (before first ' | ')."""
    name = contact.split(" | ", 1)[0]
    return re.sub(r"\s+", " ", name).strip().lower()


def merge_contact_strings(existing: list[str], incoming: list[str]) -> MergeResult:
    """Union existing + incoming contact strings, dedup by name, keeper's first,
    cap at 3. Anything beyond the cap is returned as overflow (never silently
    dropped — callers log it)."""
    kept: list[str] = []
    overflow: list[str] = []
    seen: set[str] = set()
    for contact in [*existing, *incoming]:
        if not contact or not contact.strip():
            continue
        key = _contact_name_key(contact)
        if key in seen:
            continue
        seen.add(key)
        (kept if len(kept) < _MAX_CONTACTS else overflow).append(contact)
    return MergeResult(kept=kept, overflow=overflow)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m unittest tests.test_dedup.TestKeeperAndMerge -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline/dedup.py tests/test_dedup.py
git commit -m "feat: completeness ranking + contact-string merge"
```

---

## Task 6: `LeadRecord` adapter + raw-lead fetch helper

**Files:**
- Modify: `pipeline/dedup.py` (add `lead_record_from_dict`)
- Modify: `pipeline/email_digest.py` (add `list_raw_leads_since`)
- Test: `tests/test_dedup.py`, `tests/test_email_digest.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_dedup.py`:

```python
class TestAdapter(unittest.TestCase):
    def test_lead_record_from_dict_reads_url_contacts_and_fields(self):
        from pipeline import dedup
        lead = {
            "id": "uuid-1",
            "title": "SkySong adds 28,000 square feet",
            "add_time": "2026-05-29 23:31:39",
            "value": {"amount": 588000, "currency": "USD"},
            "person_id": 42,
            "URLHASH": "https://example.com/skysong",
            "L1": "Jane Doe | CEO | jane@x.com",
            "L2": {"value": "Bob Smith | COO"},   # nested shape
            "L3": None,
        }
        rec = dedup.lead_record_from_dict(
            lead, article_url_field="URLHASH",
            lead_fields=("L1", "L2", "L3"),
        )
        self.assertEqual(rec.lead_id, "uuid-1")
        self.assertEqual(rec.url, "https://example.com/skysong")
        self.assertEqual(rec.contacts, ["Jane Doe | CEO | jane@x.com", "Bob Smith | COO"])
        self.assertIsNotNone(rec.add_dt)
        # num_filled counts: url + value + person present = 3
        self.assertEqual(rec.num_filled, 3)
```

Add to `tests/test_email_digest.py`:

```python
class TestListRawLeadsSince(unittest.TestCase):
    def test_paginates_and_filters_by_add_time(self):
        from datetime import datetime, timezone
        from unittest import mock
        from pipeline import email_digest, config

        config.settings.cache_clear()
        import os
        os.environ.setdefault("PIPEDRIVE_API_TOKEN", "t")
        os.environ.setdefault("PIPEDRIVE_DOMAIN", "d")
        os.environ.setdefault("PIPEDRIVE_FIELD_ARTICLE_URL", "f")
        settings = config.settings()

        pages = [
            {"data": [
                {"id": "old", "add_time": "2026-05-01 00:00:00"},
                {"id": "new", "add_time": "2026-06-01 00:00:00"},
            ], "additional_data": {"pagination": {"more_items_in_collection": False}}},
        ]
        http = mock.Mock()
        http.get.return_value.json.return_value = pages[0]
        http.get.return_value.raise_for_status.return_value = None

        since = datetime(2026, 5, 29, tzinfo=timezone.utc)
        out = email_digest.list_raw_leads_since(http, settings, since)
        self.assertEqual([l["id"] for l in out], ["new"])
        config.settings.cache_clear()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m unittest tests.test_dedup.TestAdapter tests.test_email_digest.TestListRawLeadsSince -v`
Expected: FAIL — `lead_record_from_dict` / `list_raw_leads_since` undefined.

- [ ] **Step 3: Write the implementations**

Add to `pipeline/dedup.py` (uses the existing `email_digest._lead_add_dt` parser via a local import to avoid duplicating date logic):

```python
def _cf(lead: dict, key: str):
    """Read a custom field that may be a bare value or a nested {value:...}."""
    v = lead.get(key)
    if isinstance(v, dict):
        return v.get("value")
    return v


def lead_record_from_dict(
    lead: dict, *, article_url_field: str, lead_fields: tuple[str | None, ...],
) -> LeadRecord:
    """Project a raw Pipedrive Lead dict into a LeadRecord."""
    from pipeline.email_digest import _lead_add_dt  # reuse the tolerant parser

    contacts = [str(c) for f in lead_fields if f and (c := _cf(lead, f))]
    url = _cf(lead, article_url_field)
    has_value = bool(lead.get("value"))
    has_person = lead.get("person_id") is not None
    num_filled = sum((bool(url), has_value, has_person))
    return LeadRecord(
        lead_id=str(lead.get("id")),
        title=lead.get("title") or "",
        url=str(url) if url else None,
        contacts=contacts,
        add_dt=_lead_add_dt(lead),
        num_filled=num_filled,
    )
```

Add to `pipeline/email_digest.py` (place near `list_leads_since`; reuse `_lead_add_dt` and `_LEADS_PAGE_SIZE` already defined in that module):

```python
def list_raw_leads_since(
    http: httpx.Client, settings: Settings, since: datetime,
) -> list[dict]:
    """Raw Lead dicts with add_time (UTC) >= `since`, newest first. Same
    pagination + post-filter as list_leads_since, but returns the unenriched
    dicts (custom fields intact) for the dedup CLIs."""
    matches: list[dict] = []
    start = 0
    while True:
        resp = http.get("leads", params={"limit": _LEADS_PAGE_SIZE, "start": start})
        resp.raise_for_status()
        body = resp.json()
        for lead in body.get("data") or []:
            dt = _lead_add_dt(lead)
            if dt is not None and dt >= since:
                matches.append(lead)
        pagination = (body.get("additional_data") or {}).get("pagination") or {}
        if not pagination.get("more_items_in_collection"):
            break
        start = pagination.get("next_start")
        if start is None:
            break
    matches.sort(key=lambda l: l.get("add_time") or "", reverse=True)
    return matches
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m unittest tests.test_dedup.TestAdapter tests.test_email_digest.TestListRawLeadsSince -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline/dedup.py pipeline/email_digest.py tests/test_dedup.py tests/test_email_digest.py
git commit -m "feat: LeadRecord adapter + raw-lead fetch helper"
```

---

## Task 7: `find_event_candidates` CLI (read-only)

**Files:**
- Create: `pipeline/cli/find_event_candidates.py`
- Test: `tests/test_cli_find_event_candidates.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli_find_event_candidates.py`:

```python
"""Tests for pipeline.cli.find_event_candidates."""
from __future__ import annotations

import io
import json
import os
import unittest
from unittest import mock


class TestFindEventCandidates(unittest.TestCase):
    def setUp(self):
        from pipeline import config
        config.settings.cache_clear()
        os.environ.update({
            "PIPEDRIVE_API_TOKEN": "t", "PIPEDRIVE_DOMAIN": "d",
            "PIPEDRIVE_FIELD_ARTICLE_URL": "URLHASH",
            "PIPEDRIVE_FIELD_LEAD_1": "L1",
        })

    def tearDown(self):
        from pipeline import config
        config.settings.cache_clear()

    def _run(self, article, raw_leads):
        from pipeline.cli import find_event_candidates as cli
        with mock.patch.object(cli.email_digest, "make_pipedrive_client"), \
             mock.patch.object(cli.email_digest, "list_raw_leads_since",
                               return_value=raw_leads), \
             mock.patch("sys.stdin", io.StringIO(json.dumps(article))), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = cli.main()
        return rc, json.loads(out.getvalue())

    def test_returns_scored_same_event_candidate(self):
        article = {"title": "SkySong adds 28,000 square feet of new leases",
                   "company_name": "Plaza Companies", "city": "Scottsdale",
                   "signal_type": "lease"}
        leads = [
            {"id": "1", "title": "SkySong leasing activity tops 28,000 square feet at ASU",
             "URLHASH": "u1", "L1": "Jane | CEO", "add_time": "2026-06-10 00:00:00"},
            {"id": "2", "title": "Creation buys 38-acre site in Avondale",
             "URLHASH": "u2", "add_time": "2026-06-10 00:00:00"},
        ]
        rc, payload = self._run(article, leads)
        self.assertEqual(rc, 0)
        ids = [c["lead_id"] for c in payload]
        self.assertIn("1", ids)         # same event surfaced
        self.assertNotIn("2", ids)      # unrelated filtered out

    def test_pipedrive_error_fails_open_to_empty(self):
        from pipeline.cli import find_event_candidates as cli
        article = {"title": "x", "company_name": "y", "city": None, "signal_type": "other"}
        with mock.patch.object(cli.email_digest, "make_pipedrive_client",
                               side_effect=RuntimeError("boom")), \
             mock.patch("sys.stdin", io.StringIO(json.dumps(article))), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = cli.main()
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out.getvalue()), [])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m unittest tests.test_cli_find_event_candidates -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Write the implementation**

Create `pipeline/cli/find_event_candidates.py`:

```python
"""`python -m pipeline.cli.find_event_candidates` (stdin JSON) — read-only.

Input:  an extracted-article object (needs at least `title`).
Output: JSON array of recent Leads that may describe the SAME news event, for
        Claude to judge. Shape:
        [{"lead_id","title","url","contacts":[...],"score":0.0-1.0}, ...]

Fails OPEN: any Pipedrive error prints `[]` and exits 0, so a transient outage
degrades to today's behavior (create the Lead) — never to a wrong merge.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone

from pipeline import config, dedup, email_digest, util


def main() -> int:
    article = json.load(sys.stdin)
    title = article.get("title") or ""
    settings = config.settings()
    lead_fields = (
        settings.pipedrive_field_lead_1,
        settings.pipedrive_field_lead_2,
        settings.pipedrive_field_lead_3,
    )
    since = datetime.now(timezone.utc) - timedelta(days=settings.dedup_window_days)

    try:
        with email_digest.make_pipedrive_client(settings) as http:
            raw = email_digest.list_raw_leads_since(http, settings, since)
    except Exception as e:  # fail open — see module docstring
        util.log_event("candidate_lookup_failed", error=repr(e))
        json.dump([], sys.stdout)
        sys.stdout.write("\n")
        return 0

    out = []
    for lead in raw:
        score = dedup.same_event_score(title, lead.get("title") or "")
        if score >= settings.dedup_score_threshold:
            rec = dedup.lead_record_from_dict(
                lead, article_url_field=settings.pipedrive_field_article_url,
                lead_fields=lead_fields,
            )
            out.append({"lead_id": rec.lead_id, "title": rec.title,
                        "url": rec.url, "contacts": rec.contacts,
                        "score": round(score, 3)})
    out.sort(key=lambda c: c["score"], reverse=True)
    json.dump(out, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m unittest tests.test_cli_find_event_candidates -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline/cli/find_event_candidates.py tests/test_cli_find_event_candidates.py
git commit -m "feat: find_event_candidates CLI (read-only candidate lookup)"
```

---

## Task 8: `merge_contacts` CLI (merge, never delete)

**Files:**
- Create: `pipeline/cli/merge_contacts.py`
- Test: `tests/test_cli_merge_contacts.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli_merge_contacts.py`:

```python
"""Tests for pipeline.cli.merge_contacts."""
from __future__ import annotations

import io
import json
import os
import unittest
from unittest import mock


def _settings(dry):
    from pipeline import config
    config.settings.cache_clear()
    os.environ.update({
        "PIPEDRIVE_API_TOKEN": "t", "PIPEDRIVE_DOMAIN": "d",
        "PIPEDRIVE_FIELD_ARTICLE_URL": "URLHASH",
        "PIPEDRIVE_FIELD_LEAD_1": "L1", "PIPEDRIVE_FIELD_LEAD_2": "L2",
        "PIPEDRIVE_FIELD_LEAD_3": "L3", "DRY_RUN": "1" if dry else "0",
    })
    return config.settings()


class TestMergeContacts(unittest.TestCase):
    def tearDown(self):
        from pipeline import config
        config.settings.cache_clear()

    def test_merges_into_empty_slots_and_patches(self):
        _settings(dry=False)
        from pipeline.cli import merge_contacts as cli
        keeper = {"id": "k", "L1": "Jane | CEO", "L2": None, "L3": None}
        pd = mock.MagicMock()
        pd.get.return_value = keeper
        with mock.patch.object(cli.push, "PipedriveClient") as PC, \
             mock.patch("sys.stdin", io.StringIO(json.dumps({
                 "keeper_lead_id": "k",
                 "contacts": ["Bob | COO", "Jane | Chief Exec"],  # Jane dup by name
             }))):
            PC.return_value.__enter__.return_value = pd
            rc = cli.main()
        self.assertEqual(rc, 0)
        # PATCH called with L1 kept, L2 filled with Bob, Jane-dup dropped.
        patched = pd.patch.call_args.args[2]
        self.assertEqual(patched.get("L2"), "Bob | COO")
        self.assertNotIn("Jane | Chief Exec", patched.values())

    def test_dry_run_does_not_patch(self):
        _settings(dry=True)
        from pipeline.cli import merge_contacts as cli
        pd = mock.MagicMock()
        pd.get.return_value = {"id": "k", "L1": None, "L2": None, "L3": None}
        with mock.patch.object(cli.push, "PipedriveClient") as PC, \
             mock.patch("sys.stdin", io.StringIO(json.dumps({
                 "keeper_lead_id": "k", "contacts": ["Bob | COO"]}))):
            PC.return_value.__enter__.return_value = pd
            rc = cli.main()
        self.assertEqual(rc, 0)
        pd.patch.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m unittest tests.test_cli_merge_contacts -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Write the implementation**

Create `pipeline/cli/merge_contacts.py`:

```python
"""`python -m pipeline.cli.merge_contacts` (stdin JSON) — merge contacts into a
keeper Lead. NEVER deletes.

Input: {
  "keeper_lead_id": "uuid",
  "contacts": ["Name | Title | Email | Phone", ...],  # contacts to ADD
  "merged_url": "https://..."   # optional: mark its url_hash 'merged' + breadcrumb
}
Output: {"keeper_lead_id","written":{<field>:<str>},"overflow":[...],"dry_run":bool}

Only the Lead 1/2/3 custom fields are touched (contacts only). Title, notes,
value, Article URL, and the linked Person are left exactly as the keeper's.
Honors DRY_RUN (logs, no writes).
"""
from __future__ import annotations

import json
import sys

from pipeline import config, dedup, push, util


def main() -> int:
    raw = json.load(sys.stdin)
    keeper_id = raw["keeper_lead_id"]
    incoming = [str(c) for c in (raw.get("contacts") or [])]
    merged_url = raw.get("merged_url")

    settings = config.settings()
    lead_fields = (
        settings.pipedrive_field_lead_1,
        settings.pipedrive_field_lead_2,
        settings.pipedrive_field_lead_3,
    )

    with push.PipedriveClient(settings) as pd:
        keeper = pd.get("leads", keeper_id)
        existing = [str(c) for f in lead_fields if f and (c := dedup._cf(keeper, f))]
        result = dedup.merge_contact_strings(existing, incoming)
        if result.overflow:
            util.log_event("merge_contacts_overflow", keeper=keeper_id,
                           dropped=len(result.overflow))

        # Map the kept contacts back onto Lead 1/2/3 in order.
        written = {f: val for f, val in zip(lead_fields, result.kept) if f}

        if settings.dry_run:
            util.log_event("dry_run_merge_contacts", keeper=keeper_id,
                           written=len(written), merged_url=merged_url)
            json.dump({"keeper_lead_id": keeper_id, "written": written,
                       "overflow": result.overflow, "dry_run": True}, sys.stdout)
            sys.stdout.write("\n")
            return 0

        if written:
            pd.patch("leads", keeper_id, written)
        if merged_url:
            pd.post("notes", {"lead_id": keeper_id,
                              "content": f"merged via event-dedup: {merged_url}"})

    if merged_url:
        # Mark the merged-away URL so a future fetch never re-creates it.
        from pipeline import db
        conn = db.connect()
        try:
            db.mark_seen_status(conn, util.sha256_hex(util.canonicalize_url(merged_url)), "merged")
            conn.commit()
        finally:
            conn.close()

    util.log_event("merge_contacts_done", keeper=keeper_id, written=len(written))
    json.dump({"keeper_lead_id": keeper_id, "written": written,
               "overflow": result.overflow, "dry_run": False}, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m unittest tests.test_cli_merge_contacts -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline/cli/merge_contacts.py tests/test_cli_merge_contacts.py
git commit -m "feat: merge_contacts CLI (contacts-only merge, never deletes)"
```

---

## Task 9: `dedup_backfill` CLI — dry-run (plan emission)

**Files:**
- Create: `pipeline/cli/dedup_backfill.py`
- Test: `tests/test_cli_dedup_backfill.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli_dedup_backfill.py`:

```python
"""Tests for pipeline.cli.dedup_backfill."""
from __future__ import annotations

import io
import json
import os
import unittest
from unittest import mock


def _settings():
    from pipeline import config
    config.settings.cache_clear()
    os.environ.update({
        "PIPEDRIVE_API_TOKEN": "t", "PIPEDRIVE_DOMAIN": "d",
        "PIPEDRIVE_FIELD_ARTICLE_URL": "URLHASH",
        "PIPEDRIVE_FIELD_LEAD_1": "L1", "PIPEDRIVE_FIELD_LEAD_2": "L2",
        "PIPEDRIVE_FIELD_LEAD_3": "L3", "DEDUP_SCORE_THRESHOLD": "0.5",
    })
    return config.settings()


SKYSONG_A = {"id": "a", "title": "SkySong leasing activity tops 28,000 square feet at ASU",
             "URLHASH": "ua", "L1": "Jane | CEO", "L2": "Bob | COO",
             "add_time": "2026-05-29 10:00:00", "value": {"amount": 1}, "person_id": 1}
SKYSONG_B = {"id": "b", "title": "SkySong adds 28,000 square feet of new leases",
             "URLHASH": "ub", "L1": "Cara | VP", "add_time": "2026-05-30 10:00:00"}
LONE = {"id": "c", "title": "Creation buys 38-acre site in Avondale",
        "URLHASH": "uc", "add_time": "2026-05-31 10:00:00"}


class TestBackfillDryRun(unittest.TestCase):
    def tearDown(self):
        from pipeline import config
        config.settings.cache_clear()

    def test_dry_run_emits_plan_with_keeper_and_merged_contacts(self):
        _settings()
        from pipeline.cli import dedup_backfill as cli
        with mock.patch.object(cli.email_digest, "make_pipedrive_client"), \
             mock.patch.object(cli.email_digest, "list_raw_leads_since",
                               return_value=[SKYSONG_A, SKYSONG_B, LONE]), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = cli.main(["--since", "2026-05-29"])
        self.assertEqual(rc, 0)
        plan = json.loads(out.getvalue())
        # Only the SkySong cluster (>1) appears; lone lead is excluded.
        self.assertEqual(len(plan["clusters"]), 1)
        cl = plan["clusters"][0]
        self.assertEqual(cl["keeper_lead_id"], "a")     # more contacts + fields
        self.assertEqual(cl["delete_lead_ids"], ["b"])
        self.assertIn("Cara | VP", cl["merged_contacts"])  # B's contact carried over
        self.assertEqual(plan["summary"]["leads_deleted"], 1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m unittest tests.test_cli_dedup_backfill -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Write the implementation**

Create `pipeline/cli/dedup_backfill.py`:

```python
"""`python -m pipeline.cli.dedup_backfill --since YYYY-MM-DD [--apply]`.

DRY-RUN BY DEFAULT. Clusters Leads created on/after `--since` by same-event
title similarity, picks the most-complete keeper per cluster, and computes the
merged contact set. Without --apply it only PRINTS the plan (JSON) for review.
With --apply it executes: merge contacts into the keeper FIRST, then delete the
losers — and deletes nothing for a cluster whose merge failed.

Plan shape:
{
  "summary": {"clusters": N, "leads_deleted": M},
  "clusters": [
    {"keeper_lead_id","keeper_title","delete_lead_ids":[...],
     "merged_contacts":[...],"overflow":[...]}, ...
  ]
}
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone

from pipeline import config, dedup, email_digest, push, util


def _build_plan(raw_leads: list[dict], settings) -> dict:
    lead_fields = (
        settings.pipedrive_field_lead_1,
        settings.pipedrive_field_lead_2,
        settings.pipedrive_field_lead_3,
    )
    recs = [
        dedup.lead_record_from_dict(
            l, article_url_field=settings.pipedrive_field_article_url,
            lead_fields=lead_fields,
        )
        for l in raw_leads
    ]
    by_id = {r.lead_id: r for r in recs}
    clusters_out = []
    for cluster in dedup.cluster_leads(recs, settings.dedup_score_threshold):
        if len(cluster) < 2:
            continue
        keeper = max(cluster, key=dedup.completeness_key)
        losers = [r for r in cluster if r.lead_id != keeper.lead_id]
        incoming = [c for r in losers for c in r.contacts]
        merged = dedup.merge_contact_strings(keeper.contacts, incoming)
        clusters_out.append({
            "keeper_lead_id": keeper.lead_id,
            "keeper_title": keeper.title,
            "delete_lead_ids": [r.lead_id for r in losers],
            "delete_urls": [by_id[r.lead_id].url for r in losers],
            "merged_contacts": merged.kept,
            "overflow": merged.overflow,
        })
    deleted = sum(len(c["delete_lead_ids"]) for c in clusters_out)
    return {"summary": {"clusters": len(clusters_out), "leads_deleted": deleted},
            "clusters": clusters_out}


def _parse_args(argv):
    p = argparse.ArgumentParser(prog="pipeline.cli.dedup_backfill")
    p.add_argument("--since", required=True, metavar="YYYY-MM-DD")
    p.add_argument("--apply", action="store_true",
                   help="Execute the plan (merge then delete). Default: dry-run.")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        d = date.fromisoformat(args.since)
    except ValueError:
        sys.stderr.write(f"Invalid --since date: {args.since!r}\n")
        return 2
    since = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)

    settings = config.settings()
    with email_digest.make_pipedrive_client(settings) as http:
        raw = email_digest.list_raw_leads_since(http, settings, since)
    plan = _build_plan(raw, settings)

    if not args.apply:
        json.dump(plan, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    return _apply(plan, settings)


def _apply(plan: dict, settings) -> int:
    """Merge contacts into each keeper, then delete that cluster's losers.
    A cluster whose merge raises is skipped and its losers are NOT deleted."""
    lead_fields = (
        settings.pipedrive_field_lead_1,
        settings.pipedrive_field_lead_2,
        settings.pipedrive_field_lead_3,
    )
    deleted = 0
    with push.PipedriveClient(settings) as pd:
        for cl in plan["clusters"]:
            keeper_id = cl["keeper_lead_id"]
            try:
                written = {f: val for f, val in zip(lead_fields, cl["merged_contacts"]) if f}
                if written and not settings.dry_run:
                    pd.patch("leads", keeper_id, written)
            except Exception as e:
                util.log_event("backfill_merge_failed", keeper=keeper_id, error=repr(e))
                continue  # never delete when the merge failed
            for lid in cl["delete_lead_ids"]:
                if not settings.dry_run:
                    pd.delete("leads", lid)
                deleted += 1
            util.log_event("backfill_cluster_done", keeper=keeper_id,
                           deleted=len(cl["delete_lead_ids"]), dry_run=settings.dry_run)
    json.dump({"applied": True, "leads_deleted": deleted,
               "dry_run": settings.dry_run}, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m unittest tests.test_cli_dedup_backfill -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline/cli/dedup_backfill.py tests/test_cli_dedup_backfill.py
git commit -m "feat: dedup_backfill CLI dry-run (plan emission)"
```

---

## Task 10: `dedup_backfill --apply` — add `delete` verb + merge-before-delete test

**Files:**
- Modify: `pipeline/push.py` (add a `delete` verb to `PipedriveClient`)
- Test: `tests/test_cli_dedup_backfill.py`, `tests/test_push.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_push.py`:

```python
def test_client_has_delete_verb(self):
    from pipeline import push, config
    config.settings.cache_clear()
    import os
    os.environ.update({"PIPEDRIVE_API_TOKEN": "t", "PIPEDRIVE_DOMAIN": "d",
                       "PIPEDRIVE_FIELD_ARTICLE_URL": "f"})
    c = push.PipedriveClient(config.settings())
    self.assertTrue(hasattr(c, "delete"))
    c.__exit__()
    config.settings.cache_clear()
```

Add to `tests/test_cli_dedup_backfill.py`:

```python
class TestBackfillApply(unittest.TestCase):
    def tearDown(self):
        from pipeline import config
        config.settings.cache_clear()

    def test_apply_merges_then_deletes_and_skips_on_merge_failure(self):
        _settings()
        from pipeline.cli import dedup_backfill as cli
        plan = {"clusters": [
            {"keeper_lead_id": "a", "merged_contacts": ["Jane | CEO"],
             "delete_lead_ids": ["b"], "delete_urls": ["ub"], "overflow": []},
            {"keeper_lead_id": "x", "merged_contacts": ["Z | CTO"],
             "delete_lead_ids": ["y"], "delete_urls": ["uy"], "overflow": []},
        ], "summary": {"clusters": 2, "leads_deleted": 2}}

        pd = mock.MagicMock()
        order = []

        def fake_patch(resource, lead_id, payload):
            order.append(("patch", lead_id))
            if lead_id != "a":               # second cluster's merge fails
                raise RuntimeError("patch boom")

        def fake_delete(resource, lead_id):
            order.append(("delete", lead_id))

        pd.patch.side_effect = fake_patch
        pd.delete.side_effect = fake_delete

        with mock.patch.object(cli.push, "PipedriveClient") as PC, \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            PC.return_value.__enter__.return_value = pd
            rc = cli._apply(plan, cli.config.settings())
        self.assertEqual(rc, 0)
        # 'a' merged then 'b' deleted; 'x' merge failed so 'y' NOT deleted.
        self.assertIn(("patch", "a"), order)
        self.assertIn(("delete", "b"), order)
        self.assertNotIn(("delete", "y"), order)
        self.assertLess(order.index(("patch", "a")), order.index(("delete", "b")))
        result = json.loads(out.getvalue())
        self.assertEqual(result["leads_deleted"], 1)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m unittest tests.test_push.TestPipedriveClient.test_client_has_delete_verb tests.test_cli_dedup_backfill.TestBackfillApply -v`
Expected: FAIL — `PipedriveClient` has no `delete`. (Match the existing class name in `tests/test_push.py`; if the client tests live in a different class, add the method test there.)

- [ ] **Step 3: Add the `delete` verb**

In `pipeline/push.py`, inside `PipedriveClient` (after `patch`), add:

```python
    def delete(self, resource: str, id) -> dict:
        """DELETE {resource}/{id}. Used by the one-time dedup backfill."""
        return self._req("DELETE", f"{resource}/{id}")
```

(`_apply` was already written in Task 9; this task makes its `pd.delete(...)` call real and proves the merge-before-delete ordering + skip-on-failure guarantee.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m unittest tests.test_push tests.test_cli_dedup_backfill -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline/push.py tests/test_push.py tests/test_cli_dedup_backfill.py
git commit -m "feat: PipedriveClient.delete + backfill apply (merge-before-delete)"
```

---

## Task 11: Wire the same-event step into the daily routine

**Files:**
- Modify: `skill/aether_daily_routine.md`

- [ ] **Step 1: Read the current push step**

Run: `grep -n "cli.push\|Step 3\|Step 4\|## Step" skill/aether_daily_routine.md`
Identify the step where, after qualify + enrich, the article is pushed via `pipeline.cli.push`.

- [ ] **Step 2: Insert the same-event sub-step immediately before push**

Add this section right before the push call (adapt the surrounding variable names — `/tmp/extracted.json`, `$URL`, `$URL_HASH` — to match the existing routine):

```markdown
### 2x. Same-event dedup check (before push)

Before pushing, check whether this article describes the SAME news event as a
recent Lead (a different article about the same story):

```bash
uv run python -m pipeline.cli.find_event_candidates < /tmp/extracted.json > /tmp/candidates.json
jq length /tmp/candidates.json
```

If 0 candidates, push as normal (next step).

If there are candidates, read `/tmp/candidates.json` and compare each candidate's
title against this article. **Bias to keeping separate** — only treat it as the
same event when you are confident (same property/project/transaction, not merely
the same company or city). Two different deals by the same developer are NOT the
same event.

- **If a candidate IS the same event:** merge this article's contacts into that
  Lead instead of creating a new one. Build the contacts list from this article's
  enriched contact(s) (the `Name | Title | Email | Phone` strings you would have
  pushed), then:

  ```bash
  echo '{"keeper_lead_id":"<candidate lead_id>","contacts":[<contact strings>],"merged_url":"<this article url>"}' \
    | uv run python -m pipeline.cli.merge_contacts
  uv run python -m pipeline.cli.mark "$URL_HASH" merged
  ```

  Skip the push step for this article.

- **If NO candidate is the same event:** proceed to push as normal.
```

- [ ] **Step 3: Verify the routine still reads coherently**

Run: `sed -n '/Same-event dedup/,/proceed to push/p' skill/aether_daily_routine.md`
Expected: the new section renders with both branches and the correct surrounding variable names.

- [ ] **Step 4: Commit**

```bash
git add skill/aether_daily_routine.md
git commit -m "feat: daily routine same-event dedup step (skip-and-merge)"
```

---

## Task 12: Full suite + backfill dry-run rehearsal

**Files:** none (verification only)

- [ ] **Step 1: Run the whole test suite**

Run: `uv run python -m unittest discover tests -v`
Expected: all tests pass (the prior 44 + the new dedup/CLI tests).

- [ ] **Step 2: Tune the threshold against real data (dry-run, read-only)**

With the prod env sourced, run the backfill dry-run and confirm it proposes the expected merges WITHOUT applying:

```bash
source ~/.aether-pipedrive-prod.env
uv run python -m pipeline.cli.dedup_backfill --since 2026-05-29 | jq '.summary'
```

Expected: `leads_deleted` ≈ 12–13 (digest 83 → ~70). If the count is off, adjust
`DEDUP_SCORE_THRESHOLD` (lower merges more, higher merges less) and re-run. This
step performs **no writes** — `--apply` is intentionally omitted.

- [ ] **Step 3: Commit any threshold tuning**

If you changed the default threshold in `config.py`, commit it:

```bash
git add pipeline/config.py
git commit -m "chore: tune DEDUP_SCORE_THRESHOLD to reproduce 83->70 on real data"
```

> **STOP — supervised gate.** Do not run `dedup_backfill --apply` (the destructive
> step) as part of plan execution. The operator reviews the dry-run plan and
> triggers `--apply` separately, exactly as the spec's supervised-deletion
> requirement states.

---

## Self-Review Notes

- **Spec coverage:** core module (Tasks 3–5), candidate lookup (Task 7), merge-no-delete (Task 8), supervised backfill dry-run + apply with merge-before-delete and skip-on-failure (Tasks 9–10), `merged` status (Task 1), config knobs (Task 2), routine step + conservative bias (Task 11), 83→70 success criterion (Task 12). Out-of-scope items (pre-05-29 graveyard, URL-gate changes, field merges) are untouched.
- **No deletion outside the supervised backfill:** the going-forward path (Task 11) only ever calls `merge_contacts` (no delete verb reached) + `mark merged`; `delete` exists solely on `PipedriveClient` for `dedup_backfill --apply`, which Task 12 explicitly gates behind operator review.
- **Type consistency:** `LeadRecord(lead_id, title, url, contacts, add_dt, num_filled)` is constructed identically in tests and `lead_record_from_dict`; `merge_contact_strings → MergeResult(kept, overflow)` used consistently in Tasks 5/8/9; `same_event_score`, `cluster_leads`, `completeness_key` names match across tasks.
