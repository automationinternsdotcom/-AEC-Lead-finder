# Pipedrive Deals MVP — Aether Article Leads Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Push filtered Aether article-leads into a dedicated "Aether Article Sources" pipeline in Pipedrive **Deals** (not Leads), idempotent by article URL, with one custom field and everything else mapped to Pipedrive built-ins.

**Architecture:** Replaces the prior 0–100 scoring + 13-column Sheets output with a binary topical filter, dual-write filtered leads to both Google Sheets (audit + handoff) and Pipedrive Deals (Jordan's actual workspace). A new stdlib Python module `skill/push_pipedrive.py` reads filtered-leads JSON from stdin and pushes each into the dedicated Deals pipeline. Idempotent via the Article URL custom field — re-runs find the existing deal and skip (don't PATCH, to avoid clobbering Jordan's edits). All other extracted data (deal size → Value, organization, person, named-contacts/date/source → Note) maps to Pipedrive Deal built-ins.

**Tech Stack:** Python 3 stdlib (`urllib`, `json`, `re`), Pipedrive REST API v1 (Deals + Organizations + Persons + Notes endpoints), `gog` CLI for Sheets writes, Claude skill orchestration in markdown. No new third-party dependencies.

**Why this supersedes the 2026-05-18 plan:** the prior plan targeted Pipedrive Leads Inbox with 4 custom fields. Mid-review, the user clarified that Jordan does not use Leads (902 unreviewed items as evidence) and that mapping article data to Deal built-ins makes 3 of the 4 custom fields unnecessary. This plan rebuilds around that pivot.

---

## Open Items (user must resolve before execution)

1. **Pipedrive plan tier** — confirm Aether's Pipedrive plan supports the expected volume (~50 new Deals/day, ~18k/year). Some tiers cap total active deals or per-stage counts. Check before Task 1.
2. **Aether's owner user ID** — Jordan's Pipedrive user ID, captured via `curl /users/me` (if Jordan runs the curl) or `/users` (if someone else does). Goes into `PIPEDRIVE_OWNER_ID` env var. Without it, deals get assigned to whoever owns the API token, which may not be Jordan.
3. **Currency assumption** — hardcoded `"USD"` throughout. Arizona CRE → always USD. If Aether ever pushes non-USD deals, this becomes configurable.
4. **Scope** — this plan only updates the `aether-leads` skill ([SKILL.md](../../../skill/SKILL.md)). The `phoenix-new-property-leads-daily` scheduled skill is untouched; wire-up there is a follow-on plan.

### Design decisions baked in

- **Target = Pipedrive Deals, dedicated pipeline.** Jordan works in Deals, not Leads. The dedicated pipeline ("Aether Article Sources") quarantines article noise from his active deal pipeline. He drags qualified items into his real pipeline. See [memory: project-aether-pipedrive-workflow] for the why.
- **One custom field: Article URL (Deal entity, Text).** Everything else maps to Deal built-ins (`title`, `value`+`currency`, `organization_id`, `person_id`, `user_id`, `pipeline_id`, `stage_id`). Per-entity hashed keys: the Deal-side Article URL hash is **different** from any Lead-side hash with the same label.
- **Re-runs skip, not PATCH.** When `find_deal_by_url` returns an existing deal, the script logs "already exists, skipping" and moves on. PATCH would clobber any manual edits Jordan made (notes, stage moves, value adjustments) — unacceptable. The cost is that genuine article updates (e.g., follow-up coverage with new details) won't auto-flow; Jordan handles those manually if needed.
- **Lead 1/2/3 string format is `"<Name>, <Role> at <Company>"`.** Pipedrive Persons are created with just the name (everything before the first comma); the full string is preserved on the Deal Note.
- **Note attached on create only.** Re-runs hit the skip branch and never POST a Note. No duplicate Notes possible.
- **Article URLs are normalized before dedup and storage.** `utm_*`, `fbclid`, `gclid`, `mc_cid`/`mc_eid`, `_hsenc`/`_hsmi`, `ref`, and the URL fragment are stripped. Two visits with different tracking params dedup against each other.
- **Deal Value comes from `parse_deal_size("$45M") → 45000000` USD.** Unparseable values (`"undisclosed"`, `"N/A"`, `""`) → field omitted from body (Pipedrive shows blank Value).
- **`DRY_RUN=1` env flag.** When set, GETs hit the real API (read-only, safe), POSTs/PATCHes are logged to stderr and return synthetic IDs so downstream chaining works. Use for every dev iteration before flipping to live.

---

## File Structure

| Path | Change | Responsibility |
|---|---|---|
| [skill/push_pipedrive.py](../../../skill/push_pipedrive.py) | **Create** | Read filtered-leads JSON from stdin → POST to `/deals` (skip-if-exists by Article URL). Stdlib-only. `DRY_RUN=1` aware. |
| [skill/tests/__init__.py](../../../skill/tests/__init__.py) | **Create** | Empty (package marker). |
| [skill/tests/test_push_pipedrive.py](../../../skill/tests/test_push_pipedrive.py) | **Create** | Unit tests with mocked `urlopen` covering: body builder, API helper with retries + success:false, URL normalization, deal-search dedup, org/person find-or-create, name parsing, deal-size parsing, push_deal create/skip paths, note formatting, CLI summary, DRY_RUN behavior. |
| [skill/SKILL.md](../../../skill/SKILL.md) | **Modify** | Remove the entire scoring section. Add `## Filter` section (binary include/exclude topical rules). Update the Leads schema to 6 columns. Update Feed History schema to 7 columns with `Passed Filter?`/`Reason`/`Pushed to Pipedrive?`. Add `## Step 4: Push to Pipedrive` orchestration block targeting Deals. |
| [README.md](../../../README.md) | **Modify** | Update column tables, remove score/priority documentation, add Pipedrive Deals setup section, add the scheduled skill to install commands. |
| [skill/.env.example](../../../skill/.env.example) | **Create** | Documents required env vars: `PIPEDRIVE_API_TOKEN`, `PIPEDRIVE_DOMAIN`, `PIPEDRIVE_PIPELINE_ID`, `PIPEDRIVE_STAGE_ID`, `PIPEDRIVE_FIELD_ARTICLE_URL`, `PIPEDRIVE_OWNER_ID`, optional `DRY_RUN`. |
| [docs/superpowers/plans/2026-05-18-pipedrive-integration.md](2026-05-18-pipedrive-integration.md) | **Modify** | Add a "SUPERSEDED" header pointing to this plan. Don't delete — preserves the design history. |
| `.gitignore` | **Verify** | Already covers `*.env`; no change needed. |

---

## Task 1: Pipedrive UI Setup (manual)

**Files:** none — done in the Pipedrive web UI plus a few read-only `curl` calls.

- [ ] **Step 1: Confirm API token works**

```bash
curl "https://${PIPEDRIVE_DOMAIN}.pipedrive.com/api/v1/users/me?api_token=${PIPEDRIVE_API_TOKEN}"
```

Expected: JSON with `"success": true`. Record the `data.id` value — this is the owner user ID for whoever holds the token. If Jordan holds the token, this is `PIPEDRIVE_OWNER_ID`. If not, get Jordan's ID via `/users` and use that instead.

- [ ] **Step 2: Confirm Article URL custom field exists on Deal entity**

In Pipedrive: **Settings → Company → Data fields → Deal**. Look for "Article URL" (Text). If present, skip Step 3. If not, create it:

| Label | Type |
|---|---|
| Article URL | Text |

- [ ] **Step 3: Capture the Article URL Deal-side hashed key**

```bash
curl -s "https://${PIPEDRIVE_DOMAIN}.pipedrive.com/api/v1/dealFields?api_token=${PIPEDRIVE_API_TOKEN}" \
  | python3 -c "import json,sys; [print(f['key'], '->', f['name']) for f in json.load(sys.stdin)['data'] if 'rticle' in f['name'].lower()]"
```

Expected: one line like `abc123def456... -> Article URL`. Record the hash. **This is the Deal-side hash and is different from any Lead-side hash with the same label.**

- [ ] **Step 4: Create the "Aether Article Sources" pipeline**

In Pipedrive: **Settings → Company → Pipelines & stages → + Add pipeline**.

- Pipeline name: `Aether Article Sources`
- Stages, in order:
  1. `New`
  2. `Reviewing`
  3. `Pursuing`
  4. `Discarded`

- [ ] **Step 5: Capture the pipeline ID and "New" stage ID**

```bash
curl -s "https://${PIPEDRIVE_DOMAIN}.pipedrive.com/api/v1/pipelines?api_token=${PIPEDRIVE_API_TOKEN}" \
  | python3 -c "import json,sys; [print(p['id'], p['name']) for p in json.load(sys.stdin)['data']]"
```

Record the ID of the row whose name is `Aether Article Sources`. Then:

```bash
curl -s "https://${PIPEDRIVE_DOMAIN}.pipedrive.com/api/v1/stages?pipeline_id=${PIPELINE_ID}&api_token=${PIPEDRIVE_API_TOKEN}" \
  | python3 -c "import json,sys; [print(s['id'], s['name']) for s in json.load(sys.stdin)['data']]"
```

Record the ID of the `New` stage.

- [ ] **Step 6: Configure the list view (Jordan-friendly columns)**

In Pipedrive: open the **Aether Article Sources** pipeline → switch to **list view** (top-right toggle) → click the gear icon on the column header row. Show these columns, in order:

1. Title
2. Organization
3. Value
4. Article URL (custom)
5. Labels
6. Created
7. Owner

Hide everything else.

- [ ] **Step 7: Write env vars to local file**

```bash
cat > ~/.aether-pipedrive.env <<'EOF'
export PIPEDRIVE_API_TOKEN="..."
export PIPEDRIVE_DOMAIN="..."
export PIPEDRIVE_PIPELINE_ID="..."
export PIPEDRIVE_STAGE_ID="..."
export PIPEDRIVE_FIELD_ARTICLE_URL="..."
export PIPEDRIVE_OWNER_ID="..."
# Uncomment to dry-run (no writes, GETs still hit real API):
# export DRY_RUN="1"
EOF
chmod 600 ~/.aether-pipedrive.env
```

This file stays outside the repo. Source it before running the skill: `source ~/.aether-pipedrive.env`.

---

## Task 2: Scaffold `push_pipedrive.py` with a failing test

**Files:**
- Create: `skill/push_pipedrive.py`
- Create: `skill/tests/__init__.py` (empty)
- Create: `skill/tests/test_push_pipedrive.py`

- [ ] **Step 1: Write the first failing test**

Create `skill/tests/__init__.py` as an empty file.

Create `skill/tests/test_push_pipedrive.py`:

```python
"""Tests for push_pipedrive.py. Mocks urlopen — no network calls."""

from __future__ import annotations

import json
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("PIPEDRIVE_API_TOKEN", "test-token")
os.environ.setdefault("PIPEDRIVE_DOMAIN", "test-co")
os.environ.setdefault("PIPEDRIVE_PIPELINE_ID", "7")
os.environ.setdefault("PIPEDRIVE_STAGE_ID", "11")
os.environ.setdefault("PIPEDRIVE_FIELD_ARTICLE_URL", "field_article_url_hash")
os.environ.setdefault("PIPEDRIVE_OWNER_ID", "99")

import push_pipedrive  # noqa: E402


class TestBuildDealBody(unittest.TestCase):
    def test_includes_required_fields(self):
        body = push_pipedrive.build_deal_body(
            article_title="Tempe retail tower signs Trader Joe's",
            article_url="https://example.com/article-1",
            value=45_000_000,
            org_id=42,
            person_id=88,
        )
        self.assertEqual(body["title"], "Tempe retail tower signs Trader Joe's")
        self.assertEqual(body["pipeline_id"], 7)
        self.assertEqual(body["stage_id"], 11)
        self.assertEqual(body["user_id"], 99)
        self.assertEqual(body["field_article_url_hash"], "https://example.com/article-1")
        self.assertEqual(body["value"], 45_000_000)
        self.assertEqual(body["currency"], "USD")
        self.assertEqual(body["organization_id"], 42)
        self.assertEqual(body["person_id"], 88)

    def test_omits_value_currency_when_value_is_none(self):
        body = push_pipedrive.build_deal_body(
            article_title="X", article_url="https://example.com/x",
            value=None, org_id=None, person_id=None,
        )
        self.assertNotIn("value", body)
        self.assertNotIn("currency", body)

    def test_omits_org_and_person_when_none(self):
        body = push_pipedrive.build_deal_body(
            article_title="X", article_url="https://example.com/x",
            value=1, org_id=None, person_id=None,
        )
        self.assertNotIn("organization_id", body)
        self.assertNotIn("person_id", body)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test, verify it fails**

```bash
python3 -m unittest skill.tests.test_push_pipedrive -v
```

Run from the repo root. Expected: `ModuleNotFoundError: No module named 'push_pipedrive'`.

- [ ] **Step 3: Create minimal `push_pipedrive.py` to make the tests pass**

Create `skill/push_pipedrive.py`:

```python
#!/usr/bin/env python3
"""Push filtered Aether leads to the dedicated Pipedrive Deals pipeline.

Reads filtered-leads JSON from stdin, POSTs each as a Pipedrive Deal in the
"Aether Article Sources" pipeline. Idempotent: dedups by the Article URL
custom field; if a Deal with the same article URL already exists, skips
(does not PATCH — would clobber Jordan's manual edits).

Stdlib only. Honors DRY_RUN=1 (GETs hit real API, POSTs/PATCHes are logged
and return synthetic IDs).
"""

from __future__ import annotations

import os

API_TOKEN = os.environ["PIPEDRIVE_API_TOKEN"]
DOMAIN = os.environ["PIPEDRIVE_DOMAIN"]
PIPELINE_ID = int(os.environ["PIPEDRIVE_PIPELINE_ID"])
STAGE_ID = int(os.environ["PIPEDRIVE_STAGE_ID"])
FIELD_ARTICLE_URL = os.environ["PIPEDRIVE_FIELD_ARTICLE_URL"]
OWNER_ID = int(os.environ["PIPEDRIVE_OWNER_ID"])
DRY_RUN = os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes")

BASE_URL = f"https://{DOMAIN}.pipedrive.com/api/v1"


def build_deal_body(
    article_title: str,
    article_url: str,
    value: int | None,
    org_id: int | None,
    person_id: int | None,
) -> dict:
    body = {
        "title": article_title,
        "pipeline_id": PIPELINE_ID,
        "stage_id": STAGE_ID,
        "user_id": OWNER_ID,
        FIELD_ARTICLE_URL: article_url,
    }
    if value is not None:
        body["value"] = value
        body["currency"] = "USD"
    if org_id is not None:
        body["organization_id"] = org_id
    if person_id is not None:
        body["person_id"] = person_id
    return body
```

- [ ] **Step 4: Run the tests, verify they pass**

```bash
python3 -m unittest skill.tests.test_push_pipedrive -v
```

Expected: 3 tests, OK.

- [ ] **Step 5: Commit**

```bash
git add skill/push_pipedrive.py skill/tests/__init__.py skill/tests/test_push_pipedrive.py
git commit -m "Add push_pipedrive.py skeleton with build_deal_body and unit tests"
```

---

## Task 3: Add `api_request` helper with mocked urlopen test

**Files:**
- Modify: `skill/push_pipedrive.py`
- Modify: `skill/tests/test_push_pipedrive.py`

The helper: builds the URL, attaches the API token, sends JSON, parses the response, raises `RuntimeError` on `success: false` envelopes (Pipedrive returns HTTP 200 with `{"success": false, ...}` for validation failures — silent failure mode otherwise), retries on 429/5xx with exponential backoff, and short-circuits writes when `DRY_RUN=1`.

- [ ] **Step 1: Write the failing tests**

Append to `skill/tests/test_push_pipedrive.py`:

```python
class TestApiRequest(unittest.TestCase):
    def _mock_urlopen(self, response_body: dict):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(response_body).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    def test_get_appends_api_token(self):
        captured = {}

        def fake_urlopen(req):
            captured["url"] = req.full_url
            captured["method"] = req.get_method()
            return self._mock_urlopen({"success": True, "data": {}})

        with patch("push_pipedrive.urlopen", fake_urlopen):
            push_pipedrive.api_request("GET", "/deals")

        self.assertIn("api_token=test-token", captured["url"])
        self.assertEqual(captured["method"], "GET")

    def test_post_sends_json_body(self):
        captured = {}

        def fake_urlopen(req):
            captured["body"] = req.data
            captured["content_type"] = req.headers.get("Content-type")
            return self._mock_urlopen({"success": True, "data": {"id": 42}})

        with patch("push_pipedrive.urlopen", fake_urlopen):
            push_pipedrive.api_request("POST", "/deals", {"title": "x"})

        self.assertEqual(json.loads(captured["body"]), {"title": "x"})
        self.assertEqual(captured["content_type"], "application/json")

    def test_raises_on_success_false(self):
        def fake_urlopen(req):
            return self._mock_urlopen({"success": False, "error": "bad field"})

        with patch("push_pipedrive.urlopen", fake_urlopen):
            with self.assertRaises(RuntimeError) as ctx:
                push_pipedrive.api_request("POST", "/deals", {"title": "x"})
        self.assertIn("bad field", str(ctx.exception))

    def test_dry_run_short_circuits_write_methods(self):
        called = {"n": 0}

        def fake_urlopen(req):
            called["n"] += 1
            return self._mock_urlopen({"success": True, "data": {}})

        with patch("push_pipedrive.urlopen", fake_urlopen), \
             patch("push_pipedrive.DRY_RUN", True):
            result = push_pipedrive.api_request("POST", "/deals", {"title": "x"})

        self.assertEqual(called["n"], 0)  # never hit urlopen
        self.assertTrue(result["success"])
        self.assertIn("id", result["data"])  # synthetic ID

    def test_dry_run_still_executes_gets(self):
        called = {"n": 0}

        def fake_urlopen(req):
            called["n"] += 1
            return self._mock_urlopen({"success": True, "data": {"items": []}})

        with patch("push_pipedrive.urlopen", fake_urlopen), \
             patch("push_pipedrive.DRY_RUN", True):
            push_pipedrive.api_request("GET", "/deals/search?term=x")

        self.assertEqual(called["n"], 1)
```

- [ ] **Step 2: Run, verify they fail**

```bash
python3 -m unittest skill.tests.test_push_pipedrive -v
```

Expected: AttributeError on `push_pipedrive.api_request` and `push_pipedrive.urlopen`.

- [ ] **Step 3: Implement `api_request` in `push_pipedrive.py`**

Add to `skill/push_pipedrive.py` (top imports + function):

```python
import json
import sys
import time
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

RETRY_STATUSES = {429, 500, 502, 503, 504}

_dry_run_id_counter = 1_000_000


def api_request(method: str, path: str, body: dict | None = None,
                attempts: int = 3) -> dict:
    """Call the Pipedrive REST API. Returns parsed JSON response.

    Raises RuntimeError on Pipedrive `success: false` envelopes.
    Retries on 429 and 5xx with exponential backoff.

    When DRY_RUN is set: GETs still hit the real API (read-only), but
    POST/PATCH/PUT/DELETE log to stderr and return a synthetic success
    response with a unique increasing ID so downstream chaining works.
    """
    global _dry_run_id_counter

    if DRY_RUN and method != "GET":
        _dry_run_id_counter += 1
        sys.stderr.write(
            f"[DRY_RUN] {method} {path} body={json.dumps(body) if body else 'null'}\n"
        )
        return {"success": True, "data": {"id": _dry_run_id_counter}}

    sep = "&" if "?" in path else "?"
    url = f"{BASE_URL}{path}{sep}api_token={quote(API_TOKEN, safe='')}"
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = Request(url, data=data, headers=headers, method=method)

    for attempt in range(attempts):
        try:
            with urlopen(req) as resp:
                result = json.loads(resp.read())
            break
        except HTTPError as e:
            if e.code in RETRY_STATUSES and attempt < attempts - 1:
                time.sleep(2 ** attempt)
                continue
            raise

    if result.get("success") is False:
        raise RuntimeError(
            f"Pipedrive {method} {path} failed: {result.get('error') or result}"
        )
    return result
```

- [ ] **Step 4: Run, verify all tests pass**

```bash
python3 -m unittest skill.tests.test_push_pipedrive -v
```

Expected: 8 tests, OK.

- [ ] **Step 5: Commit**

```bash
git add skill/push_pipedrive.py skill/tests/test_push_pipedrive.py
git commit -m "Add api_request helper with DRY_RUN, retry, and success:false check"
```

---

## Task 4: URL normalization + `find_deal_by_url`

**Files:**
- Modify: `skill/push_pipedrive.py`
- Modify: `skill/tests/test_push_pipedrive.py`

Two pieces. `normalize_url` strips marketing/analytics tracking params so two visits to the same article don't create duplicate deals. `find_deal_by_url` queries Pipedrive scoped to the Article URL custom field (otherwise Pipedrive searches default fields like title and silently misses matches).

**Pipedrive Deal IDs are integers** (unlike Lead IDs which are UUIDs). Return types reflect that.

- [ ] **Step 1: Write the failing tests**

Append:

```python
class TestNormalizeUrl(unittest.TestCase):
    def test_strips_tracking_params(self):
        url = "https://example.com/a?utm_source=x&utm_medium=y&id=42"
        self.assertEqual(
            push_pipedrive.normalize_url(url),
            "https://example.com/a?id=42",
        )

    def test_strips_fragment(self):
        self.assertEqual(
            push_pipedrive.normalize_url("https://example.com/a#section"),
            "https://example.com/a",
        )

    def test_strips_fbclid_gclid_hubspot(self):
        url = "https://example.com/a?fbclid=x&gclid=y&_hsenc=z&_hsmi=w&keep=1"
        self.assertEqual(
            push_pipedrive.normalize_url(url),
            "https://example.com/a?keep=1",
        )

    def test_leaves_clean_url_unchanged(self):
        self.assertEqual(
            push_pipedrive.normalize_url("https://example.com/a?id=42"),
            "https://example.com/a?id=42",
        )


class TestFindDealByUrl(unittest.TestCase):
    def _mock_search_response(self, items):
        mock = MagicMock()
        mock.read.return_value = json.dumps({
            "success": True,
            "data": {"items": items},
        }).encode()
        mock.__enter__ = MagicMock(return_value=mock)
        mock.__exit__ = MagicMock(return_value=False)
        return mock

    def test_scopes_search_to_article_url_field(self):
        captured = {}

        def fake_urlopen(req):
            captured["url"] = req.full_url
            return self._mock_search_response(
                [{"item": {"id": 555}}]
            )

        with patch("push_pipedrive.urlopen", fake_urlopen):
            result = push_pipedrive.find_deal_by_url("https://example.com/x")
        self.assertEqual(result, 555)
        self.assertIn("fields=field_article_url_hash", captured["url"])
        self.assertIn("exact_match=true", captured["url"])

    def test_returns_none_when_no_match(self):
        def fake_urlopen(req):
            return self._mock_search_response([])

        with patch("push_pipedrive.urlopen", fake_urlopen):
            result = push_pipedrive.find_deal_by_url("https://example.com/x")
        self.assertIsNone(result)
```

- [ ] **Step 2: Run, verify fail**

Expected: AttributeError on `normalize_url` and `find_deal_by_url`.

- [ ] **Step 3: Implement**

Add to `skill/push_pipedrive.py`:

```python
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

TRACKING_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "mc_cid", "mc_eid", "_hsenc", "_hsmi", "ref",
})


def normalize_url(url: str) -> str:
    """Strip marketing/analytics tracking params and the URL fragment.

    Two visits to the same article with different `utm_*` etc. should
    dedup against each other in Pipedrive.
    """
    parsed = urlparse(url)
    kept = [
        (k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=False)
        if k.lower() not in TRACKING_PARAMS
    ]
    return urlunparse(parsed._replace(query=urlencode(kept), fragment=""))


def find_deal_by_url(article_url: str) -> int | None:
    """Search Deals by the Article URL custom field. Returns Deal id or None.

    `fields=<custom-field-key>` is required — without it, Pipedrive searches
    default fields (title, etc.) and silently misses matches against custom
    fields.
    """
    query = urlencode({
        "term": article_url,
        "exact_match": "true",
        "fields": FIELD_ARTICLE_URL,
    })
    resp = api_request("GET", f"/deals/search?{query}")
    items = resp.get("data", {}).get("items", [])
    return items[0]["item"]["id"] if items else None
```

- [ ] **Step 4: Run, verify pass**

Expected: 14 tests, OK (8 prior + 4 normalize + 2 find).

- [ ] **Step 5: Commit**

```bash
git commit -am "Add normalize_url and find_deal_by_url for article-URL dedup"
```

---

## Task 5: `find_or_create_org`, `find_or_create_person`, `parse_person_name`

**Files:**
- Modify: `skill/push_pipedrive.py`
- Modify: `skill/tests/test_push_pipedrive.py`

The skill emits decision-maker fields as a single string like `"Jane Doe, VP Operations at Acme"` (name, then role/affiliation after a comma). `parse_person_name` extracts just the name portion before the comma so Pipedrive Persons aren't literally named that whole string. The full string (with title) is preserved on the Deal Note attached in Task 8.

- [ ] **Step 1: Write failing tests**

Append:

```python
class TestParsePersonName(unittest.TestCase):
    def test_strips_role_and_company(self):
        self.assertEqual(
            push_pipedrive.parse_person_name("Jane Doe, VP Operations at Acme"),
            "Jane Doe",
        )

    def test_passes_bare_name_through(self):
        self.assertEqual(push_pipedrive.parse_person_name("Jane Doe"), "Jane Doe")

    def test_trims_whitespace(self):
        self.assertEqual(
            push_pipedrive.parse_person_name("  Jane Doe  , VP"), "Jane Doe"
        )


def _mock_json(payload):
    mock = MagicMock()
    mock.read.return_value = json.dumps(payload).encode()
    mock.__enter__ = MagicMock(return_value=mock)
    mock.__exit__ = MagicMock(return_value=False)
    return mock


class TestFindOrCreateOrg(unittest.TestCase):
    def test_returns_existing_id_when_found(self):
        calls = []

        def fake_urlopen(req):
            calls.append((req.get_method(), req.full_url))
            return _mock_json({
                "success": True,
                "data": {"items": [{"item": {"id": 7, "name": "Acme"}}]},
            })

        with patch("push_pipedrive.urlopen", fake_urlopen):
            org_id = push_pipedrive.find_or_create_org("Acme")
        self.assertEqual(org_id, 7)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "GET")

    def test_creates_when_not_found(self):
        responses = [
            {"success": True, "data": {"items": []}},
            {"success": True, "data": {"id": 42}},
        ]

        def fake_urlopen(req):
            return _mock_json(responses.pop(0))

        with patch("push_pipedrive.urlopen", fake_urlopen):
            org_id = push_pipedrive.find_or_create_org("New Corp")
        self.assertEqual(org_id, 42)


class TestFindOrCreatePerson(unittest.TestCase):
    def test_returns_existing_when_org_matches(self):
        def fake_urlopen(req):
            return _mock_json({
                "success": True,
                "data": {"items": [
                    {"item": {"id": 11, "name": "Jane Doe",
                              "organization": {"id": 7}}},
                ]},
            })

        with patch("push_pipedrive.urlopen", fake_urlopen):
            pid = push_pipedrive.find_or_create_person("Jane Doe", 7)
        self.assertEqual(pid, 11)

    def test_creates_when_no_match_in_org(self):
        responses = [
            {"success": True, "data": {"items": [
                {"item": {"id": 11, "name": "Jane Doe",
                          "organization": {"id": 999}}},
            ]}},
            {"success": True, "data": {"id": 22}},
        ]

        def fake_urlopen(req):
            return _mock_json(responses.pop(0))

        with patch("push_pipedrive.urlopen", fake_urlopen):
            pid = push_pipedrive.find_or_create_person("Jane Doe", 7)
        self.assertEqual(pid, 22)
```

- [ ] **Step 2: Run, verify fail**

Expected: AttributeError on the three new functions.

- [ ] **Step 3: Implement**

Add to `skill/push_pipedrive.py`:

```python
def parse_person_name(raw: str) -> str:
    """Extract the name from formats like 'Jane Doe, VP Operations at Acme'.

    The skill writes `<name>, <role> at <company>` — Pipedrive Persons should
    hold only the name. The full string is preserved in the Deal Note.
    """
    return raw.split(",", 1)[0].strip()


def find_or_create_org(name: str) -> int:
    """Find an Organization by exact name; create if missing. Returns id."""
    query = urlencode({"term": name, "exact_match": "true", "fields": "name"})
    resp = api_request("GET", f"/organizations/search?{query}")
    items = resp.get("data", {}).get("items", [])
    if items:
        return items[0]["item"]["id"]
    created = api_request("POST", "/organizations", {"name": name})
    return created["data"]["id"]


def find_or_create_person(name: str, org_id: int) -> int:
    """Find a Person by exact name + matching org; create if missing."""
    query = urlencode({"term": name, "exact_match": "true", "fields": "name"})
    resp = api_request("GET", f"/persons/search?{query}")
    for item in resp.get("data", {}).get("items", []):
        existing_org = item["item"].get("organization") or {}
        if existing_org.get("id") == org_id:
            return item["item"]["id"]
    created = api_request("POST", "/persons", {"name": name, "org_id": org_id})
    return created["data"]["id"]
```

- [ ] **Step 4: Run, verify pass**

Expected: 21 tests, OK (14 prior + 3 name + 2 org + 2 person).

- [ ] **Step 5: Commit**

```bash
git commit -am "Add find_or_create_org/person + parse_person_name helpers"
```

---

## Task 6: `parse_deal_size`

**Files:**
- Modify: `skill/push_pipedrive.py`
- Modify: `skill/tests/test_push_pipedrive.py`

Converts free-text deal sizes from articles (`"$45M"`, `"$1.2 billion"`, `"undisclosed"`) to integer USD values for Pipedrive's `value` field. Returns `None` for unparseable input — caller then omits `value` and `currency` from the body.

- [ ] **Step 1: Write failing tests**

Append:

```python
class TestParseDealSize(unittest.TestCase):
    def test_million_suffix(self):
        self.assertEqual(push_pipedrive.parse_deal_size("$45M"), 45_000_000)

    def test_billion_suffix(self):
        self.assertEqual(push_pipedrive.parse_deal_size("$1.2B"), 1_200_000_000)

    def test_thousand_suffix(self):
        self.assertEqual(push_pipedrive.parse_deal_size("$500K"), 500_000)

    def test_word_million(self):
        self.assertEqual(push_pipedrive.parse_deal_size("$45 million"), 45_000_000)

    def test_word_billion(self):
        self.assertEqual(push_pipedrive.parse_deal_size("$1.2 billion"), 1_200_000_000)

    def test_commas_no_suffix(self):
        self.assertEqual(push_pipedrive.parse_deal_size("$45,000,000"), 45_000_000)

    def test_lowercase_m(self):
        self.assertEqual(push_pipedrive.parse_deal_size("45m"), 45_000_000)

    def test_returns_none_for_undisclosed(self):
        self.assertIsNone(push_pipedrive.parse_deal_size("undisclosed"))

    def test_returns_none_for_na(self):
        self.assertIsNone(push_pipedrive.parse_deal_size("N/A"))

    def test_returns_none_for_empty(self):
        self.assertIsNone(push_pipedrive.parse_deal_size(""))

    def test_returns_none_for_none(self):
        self.assertIsNone(push_pipedrive.parse_deal_size(None))

    def test_returns_none_for_tbd(self):
        self.assertIsNone(push_pipedrive.parse_deal_size("TBD"))
```

- [ ] **Step 2: Run, verify fail**

Expected: AttributeError on `parse_deal_size`.

- [ ] **Step 3: Implement**

Add to `skill/push_pipedrive.py`:

```python
import re

_DEAL_SIZE_RE = re.compile(
    r"^\$?\s*([\d,]+(?:\.\d+)?)\s*([kmb]|thousand|million|billion)?",
    re.IGNORECASE,
)

_DEAL_SIZE_MULTIPLIERS = {
    "": 1,
    "k": 1_000, "thousand": 1_000,
    "m": 1_000_000, "million": 1_000_000,
    "b": 1_000_000_000, "billion": 1_000_000_000,
}


def parse_deal_size(raw: str | None) -> int | None:
    """Parse free-text deal size strings to int USD value.

    Returns None for unparseable inputs (undisclosed, N/A, TBD, "", None) so
    the caller can omit the `value` field from the Pipedrive Deal body.
    """
    if not raw or not raw.strip():
        return None
    m = _DEAL_SIZE_RE.match(raw.strip())
    if not m:
        return None
    number = float(m.group(1).replace(",", ""))
    suffix = (m.group(2) or "").lower()
    return int(number * _DEAL_SIZE_MULTIPLIERS[suffix])
```

- [ ] **Step 4: Run, verify pass**

Expected: 33 tests, OK (21 prior + 12 deal-size).

- [ ] **Step 5: Commit**

```bash
git commit -am "Add parse_deal_size helper for free-text article deal sizes"
```

---

## Task 7: `push_deal` end-to-end (skip-if-exists, not PATCH)

**Files:**
- Modify: `skill/push_pipedrive.py`
- Modify: `skill/tests/test_push_pipedrive.py`

`push_deal` is the boundary that normalizes the URL once, then uses the normalized value for both the dedup lookup and the stored custom field. **On existing match: skip and return a marker dict** — do NOT PATCH, since that would clobber any manual edits Jordan made (stage moves, note additions, value adjustments).

- [ ] **Step 1: Write failing tests**

Append:

```python
class TestPushDeal(unittest.TestCase):
    def test_creates_when_no_match(self):
        # Responses, in order:
        # 1. /deals/search → empty
        # 2. /organizations/search → empty
        # 3. POST /organizations → id=42
        # 4. /persons/search → empty
        # 5. POST /persons → id=88
        # 6. POST /deals → id=555
        # 7. POST /notes → ok
        responses = [
            {"success": True, "data": {"items": []}},
            {"success": True, "data": {"items": []}},
            {"success": True, "data": {"id": 42}},
            {"success": True, "data": {"items": []}},
            {"success": True, "data": {"id": 88}},
            {"success": True, "data": {"id": 555}},
            {"success": True, "data": {"id": 999}},
        ]
        captured_methods = []

        def fake_urlopen(req):
            captured_methods.append((req.get_method(), req.full_url))
            return _mock_json(responses.pop(0))

        with patch("push_pipedrive.urlopen", fake_urlopen):
            result = push_pipedrive.push_deal(
                article_title="Tempe retail tower",
                article_url="https://example.com/a?utm_source=x",
                date_posted="2026-05-15",
                deal_size="$45M",
                source_feed="phoenix-dev",
                lead_names=["Jane Doe, VP Ops at Acme", "", ""],
                organization="Acme",
            )

        self.assertEqual(result["data"]["id"], 555)
        # Verify deal POST happened (6th call)
        self.assertEqual(captured_methods[5][0], "POST")
        self.assertIn("/deals", captured_methods[5][1])

    def test_skips_when_existing_match(self):
        # Response: /deals/search → one match
        def fake_urlopen(req):
            self.assertIn("/deals/search", req.full_url)
            return _mock_json({
                "success": True,
                "data": {"items": [{"item": {"id": 555}}]},
            })

        with patch("push_pipedrive.urlopen", fake_urlopen):
            result = push_pipedrive.push_deal(
                article_title="Already pushed",
                article_url="https://example.com/already",
                date_posted="2026-05-15",
                deal_size="$45M",
                source_feed="phoenix-dev",
                lead_names=["", "", ""],
                organization="Acme",
            )

        self.assertEqual(result["skipped"], True)
        self.assertEqual(result["existing_id"], 555)

    def test_skips_org_lookup_when_organization_none(self):
        responses = [
            {"success": True, "data": {"items": []}},  # /deals/search
            {"success": True, "data": {"id": 555}},     # POST /deals
        ]

        def fake_urlopen(req):
            return _mock_json(responses.pop(0))

        with patch("push_pipedrive.urlopen", fake_urlopen):
            result = push_pipedrive.push_deal(
                article_title="No org",
                article_url="https://example.com/no-org",
                date_posted=None,
                deal_size=None,
                source_feed=None,
                lead_names=["", "", ""],
                organization=None,
            )

        self.assertEqual(result["data"]["id"], 555)
```

- [ ] **Step 2: Run, verify fail**

- [ ] **Step 3: Implement**

Add to `skill/push_pipedrive.py`:

```python
def push_deal(
    article_title: str,
    article_url: str,
    date_posted: str | None,
    deal_size: str | None,
    source_feed: str | None,
    lead_names: list[str],
    organization: str | None,
) -> dict:
    """Create one Pipedrive Deal, idempotent on normalized article_url.

    If a Deal with the same article URL already exists, returns
    `{"skipped": True, "existing_id": <id>}` — does NOT PATCH (would clobber
    Jordan's manual edits like stage moves or note additions).

    On create: also attaches a Note with date posted, source feed, and named
    contacts.
    """
    article_url = normalize_url(article_url)
    existing_id = find_deal_by_url(article_url)
    if existing_id is not None:
        return {"skipped": True, "existing_id": existing_id}

    org_id = find_or_create_org(organization) if organization else None

    person_id = None
    if org_id is not None:
        non_empty = [n for n in lead_names if n and n.strip()]
        if non_empty:
            person_id = find_or_create_person(
                parse_person_name(non_empty[0]), org_id
            )

    body = build_deal_body(
        article_title=article_title,
        article_url=article_url,
        value=parse_deal_size(deal_size),
        org_id=org_id,
        person_id=person_id,
    )

    created = api_request("POST", "/deals", body)
    add_deal_note(
        created["data"]["id"], lead_names, date_posted, source_feed
    )
    return created
```

Note on the API: Pipedrive Deals accept only a single `person_id`. Additional named decision-makers (Lead 2, Lead 3) are still preserved verbatim on the Note attached by `add_deal_note` (Task 8).

- [ ] **Step 4: Run, verify pass** (will fail on `add_deal_note` — implemented in next task)

For now, stub `add_deal_note` at the top of the module to make Task 7 tests pass:

```python
def add_deal_note(deal_id: int, names: list[str],
                  date_posted: str | None, source_feed: str | None) -> None:
    pass  # implemented in Task 8
```

Expected: 36 tests, OK (33 prior + 3 push_deal).

- [ ] **Step 5: Commit**

```bash
git commit -am "Add push_deal with skip-if-exists dedup (no PATCH on re-run)"
```

---

## Task 8: `add_deal_note` with date, source, contacts

**Files:**
- Modify: `skill/push_pipedrive.py`
- Modify: `skill/tests/test_push_pipedrive.py`

The Note preserves data that doesn't fit Deal built-ins or that we deliberately keep out of structured fields (date posted, source feed, full contact strings with role/title). Called from `push_deal` only on create — never on the skip path — so re-runs never duplicate Notes.

- [ ] **Step 1: Write failing tests**

Append (replacing the stub from Task 7's verification step — actual implementation tests):

```python
class TestAddDealNote(unittest.TestCase):
    def test_includes_date_source_and_contacts(self):
        captured = {}

        def fake_urlopen(req):
            captured["url"] = req.full_url
            captured["body"] = json.loads(req.data)
            return _mock_json({"success": True, "data": {"id": 999}})

        with patch("push_pipedrive.urlopen", fake_urlopen):
            push_pipedrive.add_deal_note(
                deal_id=555,
                names=["Jane Doe, VP Ops at Acme", "John Smith, CFO at Acme", ""],
                date_posted="2026-05-15",
                source_feed="phoenix-dev",
            )

        self.assertIn("/notes", captured["url"])
        self.assertEqual(captured["body"]["deal_id"], 555)
        content = captured["body"]["content"]
        self.assertIn("2026-05-15", content)
        self.assertIn("phoenix-dev", content)
        self.assertIn("Jane Doe, VP Ops at Acme", content)
        self.assertIn("John Smith, CFO at Acme", content)

    def test_skips_post_when_nothing_to_record(self):
        called = {"n": 0}

        def fake_urlopen(req):
            called["n"] += 1
            return _mock_json({"success": True, "data": {}})

        with patch("push_pipedrive.urlopen", fake_urlopen):
            push_pipedrive.add_deal_note(
                deal_id=555, names=["", "", ""],
                date_posted=None, source_feed=None,
            )

        self.assertEqual(called["n"], 0)

    def test_includes_only_present_fields(self):
        captured = {}

        def fake_urlopen(req):
            captured["body"] = json.loads(req.data)
            return _mock_json({"success": True, "data": {"id": 999}})

        with patch("push_pipedrive.urlopen", fake_urlopen):
            push_pipedrive.add_deal_note(
                deal_id=555, names=["", "", ""],
                date_posted="2026-05-15", source_feed=None,
            )

        content = captured["body"]["content"]
        self.assertIn("2026-05-15", content)
        self.assertNotIn("Source feed:", content)
        self.assertNotIn("Named contacts", content)
```

- [ ] **Step 2: Run, verify fail**

- [ ] **Step 3: Implement** — replace the Task 7 stub with the real function in `skill/push_pipedrive.py`:

```python
def add_deal_note(deal_id: int, names: list[str],
                  date_posted: str | None, source_feed: str | None) -> None:
    """Attach a Note to a newly-created Deal.

    Records data that doesn't fit Pipedrive Deal built-ins:
    date the article was posted, source feed, and the full named-contacts
    list (with roles/titles preserved verbatim).

    Called only from the create branch of push_deal — never on skip — so
    re-running on the same article never produces duplicate Notes.
    """
    non_empty_names = [n for n in names if n and n.strip()]
    lines = []
    if date_posted:
        lines.append(f"Date posted: {date_posted}")
    if source_feed:
        lines.append(f"Source feed: {source_feed}")
    if non_empty_names:
        if lines:
            lines.append("")
        lines.append("Named contacts from article:")
        lines.extend(f"- {n}" for n in non_empty_names)
    if not lines:
        return
    api_request("POST", "/notes", {
        "deal_id": deal_id,
        "content": "\n".join(lines),
    })
```

- [ ] **Step 4: Run, verify pass**

Expected: 39 tests, OK (36 prior + 3 add_deal_note).

- [ ] **Step 5: Commit**

```bash
git commit -am "Implement add_deal_note with date, source feed, named contacts"
```

---

## Task 9: CLI entrypoint reading stdin JSON

**Files:**
- Modify: `skill/push_pipedrive.py`
- Modify: `skill/tests/test_push_pipedrive.py`

Input contract (JSON written by the skill orchestration). `lead_1/2/3` use the format **`"<Name>, <Role> at <Company>"`** — `parse_person_name` strips everything after the first comma for the Pipedrive Person; `add_deal_note` preserves the full string verbatim. Leave a field as `""` if the article doesn't name a high-confidence decision-maker (no fabrication).

```json
{
  "leads": [
    {
      "article_title": "...",
      "article_link": "https://...",
      "date_posted": "2026-05-15",
      "deal_size": "$45M",
      "source": "phoenix-dev",
      "organization": "Acme Development LLC",
      "lead_1": "Jane Doe, VP Operations at Acme",
      "lead_2": "",
      "lead_3": ""
    }
  ]
}
```

Output (written to stdout):

```json
{
  "pushed": 1,
  "skipped": 0,
  "errors": []
}
```

- [ ] **Step 1: Write failing test**

Append:

```python
class TestMainCli(unittest.TestCase):
    def test_pushes_two_leads_and_summarizes(self):
        # Both leads are new (deals/search returns []), and we mock minimal
        # downstream calls. Use a generic responder that returns "no match"
        # for searches and synthetic IDs for creates.
        def fake_urlopen(req):
            url = req.full_url
            if "/search" in url:
                return _mock_json({"success": True, "data": {"items": []}})
            return _mock_json({"success": True, "data": {"id": 1}})

        payload = json.dumps({"leads": [
            {
                "article_title": "Lead A",
                "article_link": "https://example.com/a",
                "date_posted": "2026-05-15",
                "deal_size": "$10M",
                "source": "phoenix-dev",
                "organization": "Acme",
                "lead_1": "Jane Doe, VP at Acme",
                "lead_2": "", "lead_3": "",
            },
            {
                "article_title": "Lead B",
                "article_link": "https://example.com/b",
                "date_posted": "2026-05-15",
                "deal_size": "undisclosed",
                "source": "az-cre",
                "organization": None,
                "lead_1": "", "lead_2": "", "lead_3": "",
            },
        ]})

        with patch("push_pipedrive.urlopen", fake_urlopen), \
             patch("sys.stdin", io.StringIO(payload)), \
             patch("sys.stdout", new_callable=io.StringIO) as stdout:
            rc = push_pipedrive.main()

        self.assertEqual(rc, 0)
        summary = json.loads(stdout.getvalue())
        self.assertEqual(summary["pushed"], 2)
        self.assertEqual(summary["skipped"], 0)
        self.assertEqual(summary["errors"], [])

    def test_counts_skipped(self):
        def fake_urlopen(req):
            if "/deals/search" in req.full_url:
                return _mock_json({
                    "success": True,
                    "data": {"items": [{"item": {"id": 42}}]},
                })
            self.fail("Should not call anything after dedup hit")

        payload = json.dumps({"leads": [{
            "article_title": "Already pushed",
            "article_link": "https://example.com/dup",
            "date_posted": "2026-05-15",
            "deal_size": "$5M",
            "source": "phoenix-dev",
            "organization": "Acme",
            "lead_1": "", "lead_2": "", "lead_3": "",
        }]})

        with patch("push_pipedrive.urlopen", fake_urlopen), \
             patch("sys.stdin", io.StringIO(payload)), \
             patch("sys.stdout", new_callable=io.StringIO) as stdout:
            push_pipedrive.main()

        summary = json.loads(stdout.getvalue())
        self.assertEqual(summary["pushed"], 0)
        self.assertEqual(summary["skipped"], 1)

    def test_captures_errors_without_propagating(self):
        def fake_urlopen(req):
            if "/deals/search" in req.full_url:
                return _mock_json({"success": True, "data": {"items": []}})
            return _mock_json({"success": False, "error": "Some Pipedrive error"})

        payload = json.dumps({"leads": [{
            "article_title": "Broken",
            "article_link": "https://example.com/broken",
            "date_posted": "2026-05-15",
            "deal_size": None,
            "source": "phoenix-dev",
            "organization": None,
            "lead_1": "", "lead_2": "", "lead_3": "",
        }]})

        with patch("push_pipedrive.urlopen", fake_urlopen), \
             patch("sys.stdin", io.StringIO(payload)), \
             patch("sys.stdout", new_callable=io.StringIO) as stdout:
            rc = push_pipedrive.main()

        self.assertEqual(rc, 0)  # errors captured, not raised
        summary = json.loads(stdout.getvalue())
        self.assertEqual(summary["pushed"], 0)
        self.assertEqual(len(summary["errors"]), 1)
        self.assertIn("Some Pipedrive error", summary["errors"][0]["error"])
        self.assertEqual(
            summary["errors"][0]["url"], "https://example.com/broken"
        )
```

Note: the `test_pushes_two_leads_and_summarizes` test imports `io` — add `import io` at the top of the test file if it's not already there.

- [ ] **Step 2: Run, verify fail**

- [ ] **Step 3: Implement**

Add to `skill/push_pipedrive.py`:

```python
def main() -> int:
    payload = json.load(sys.stdin)
    leads = payload.get("leads", [])
    summary = {"pushed": 0, "skipped": 0, "errors": []}
    for lead in leads:
        try:
            result = push_deal(
                article_title=lead["article_title"],
                article_url=lead["article_link"],
                date_posted=lead.get("date_posted"),
                deal_size=lead.get("deal_size"),
                source_feed=lead.get("source"),
                lead_names=[
                    lead.get("lead_1", ""),
                    lead.get("lead_2", ""),
                    lead.get("lead_3", ""),
                ],
                organization=lead.get("organization"),
            )
            if result.get("skipped"):
                summary["skipped"] += 1
            else:
                summary["pushed"] += 1
        except (HTTPError, RuntimeError) as e:
            # HTTPError = transport/HTTP-level failure (after retries exhausted).
            # RuntimeError = Pipedrive responded 200 but envelope was success: false.
            summary["errors"].append({
                "url": lead.get("article_link"),
                "error": str(e),
            })
    json.dump(summary, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run, verify pass**

Expected: 42 tests, OK (39 prior + 3 main).

- [ ] **Step 5: Commit**

```bash
git commit -am "Add main() CLI entrypoint with push/skip/error summary"
```

---

## Task 10: Rewrite `skill/SKILL.md` — drop scoring, add Filter, push to Deals

**Files:**
- Modify: `skill/SKILL.md`

A single coordinated rewrite. Sections that disappear: "What counts as a hit (HIGH/MEDIUM/LOW)" tiers, all `score`/`priority` field mentions in the data contract, the score-based sort/keep-top-25 instruction. New sections appear for the filter and the Pipedrive push.

- [ ] **Step 1: Replace the "## Step 2: Score and Enrich" section with "## Step 2: Filter and Extract"**

The new section's content covers:

- **Binary filter, no scores.** Same topical categories as before, reframed as include/exclude:
  - **Include if** the article describes any of: new tenant occupancy / lease signing, renovation or construction completion, new business opening, property management transition, major expansion or buildout, new apartment/condo lease-up, HOA stand-up, developer land acquisition, industrial/warehouse deal, or any commercial property transaction.
  - **Exclude if** the article is: macro market commentary, mortgage/rate news, residential-only coverage, an out-of-state story, rankings/awards without property activity, or an editorial.
- **Geographic scope:** Arizona only, Goodyear → Apache Junction plus Tucson. Out-of-state articles are excluded.
- For each *passing* article, extract:
  - `article_title`, `article_link`, `date_posted`, `deal_size` (article-stated value or `"N/A"`), `source` (the feed name from the input JSON), `organization` (the property owner / developer / operator named in the article — `null` if not clearly identifiable).
  - `lead_1`, `lead_2`, `lead_3`: **only** names actually appearing in the article text AND who are ≥90% probably decision-makers (owner, principal, GM, facilities director, COO, developer project lead, property manager named with a company). Use the **`"<Name>, <Role> at <Company>"`** format. **Do not fabricate.** Leave blank if not present.
  - `filter_reason`: one short sentence — used for the Feed History audit, e.g. `"New retail tenants leasing"` or `"Out of state — Las Vegas"`.
- For each *rejected* article, record the same `filter_reason` so it can be audited in Feed History.

- [ ] **Step 2: Replace the Leads-tab schema section with the new 6-column schema**

New header row:

```bash
GOG_KEYRING_PASSWORD=aether gog sheets update 1DM5qOV3mfPcVbgx_Fj3gVEKS_PPYtAsinIu9Zg5Oxy4 "Leads!A1" --values-json '[["Article","Date Posted","Deal Size","Lead 1","Lead 2","Lead 3"]]' --input USER_ENTERED --no-input -a norgordjacob@gmail.com
```

Clear `A2:Z1000` each run (preserves header). Write filtered leads to `A2`, column order: Article hyperlink | Date Posted | Deal Size | Lead 1 | Lead 2 | Lead 3.

- [ ] **Step 3: Replace the Feed History schema with the audit version**

**Pre-flight schema guard.** Before writing any audit rows, read the current `Feed History!A1:G1` header. If column A is not `Run Date` OR column E is not `Passed Filter?`, the sheet still has the legacy 4-column schema (`Score`, `Priority`, `Filter Reason`, `Included in Leads`). Stop the run with an error asking the operator to either rename the existing tab to `Feed History (legacy)` or clear it — otherwise new-schema rows will be appended below old-schema rows and the audit log becomes unreadable.

```bash
# Pre-flight check (run BEFORE the header update below):
header=$(GOG_KEYRING_PASSWORD=aether gog sheets read 1DM5qOV3mfPcVbgx_Fj3gVEKS_PPYtAsinIu9Zg5Oxy4 "'Feed History'!A1:G1" -a norgordjacob@gmail.com)
case "$header" in
  *"Run Date"*"Passed Filter?"*) echo "Feed History schema OK" ;;
  *) echo "ERROR: Feed History uses legacy schema. Rename tab to 'Feed History (legacy)' or clear it before continuing." >&2; exit 1 ;;
esac
```

Then write the new header:

```bash
GOG_KEYRING_PASSWORD=aether gog sheets update 1DM5qOV3mfPcVbgx_Fj3gVEKS_PPYtAsinIu9Zg5Oxy4 "'Feed History'!A1" --values-json '[["Run Date","Article","Date Posted","Source Feed","Passed Filter?","Reason","Pushed to Pipedrive?"]]' --input USER_ENTERED --no-input -a norgordjacob@gmail.com
```

Append every article (kept or rejected). `Passed Filter?` = `Yes`/`No`, `Pushed to Pipedrive?` = `Yes`/`Skipped (already exists)`/`No (error)`/`N/A` (N/A when filter rejected).

- [ ] **Step 4: Add "## Step 4: Push to Pipedrive Deals"**

```bash
source ~/.aether-pipedrive.env
echo '<filtered_leads_json>' | python3 ~/.claude/skills/aether-leads/push_pipedrive.py > /tmp/pipedrive_push_result.json
```

Read `/tmp/pipedrive_push_result.json`. Map result counts to Feed History column G:
- `summary.pushed` count → `Yes`
- `summary.skipped` count → `Skipped (already exists)`
- URLs in `summary.errors` → `No (error)`

- [ ] **Step 5: Update "## Output" section**

Report: number of articles fetched, number passed filter, number pushed to Pipedrive Deals, any push errors, the top 3 by date with title and deal size, link to the Google Sheet and to the "Aether Article Sources" pipeline view in Pipedrive.

- [ ] **Step 6: Update the frontmatter description**

Confirm the trigger description matches the new behavior (filter + push to Deals, not score + Sheets-only).

- [ ] **Step 7: Commit**

```bash
git add skill/SKILL.md
git commit -m "Rewrite SKILL.md: drop scoring, add filter+audit, push to Pipedrive Deals"
```

---

## Task 11: README, `.env.example`, supersede old plan

**Files:**
- Modify: `README.md`
- Create: `skill/.env.example`
- Modify: `docs/superpowers/plans/2026-05-18-pipedrive-integration.md`

- [ ] **Step 1: Create `skill/.env.example`**

```
# Copy to ~/.aether-pipedrive.env, fill in real values, then chmod 600.
export PIPEDRIVE_API_TOKEN=""
export PIPEDRIVE_DOMAIN=""
export PIPEDRIVE_PIPELINE_ID=""
export PIPEDRIVE_STAGE_ID=""
export PIPEDRIVE_FIELD_ARTICLE_URL=""
export PIPEDRIVE_OWNER_ID=""
# Uncomment to dry-run (GETs hit real API, POSTs/PATCHes are logged only):
# export DRY_RUN="1"
```

- [ ] **Step 2: Update `README.md`**

- Replace the Leads-tab column table with the new 6-column schema (Article, Date Posted, Deal Size, Lead 1, Lead 2, Lead 3).
- Replace the Feed History column table with the 7-column audit schema (Run Date, Article, Date Posted, Source Feed, Passed Filter?, Reason, Pushed to Pipedrive?).
- Delete the "Scoring Criteria" section (HIGH / MEDIUM / LOW).
- Add a "Pipedrive Setup" section before "How It Works" covering: the one custom field (Article URL on Deal), the dedicated pipeline, the env-var file. Reference `skill/.env.example`.
- Update the install command to copy `push_pipedrive.py` and `.env.example`.
- Add `skill/SKILLS_Scheduled.md` to the install command (it was missing from the install list previously).

- [ ] **Step 3: Mark the old plan as superseded**

Add to the very top of `docs/superpowers/plans/2026-05-18-pipedrive-integration.md`:

```markdown
> **⚠️ SUPERSEDED** by [2026-05-21-pipedrive-deals-mvp.md](2026-05-21-pipedrive-deals-mvp.md).
>
> Mid-review the user clarified that Jordan does not use Pipedrive Leads (works in Deals). This plan's Leads-Inbox target was abandoned in favor of a dedicated Deals pipeline with 1 custom field. Kept for design-history visibility.
```

(Do not delete the old plan — preserves the design evolution.)

- [ ] **Step 4: Commit**

```bash
git add README.md skill/.env.example docs/superpowers/plans/2026-05-18-pipedrive-integration.md
git commit -m "Update README, add .env.example, supersede 2026-05-18 plan"
```

---

## Task 12: End-to-end dry run + first real run

**Files:** none — verification only.

- [ ] **Step 1: Run the unit tests once more**

```bash
python3 -m unittest discover skill/tests -v
```

Expected: 42 tests, all OK.

- [ ] **Step 2: Dry-run a single fixture article**

```bash
source ~/.aether-pipedrive.env
export DRY_RUN=1
echo '{"leads":[{"article_title":"DRY RUN - Tempe retail tower","article_link":"https://example.com/dry-run-1","date_posted":"2026-05-15","deal_size":"$45M","source":"phoenix-dev","organization":"Aether Test Dev LLC","lead_1":"","lead_2":"","lead_3":""}]}' \
  | python3 skill/push_pipedrive.py
```

Expected on stderr: `[DRY_RUN] POST /organizations ...` and `[DRY_RUN] POST /deals ...` log lines.
Expected on stdout: `{"pushed": 1, "skipped": 0, "errors": []}`.
Verify in Pipedrive UI: **no new Deal or Organization actually created.**

- [ ] **Step 3: Real run, single fixture, no named contacts**

```bash
unset DRY_RUN
echo '{"leads":[{"article_title":"REAL TEST - Tempe retail tower","article_link":"https://example.com/real-test-1","date_posted":"2026-05-15","deal_size":"$45M","source":"phoenix-dev","organization":"Aether Test Dev LLC","lead_1":"","lead_2":"","lead_3":""}]}' \
  | python3 skill/push_pipedrive.py
```

Expected stdout: `{"pushed": 1, "skipped": 0, "errors": []}`.
In Pipedrive UI, the **Aether Article Sources → New** stage shows:
- A Deal titled "REAL TEST - Tempe retail tower"
- Value: $45,000,000 USD
- Organization linked: "Aether Test Dev LLC" (newly created)
- Article URL custom field populated
- A Note attached with `Date posted: 2026-05-15` and `Source feed: phoenix-dev`
- No Person linked (all contacts blank)

- [ ] **Step 4: Real run with one named contact**

Same payload but with `"lead_1": "Jane Doe, VP Operations at Aether Test Dev"`, and a different URL like `https://example.com/real-test-2`.

Expected: Person "Jane Doe" created, linked to Org "Aether Test Dev LLC", attached to the Deal. The Note now includes the `Named contacts from article:` section listing "Jane Doe, VP Operations at Aether Test Dev" (full role-preserving string).

- [ ] **Step 5: Idempotency check**

Re-run Step 3 with the **same** payload (same `article_link`).
Expected: `{"pushed": 0, "skipped": 1, "errors": []}` and **no** duplicate Deal in Pipedrive.

- [ ] **Step 6: Clean up dry-run artifacts**

In Pipedrive UI, delete the test Deals and the "Aether Test Dev LLC" Organization (and any test Persons) before the first real article-driven run.

- [ ] **Step 7: First real article run**

In Claude Code, invoke "run aether leads" (or whatever trigger phrase wires to `skill/SKILL.md`). Verify:

- Feed History tab populates with every article and the `Passed Filter?` column.
- Leads tab populates with only passing articles in the new 6-column schema.
- Pipedrive **Aether Article Sources → New** stage shows the same set of passing articles.
- `/tmp/pipedrive_push_result.json` summary `pushed + skipped + errors` count matches passing-article count.
- `Pushed to Pipedrive?` column in Feed History correctly reflects `Yes`/`Skipped (already exists)`/`No (error)`.

- [ ] **Step 8: Final commit if any tweaks were needed**

```bash
git commit -am "End-to-end verification fixes"
```

---

## Summary

12 tasks, ~7 commits, no new third-party dependencies. Stdlib-only Python (`urllib`, `json`, `re`). The Python side is fully unit-tested (42 tests, all mocked `urlopen`). The skill-side changes are not unit-testable; the dry-run + real-run sequence in Task 12 is the verification gate. Estimated effort: 3–5 focused hours for a developer who has the Pipedrive API docs open.

**Code surface area:** ~250 lines of Python in `push_pipedrive.py` (down from the original plan's projected ~400+), plus ~350 lines of tests.

**What this MVP does NOT do** (deliberate — out of scope for v1):
- Does not update an existing Deal on re-run (skip-only, to protect Jordan's edits).
- Does not extract Property Address or Total Available Space from articles (could be added later if articles consistently mention them).
- Does not auto-assign per source feed (all deals go to a single `PIPEDRIVE_OWNER_ID`).
- Does not handle non-USD currencies.
- Does not wire up the `phoenix-new-property-leads-daily` scheduled skill — that's a follow-on.
