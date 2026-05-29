# Org Cache + Maricopa Assessor Lookup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Two enrichment improvements on top of the Grok flow: (1) an SQLite-backed org cache so repeat companies skip the Grok query, and (2) a Maricopa County Assessor scraper that, when an article supplies an AZ property address, returns the owning entity — used to enrich the Grok query for property-acquisition signals.

**Architecture:**

1. **Org cache** lives in the existing `db.sqlite` as `enriched_orgs(name_normalized PRIMARY KEY, raw_name, lead_json, source, enriched_at)`. Two helpers in `pipeline/db.py`: `get_cached_enrichment(conn, name)` and `cache_enrichment(conn, name, lead, source)`. Name normalization: lowercase, strip business suffixes (LLC/Inc/Corp/Ltd/LP/LLP), collapse non-alphanumeric. The routine consults the cache before any external enrichment call and writes any successful enrichment back.

2. **Maricopa Assessor lookup** is a pure-Python httpx scrape (no captcha, verified 2026-05-22) of `mcassessor.maricopa.gov/mcs/?q=<address>`. Returns the top-result owning entity name + mailing address. New module `pipeline/assessor.py` with a CLI shim `pipeline/cli/assessor_lookup.py`. The routine calls it when the article includes a Maricopa address, then feeds the entity name into the Grok query as an owner-hint.

**Tech Stack:** Python 3.12 stdlib + existing deps (`httpx`, `sqlite3`). No new third-party deps.

**Why AZCC is NOT in this plan:** Validated 2026-05-22 that `arizonabusinesscenter.azcc.gov/businesssearch` puts a captcha on every search. Pure scraping is infeasible. Browser-automated AZCC (via Claude in Chrome) would have the same brittleness + serial bottleneck as Grok itself, with marginal added value since Grok's index already covers AZCC public records. **Defer to Grok for principals research; let it consult AZCC when it judges it relevant.**

---

## Open items

1. **Tucson (Pima County) Assessor** — different URL (`asr.pima.gov`), different HTML, not addressed in this plan. Out of scope for v1; Tucson articles fall back to Grok-only.
2. **Address presence rate** — most Google News headline-only articles don't include a full street address. The Assessor lookup will only fire on the subset of articles where extract.py captures one. Expect this to be a minority of articles (rough guess: 20-40%) — still net positive for those.
3. **Cache TTL** — none in v1. Once enriched, results are kept indefinitely. Revisit if contact accuracy drifts (companies' decision-makers change every 1-2 years).
4. **Multi-county addresses** — the routine should detect "Maricopa-area cities" (Phoenix, Tempe, Scottsdale, Mesa, Chandler, Glendale, Gilbert, etc.) before calling the Assessor; non-Maricopa addresses skip it. Hardcoded city list in `pipeline/assessor.py`.

### Design decisions baked in

- **Cache is org-name-keyed, not URL-keyed.** Two articles about the same company should share enrichment, even if they reference different properties.
- **Cache stores the full `Lead` JSON** (not just contact fields) so we can preserve provenance and recreate the dataclass on lookup.
- **Cache writes happen at the routine layer, not inside `push.py`.** Push stays a thin Pipedrive wrapper; enrichment-source tracking belongs in the cache.
- **Assessor parser tolerates layout drift.** The scraper extracts data via stable text anchors ("Owner Name:", "Mailing Address:"), not fragile CSS selectors. Layout changes degrade gracefully (returns partial data, doesn't crash).
- **Assessor scraper uses a browser-like UA** (issue [#3](https://github.com/automationinternsdotcom/Master-AetherCleaning/issues/3) findings — the AetherLeadBot UA gets 403'd by many publisher sites; Assessor seems fine but use browser UA preemptively).

---

## File structure

| Path | Change | Responsibility |
|---|---|---|
| [pipeline/db.py](../../../pipeline/db.py) | **Modify** | Add `enriched_orgs` to SCHEMA. Add `get_cached_enrichment()`, `cache_enrichment()`, `_normalize_org_name()` helpers. |
| [tests/test_db_cache.py](../../../tests/test_db_cache.py) | **Create** | Unit tests for normalization + cache helpers (in-memory sqlite). |
| [pipeline/cli/cache_lookup.py](../../../pipeline/cli/cache_lookup.py) | **Create** | `python -m pipeline.cli.cache_lookup <org_name>` → Lead JSON or `null`. |
| [tests/test_cli_cache_lookup.py](../../../tests/test_cli_cache_lookup.py) | **Create** | Tests for the cache CLI. |
| [pipeline/assessor.py](../../../pipeline/assessor.py) | **Create** | `lookup_by_address(address: str, http: httpx.Client) -> dict \| None`. Returns `{"owner": "...", "mailing_address": "...", "apn": "..."}` or None. |
| [tests/test_assessor.py](../../../tests/test_assessor.py) | **Create** | Tests using saved HTML fixtures captured from a real lookup. |
| [pipeline/cli/assessor_lookup.py](../../../pipeline/cli/assessor_lookup.py) | **Create** | `python -m pipeline.cli.assessor_lookup <address>` → JSON or `null`. Detects non-Maricopa addresses and short-circuits. |
| [tests/test_cli_assessor_lookup.py](../../../tests/test_cli_assessor_lookup.py) | **Create** | CLI tests. |
| [skill/aether_daily_routine.md](../../../skill/aether_daily_routine.md) | **Modify** | Step 2d: cache check first → Assessor (if Maricopa address) → Grok (with owner-hint if Assessor returned one) → cache the result. |
| [skill/grok_enricher.md](../../../skill/grok_enricher.md) | **Modify** | Accept optional `owner_entity` input. Include in the Grok prompt as `"(owner entity per Maricopa Assessor: {owner_entity})"` clause. |

---

## Task 1: Org cache layer

**Files:**
- Modify: `pipeline/db.py`
- Create: `tests/test_db_cache.py`

- [ ] **Step 1: Write failing tests**

`tests/test_db_cache.py`:

```python
"""Tests for the enriched_orgs cache in pipeline/db.py."""

from __future__ import annotations

import json
import sqlite3
import unittest

from pipeline import db
from pipeline.enrich import Lead


def _mem_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(db.SCHEMA)
    return conn


def _sample_lead() -> Lead:
    return Lead(
        name="Michael Wilson", title="COO, Mark-Taylor Inc",
        email="michael.wilson@mark-taylor.com", phone=None,
        linkedin_url="https://linkedin.com/in/michael-wilson",
        seniority="c_suite", apollo_id="grok",
    )


class TestNormalizeOrgName(unittest.TestCase):
    def test_strips_llc_suffix(self):
        self.assertEqual(db._normalize_org_name("Mark-Taylor Residential LLC"),
                         "marktaylor residential")

    def test_strips_inc_suffix(self):
        self.assertEqual(db._normalize_org_name("Acme, Inc."), "acme")

    def test_collapses_whitespace(self):
        self.assertEqual(db._normalize_org_name("  Mark-Taylor   Residential  "),
                         "marktaylor residential")

    def test_strips_punctuation(self):
        self.assertEqual(db._normalize_org_name("M&T Group, L.L.C."), "mt group")

    def test_idempotent(self):
        once = db._normalize_org_name("Mark-Taylor Residential LLC")
        twice = db._normalize_org_name(once)
        self.assertEqual(once, twice)

    def test_handles_corp_ltd_lp_llp(self):
        for suf in ("Corp", "Corporation", "Ltd", "LP", "LLP"):
            self.assertEqual(db._normalize_org_name(f"Acme {suf}"), "acme")


class TestCacheRoundTrip(unittest.TestCase):
    def test_miss_returns_none(self):
        conn = _mem_conn()
        self.assertIsNone(db.get_cached_enrichment(conn, "Mark-Taylor"))

    def test_hit_returns_lead(self):
        conn = _mem_conn()
        lead = _sample_lead()
        db.cache_enrichment(conn, "Mark-Taylor Residential", lead, source="grok")
        conn.commit()
        cached = db.get_cached_enrichment(conn, "Mark-Taylor Residential")
        self.assertIsNotNone(cached)
        self.assertEqual(cached.name, "Michael Wilson")
        self.assertEqual(cached.apollo_id, "grok")

    def test_hit_via_normalized_name_variation(self):
        """Cached as 'Mark-Taylor Residential LLC'; queried as 'mark taylor residential'."""
        conn = _mem_conn()
        db.cache_enrichment(conn, "Mark-Taylor Residential LLC", _sample_lead(), source="grok")
        conn.commit()
        cached = db.get_cached_enrichment(conn, "mark taylor residential")
        self.assertIsNotNone(cached)

    def test_overwrite_replaces(self):
        """Second cache for same normalized name overwrites — fresher data wins."""
        conn = _mem_conn()
        lead_v1 = _sample_lead()
        db.cache_enrichment(conn, "Mark-Taylor", lead_v1, source="grok")
        lead_v2 = Lead(name="Different Person", title="x", email=None, phone=None,
                       linkedin_url=None, seniority="", apollo_id="grok")
        db.cache_enrichment(conn, "Mark-Taylor", lead_v2, source="grok")
        conn.commit()
        cached = db.get_cached_enrichment(conn, "Mark-Taylor")
        self.assertEqual(cached.name, "Different Person")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run, verify fail**

```bash
uv run python -m unittest tests.test_db_cache -v
```

Expected: AttributeError on `db._normalize_org_name`, `db.get_cached_enrichment`, `db.cache_enrichment`.

- [ ] **Step 3: Implement in `pipeline/db.py`**

Append to the SCHEMA constant (inside the triple-quoted block):

```sql
CREATE TABLE IF NOT EXISTS enriched_orgs (
  name_normalized TEXT PRIMARY KEY,
  raw_name        TEXT NOT NULL,
  lead_json       TEXT NOT NULL,
  source          TEXT NOT NULL,
  enriched_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_enriched_orgs_enriched_at ON enriched_orgs(enriched_at);
```

Add helpers (at the end of the module):

```python
import json as _json
import re as _re

from pipeline.enrich import Lead as _Lead

_BUSINESS_SUFFIXES = (
    "llc", "l.l.c.", "l l c",
    "inc", "inc.", "incorporated",
    "corp", "corp.", "corporation",
    "ltd", "ltd.", "limited",
    "lp", "l.p.",
    "llp", "l.l.p.",
    "company", "co", "co.",
)
_SUFFIX_RE = _re.compile(
    r"\b(" + "|".join(_re.escape(s) for s in _BUSINESS_SUFFIXES) + r")\b",
    _re.IGNORECASE,
)
_NON_ALNUM = _re.compile(r"[^a-z0-9 ]+")
_MULTI_SPACE = _re.compile(r"\s+")


def _normalize_org_name(name: str) -> str:
    """Lowercase, drop common business suffixes, collapse punctuation/whitespace."""
    s = name.lower()
    s = _SUFFIX_RE.sub(" ", s)
    s = _NON_ALNUM.sub(" ", s)
    s = _MULTI_SPACE.sub(" ", s)
    return s.strip()


def get_cached_enrichment(conn: sqlite3.Connection, org_name: str) -> "_Lead | None":
    """Return a cached Lead for this org, or None if uncached.

    Lookup key is the normalized form, so 'Mark-Taylor Residential LLC' and
    'mark taylor residential' resolve to the same row.
    """
    row = conn.execute(
        "SELECT lead_json FROM enriched_orgs WHERE name_normalized = ?",
        (_normalize_org_name(org_name),),
    ).fetchone()
    if row is None:
        return None
    return _Lead(**_json.loads(row["lead_json"]))


def cache_enrichment(conn: sqlite3.Connection, org_name: str,
                     lead: "_Lead", source: str) -> None:
    """Upsert this enrichment into the cache. Newer writes overwrite older ones."""
    import dataclasses as _dc
    conn.execute(
        "INSERT OR REPLACE INTO enriched_orgs "
        "(name_normalized, raw_name, lead_json, source, enriched_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            _normalize_org_name(org_name), org_name,
            _json.dumps(_dc.asdict(lead)), source, utc_now_iso(),
        ),
    )
```

- [ ] **Step 4: Run, verify pass**

```bash
uv run python -m unittest discover tests -v
```

Expected: 59 + ~14 new = ~73 tests OK.

- [ ] **Step 5: Commit**

```bash
git add pipeline/db.py tests/test_db_cache.py
git commit -m "Add enriched_orgs cache + name normalization in pipeline/db.py"
```

---

## Task 2: Cache lookup CLI

**Files:**
- Create: `pipeline/cli/cache_lookup.py`
- Create: `tests/test_cli_cache_lookup.py`

- [ ] **Step 1: Write failing tests**

`tests/test_cli_cache_lookup.py`:

```python
"""Tests for pipeline.cli.cache_lookup — org-cache hit/miss as Lead JSON."""

from __future__ import annotations

import io
import json
import sqlite3
import unittest
from unittest.mock import patch

from pipeline import db
from pipeline.cli import cache_lookup
from pipeline.enrich import Lead


def _mem_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(db.SCHEMA)
    return conn


class TestCacheLookupCli(unittest.TestCase):
    def test_prints_null_on_miss(self):
        conn = _mem_conn()
        with patch("pipeline.cli.cache_lookup.db.connect", return_value=conn), \
             patch("sys.argv", ["prog", "Unknown Co"]), \
             patch("sys.stdout", new_callable=io.StringIO) as stdout:
            rc = cache_lookup.main()
        self.assertEqual(rc, 0)
        self.assertEqual(stdout.getvalue().strip(), "null")

    def test_prints_lead_json_on_hit(self):
        conn = _mem_conn()
        lead = Lead(name="Jane", title="COO", email="j@x.com", phone=None,
                    linkedin_url=None, seniority="c_suite", apollo_id="grok")
        db.cache_enrichment(conn, "Acme LLC", lead, source="grok")
        conn.commit()
        with patch("pipeline.cli.cache_lookup.db.connect", return_value=conn), \
             patch("sys.argv", ["prog", "acme"]), \
             patch("sys.stdout", new_callable=io.StringIO) as stdout:
            rc = cache_lookup.main()
        self.assertEqual(rc, 0)
        data = json.loads(stdout.getvalue())
        self.assertEqual(data["name"], "Jane")

    def test_exits_2_on_missing_arg(self):
        with patch("sys.argv", ["prog"]), patch("sys.stderr"):
            rc = cache_lookup.main()
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Implement `pipeline/cli/cache_lookup.py`**

```python
"""`python -m pipeline.cli.cache_lookup <org_name>` — print cached Lead JSON or null."""
from __future__ import annotations

import dataclasses
import json
import sys

from pipeline import db


def main() -> int:
    if len(sys.argv) != 2:
        sys.stderr.write("usage: python -m pipeline.cli.cache_lookup <org_name>\n")
        return 2
    org_name = sys.argv[1]
    conn = db.connect()
    try:
        lead = db.get_cached_enrichment(conn, org_name)
    finally:
        conn.close()
    if lead is None:
        sys.stdout.write("null\n")
    else:
        json.dump(dataclasses.asdict(lead), sys.stdout)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3: Run all tests, commit**

```bash
uv run python -m unittest discover tests -v
git add pipeline/cli/cache_lookup.py tests/test_cli_cache_lookup.py
git commit -m "Add pipeline.cli.cache_lookup CLI shim for the routine"
```

---

## Task 3: Maricopa Assessor scraper

**Files:**
- Create: `pipeline/assessor.py`
- Create: `tests/test_assessor.py`
- Create: `tests/fixtures/assessor_results.html` (capture from a real lookup during dev)

The scraper hits `https://mcassessor.maricopa.gov/mcs/?q=<address>` (verified 2026-05-22, no captcha, HTML results). Returns the **top result's owner + mailing address + APN** as a dict, or None if no results.

- [ ] **Step 1: Capture a real fixture**

```bash
mkdir -p tests/fixtures
curl -s -A "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15" \
  "https://mcassessor.maricopa.gov/mcs/?q=410+N+Scottsdale+Rd" \
  > tests/fixtures/assessor_results.html
ls -la tests/fixtures/assessor_results.html  # confirm 10kb+ HTML, not a 403/captcha
```

- [ ] **Step 2: Inspect the fixture to find stable anchors**

Open the HTML and locate the columns of the results table: typically APN, Owner Name, Property Address, Mailing Address. Write the parser against text anchors (the column header strings) not CSS classes that may change.

- [ ] **Step 3: Write failing tests**

`tests/test_assessor.py`:

```python
"""Tests for pipeline/assessor.py — Maricopa Assessor lookup via httpx."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock

from pipeline import assessor


_FIXTURE = (Path(__file__).parent / "fixtures" / "assessor_results.html").read_text(encoding="utf-8")


def _mock_http(body: str, status: int = 200) -> MagicMock:
    m = MagicMock()
    m.get.return_value.status_code = status
    m.get.return_value.text = body
    return m


class TestLookupByAddress(unittest.TestCase):
    def test_returns_top_owner_on_match(self):
        http = _mock_http(_FIXTURE)
        result = assessor.lookup_by_address("410 N Scottsdale Rd", http)
        self.assertIsNotNone(result)
        # At minimum, an owner string and an APN
        self.assertIn("owner", result)
        self.assertTrue(result["owner"])
        self.assertIn("apn", result)

    def test_returns_none_on_empty_results(self):
        empty = "<html><body><h1>No Results Found</h1></body></html>"
        http = _mock_http(empty)
        self.assertIsNone(assessor.lookup_by_address("nowhere st", http))

    def test_returns_none_on_http_error(self):
        http = _mock_http("error", status=500)
        self.assertIsNone(assessor.lookup_by_address("any", http))

    def test_skips_non_maricopa_cities(self):
        """Hardcoded Maricopa city list short-circuits non-Maricopa addresses."""
        http = _mock_http(_FIXTURE)
        self.assertIsNone(assessor.lookup_by_address("123 Main St, Tucson AZ", http))
        # http.get should not have been called
        http.get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 4: Implement `pipeline/assessor.py`**

```python
"""Maricopa County Assessor parcel lookup by address.

Scrapes the public mcassessor.maricopa.gov/mcs/?q=... results page.
Captcha-free as of 2026-05-22 — verified live in the spike.

Returns the top result's {owner, mailing_address, apn} or None.

For non-Maricopa cities (Tucson, Flagstaff, etc.) the function short-circuits
without making an HTTP request — Maricopa Assessor only covers Maricopa County.
"""
from __future__ import annotations

import re
from typing import Any

import httpx

BASE_URL = "https://mcassessor.maricopa.gov/mcs/"

# Cities that are at least partially inside Maricopa County. Conservative list —
# better to query and find nothing than to skip a valid address.
_MARICOPA_CITIES = frozenset({
    "phoenix", "tempe", "scottsdale", "mesa", "chandler", "glendale",
    "gilbert", "peoria", "surprise", "avondale", "goodyear", "buckeye",
    "queen creek", "fountain hills", "cave creek", "el mirage", "carefree",
    "litchfield park", "tolleson", "wickenburg", "youngtown", "guadalupe",
    "paradise valley", "apache junction",  # AJ straddles Maricopa/Pinal
})


def _in_maricopa(address: str) -> bool:
    a = address.lower()
    return any(city in a for city in _MARICOPA_CITIES)


# Parser anchors — adjusted to match the fixture's actual structure.
# The results table has columns: APN, Owner Name, Property Address, Mailing Address.
# We extract the first data row.
_APN_RE = re.compile(r"\b(\d{3}-\d{2}-\d{3}[A-Z]?)\b")  # APN format: 123-45-678 or with letter suffix


def lookup_by_address(address: str, http: httpx.Client) -> dict[str, Any] | None:
    """Lookup the top property record matching this address.

    Returns dict with keys 'owner', 'mailing_address', 'apn', or None on miss.
    """
    if not _in_maricopa(address):
        return None

    try:
        resp = http.get(BASE_URL, params={"q": address})
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    return _parse_top_result(resp.text)


def _parse_top_result(html: str) -> dict[str, Any] | None:
    """Extract the first row of the results table.

    Uses text-based anchors (column headers) rather than CSS classes so
    layout drift degrades gracefully.
    """
    if "No Results Found" in html or "No results" in html:
        return None

    # The implementation here depends on the actual fixture structure.
    # During Task 3 Step 2 (inspect fixture), refine these regexes.
    # Generic skeleton: find table rows, take first one, pull APN/owner/mailing.

    apn_m = _APN_RE.search(html)
    if not apn_m:
        return None

    # TODO when fixture is captured: extract owner and mailing address
    # using stable text anchors. Placeholder structure:
    return {
        "apn": apn_m.group(1),
        "owner": _extract_owner(html),
        "mailing_address": _extract_mailing_address(html),
    }


def _extract_owner(html: str) -> str | None:
    """Pull the top result's owner name. Implement based on fixture."""
    # Implementation TBD — see Task 3 Step 2 fixture inspection.
    return None


def _extract_mailing_address(html: str) -> str | None:
    """Pull the top result's mailing address. Implement based on fixture."""
    return None
```

(The two `_extract_*` helpers are stubs — they MUST be implemented against the real fixture in Step 2. If the parser returns `owner: None` for all queries, the chain still works — it just means we don't have an owner-hint for Grok.)

- [ ] **Step 5: Run, verify pass + commit**

```bash
uv run python -m unittest discover tests -v
git add pipeline/assessor.py tests/test_assessor.py tests/fixtures/
git commit -m "Add Maricopa Assessor lookup module (httpx scrape, captcha-free)"
```

---

## Task 4: Assessor lookup CLI

**Files:**
- Create: `pipeline/cli/assessor_lookup.py`
- Create: `tests/test_cli_assessor_lookup.py`

- [ ] **Step 1: Write failing tests**

```python
"""Tests for pipeline.cli.assessor_lookup — stdout JSON or null."""

from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch

from pipeline.cli import assessor_lookup as cli


class TestAssessorLookupCli(unittest.TestCase):
    def test_prints_json_on_hit(self):
        with patch("pipeline.cli.assessor_lookup.assessor.lookup_by_address",
                   return_value={"owner": "ABC Holdings LLC",
                                 "mailing_address": "100 Main St", "apn": "123-45-678"}), \
             patch("sys.argv", ["prog", "100 Main St, Phoenix AZ"]), \
             patch("sys.stdout", new_callable=io.StringIO) as stdout:
            rc = cli.main()
        self.assertEqual(rc, 0)
        data = json.loads(stdout.getvalue())
        self.assertEqual(data["owner"], "ABC Holdings LLC")

    def test_prints_null_on_miss(self):
        with patch("pipeline.cli.assessor_lookup.assessor.lookup_by_address",
                   return_value=None), \
             patch("sys.argv", ["prog", "nowhere"]), \
             patch("sys.stdout", new_callable=io.StringIO) as stdout:
            cli.main()
        self.assertEqual(stdout.getvalue().strip(), "null")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Implement `pipeline/cli/assessor_lookup.py`**

```python
"""`python -m pipeline.cli.assessor_lookup <address>` — print Assessor JSON or null."""
from __future__ import annotations

import json
import sys

from pipeline import assessor, util


def main() -> int:
    if len(sys.argv) != 2:
        sys.stderr.write("usage: python -m pipeline.cli.assessor_lookup <address>\n")
        return 2
    address = sys.argv[1]
    with util.make_http_client() as http:
        result = assessor.lookup_by_address(address, http)
    if result is None:
        sys.stdout.write("null\n")
    else:
        json.dump(result, sys.stdout)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3: Run all tests, commit**

```bash
uv run python -m unittest discover tests -v
git add pipeline/cli/assessor_lookup.py tests/test_cli_assessor_lookup.py
git commit -m "Add pipeline.cli.assessor_lookup CLI shim"
```

---

## Task 5: Skill + routine updates

**Files:**
- Modify: `skill/aether_daily_routine.md`
- Modify: `skill/grok_enricher.md`

- [ ] **Step 1: Update Step 2d in `skill/aether_daily_routine.md`**

Insert before the existing Apollo / Grok branch:

```markdown
**Cache check (always first):**

```bash
COMPANY=$(echo '<extracted_json>' | jq -r '.company_name')
uv run python -m pipeline.cli.cache_lookup "$COMPANY" > /tmp/lead.json
if [ "$(cat /tmp/lead.json | tr -d '\n[:space:]')" != "null" ]; then
  echo "Cache hit for $COMPANY — skipping external enrichment"
  # Skip to push step
fi
```

**Maricopa Assessor hint (if extracted address is in Maricopa):**

```bash
ADDRESS=$(echo '<extracted_json>' | jq -r '.address // empty')
if [ -n "$ADDRESS" ]; then
  uv run python -m pipeline.cli.assessor_lookup "$ADDRESS" > /tmp/assessor.json
  OWNER_HINT=$(jq -r '.owner // empty' /tmp/assessor.json)
fi
```
```

After the existing Grok-or-Apollo block, add a cache write:

```markdown
**Cache the result:**

```bash
# Only cache successful enrichments (not lead_gap=null)
if [ "$(cat /tmp/lead.json | tr -d '\n[:space:]')" != "null" ]; then
  # Helper: write to enriched_orgs via a small inline Python (no dedicated CLI yet)
  uv run python -c "
from pipeline import db
import json, sys
lead_dict = json.load(open('/tmp/lead.json'))
from pipeline.enrich import Lead
conn = db.connect()
db.cache_enrichment(conn, sys.argv[1], Lead(**lead_dict), source=sys.argv[2])
conn.commit()
conn.close()
" "$COMPANY" "${ENRICH_VIA:-grok}"
fi
```

- [ ] **Step 2: Update `skill/grok_enricher.md`** — accept optional `owner_entity` input

Add to the "Inputs" section:

```markdown
- `owner_entity`: string or null — the Maricopa Assessor's recorded owning entity for the property in the article (often a holding LLC like "MT Phoenix Holdings LLC"). When set, the prompt template adds an owner-hint clause so Grok can correlate the news-name vs. the legal-owner-name.
```

Modify the prompt template:

```
Find decision-makers at {company_name}{city_phrase}{description_phrase}{owner_phrase}. Return 1-3 ...
```

Where `owner_phrase` = ` (the property's recorded owner per Maricopa County records: "{owner_entity}" — this may be a holding LLC distinct from the operating company)` if `owner_entity` is set, else `""`.

- [ ] **Step 3: Commit**

```bash
git add skill/aether_daily_routine.md skill/grok_enricher.md
git commit -m "Wire routine to use org cache + Assessor hint before Grok"
```

---

## Task 6: End-to-end validation + PR update

**Files:** none — validation only.

- [ ] **Step 1: Run full test suite**

```bash
uv run python -m unittest discover tests -v
```

Expected: ~80 tests total, all pass.

- [ ] **Step 2: Live Assessor test**

```bash
uv run python -m pipeline.cli.assessor_lookup "410 N Scottsdale Rd, Scottsdale AZ"
```

Expected: JSON with owner + mailing_address + apn.

- [ ] **Step 3: Cache round-trip test**

```bash
# Pre-cache a known company
uv run python -c "
from pipeline import db
from pipeline.enrich import Lead
conn = db.connect()
db.cache_enrichment(conn, 'Mark-Taylor Residential',
    Lead(name='Test Person', title='COO', email='t@x.com', phone=None,
         linkedin_url=None, seniority='c_suite', apollo_id='grok'),
    source='grok')
conn.commit()
"

# Hit it
uv run python -m pipeline.cli.cache_lookup "mark-taylor residential LLC"
# Expected: JSON with name=Test Person
```

- [ ] **Step 4: Force-push branch + update PR #4 body**

Add a section to the PR body documenting the cache + Assessor additions, explaining the AZCC drop decision, and bumping the test count.

---

## Summary

5 build tasks (down from 6 after dropping AZCC), 1 validation task. ~200 lines of new Python. Test count goes from 59 → ~80. No new third-party deps.

**Architectural call documented:** AZCC was investigated, found to be captcha-gated on every search. Deferred to Grok (which already indexes AZCC public records). Documented in the plan so this isn't re-litigated.

**What this does NOT do:**
- Tucson (Pima County) Assessor — out of scope; Tucson articles fall back to Grok-only
- Multi-source consensus (Grok + Gemini) — separate followup
- In-article contact extraction — separate followup
- Cache TTL / refresh policy — accept indefinite caching for v1
