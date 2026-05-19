# Pipedrive Integration for Aether Leads Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current 0–100 scoring + 13-column Sheets output with a simpler binary topical filter, dual-write filtered leads to both Google Sheets and Pipedrive's Leads Inbox, and repurpose the Feed History tab as a filter-tuning audit log.

**Architecture:** Drop scoring entirely. The skill applies a natural-language topical filter to each fetched article, then for items that pass: writes a row to the Sheets `Leads` tab (6 columns: Article hyperlink, Date Posted, Deal Size, Lead 1, Lead 2, Lead 3) AND pushes a Pipedrive Lead via a new stdlib Python module `push_pipedrive.py`. Every article (passed or rejected) is logged to the `Feed History` audit tab with `Passed Filter?`, `Reason`, and `Pushed to Pipedrive?` columns. Lead 1/2/3 are filled only when the article names a specific decision-maker with high confidence; blank otherwise (no fabrication).

**Tech Stack:** Python 3 stdlib (`urllib`, `json`), Pipedrive REST API v1, `gog` CLI for Sheets writes, Claude skill orchestration in markdown. No new third-party dependencies.

---

## Open Items (user must resolve before execution)

1. **Pipedrive company domain** — needed to build API URL `https://{domain}.pipedrive.com/api/v1`. Set as `PIPEDRIVE_DOMAIN` env var.
2. **Filter location** — this plan keeps the topical filter inside `skill/SKILL.md`'s `## Filter` section (matches existing skill pattern; one file to edit). If you'd rather have a separate `skill/filter.md` file so non-engineers can edit without touching the orchestration logic, flag before Task 6 — it's a 10-minute change to split.
3. **Scope** — this plan only updates the `aether-leads` skill ([SKILL.md](../../../skill/SKILL.md)). The `phoenix-new-property-leads-daily` skill ([SKILLS_Scheduled.md](../../../skill/SKILLS_Scheduled.md)) is untouched; wire-up there is a follow-on plan.
4. **Custom field hashed keys** — after Task 1 (Pipedrive UI setup), record the four hashed keys Pipedrive assigns to the custom Lead fields. They populate env vars used by `push_pipedrive.py`.

---

## Pipedrive UI Setup (Task 1, manual, one-time)

Done by Jacob/Jordan in the Pipedrive web UI before any code runs. Captured as a numbered checklist in Task 1 below.

---

## File Structure

| Path | Change | Responsibility |
|---|---|---|
| [skill/push_pipedrive.py](../../../skill/push_pipedrive.py) | **Create** | Read filtered-leads JSON from stdin → POST/PATCH to Pipedrive Leads Inbox, idempotent via `article_url` custom field. Stdlib-only. |
| [skill/tests/test_push_pipedrive.py](../../../skill/tests/test_push_pipedrive.py) | **Create** | Unit tests with mocked `urlopen` covering: create new lead, update existing lead, dedup match, missing required env vars, HTTP error handling, empty contact handling. |
| [skill/SKILL.md](../../../skill/SKILL.md) | **Modify** | Remove the entire scoring section. Add `## Filter` section (binary include/exclude topical rules). Update the Leads schema to 6 columns. Update Feed History schema to 7 columns (with `Passed Filter?` / `Pushed to Pipedrive?`). Add `## Step 4: Push to Pipedrive` orchestration block. |
| [README.md](../../../README.md) | **Modify** | Update column tables, remove score/priority documentation, add Pipedrive setup section, add the second skill to the install commands. |
| [skill/.env.example](../../../skill/.env.example) | **Create** | Documents required env vars: `PIPEDRIVE_API_TOKEN`, `PIPEDRIVE_DOMAIN`, `PIPEDRIVE_FIELD_ARTICLE_URL`, `PIPEDRIVE_FIELD_DATE_POSTED`, `PIPEDRIVE_FIELD_DEAL_SIZE`, `PIPEDRIVE_FIELD_SOURCE_FEED`. |
| `.gitignore` | **Verify** | Already covers `*.env`; no change needed. |

---

## Task 1: Pipedrive UI Setup (manual)

**Files:** none — done in the Pipedrive web UI.

- [ ] **Step 1: Confirm API token works**

```bash
curl "https://${PIPEDRIVE_DOMAIN}.pipedrive.com/api/v1/users/me?api_token=${PIPEDRIVE_API_TOKEN}"
```

Expected: JSON with `"success": true` and the user's profile.

- [ ] **Step 2: Create custom Lead fields in the UI**

Pipedrive → Settings → Data fields → Lead → "+ Custom field". Create all four:

| Label | Type | Field key after creation |
|---|---|---|
| Article URL | Text | record the hash key |
| Date Posted | Date | record the hash key |
| Deal Size | Text | record the hash key |
| Source Feed | Single option (`az-cre`, `phoenix-dev`, `tucson-cre`) | record the hash key |

- [ ] **Step 3: Capture hashed field keys**

After creation, Pipedrive shows each field's API key as a 40-char hash (e.g. `1a2b3c4d5e6f...`). Copy each one. These populate the `PIPEDRIVE_FIELD_*` env vars.

- [ ] **Step 4: Verify the new fields are queryable**

```bash
curl "https://${PIPEDRIVE_DOMAIN}.pipedrive.com/api/v1/leadFields?api_token=${PIPEDRIVE_API_TOKEN}" | grep -E '"key"|"name"'
```

Expected: All four custom fields appear in the output. Record their keys.

- [ ] **Step 5: Write env vars to local `.env`**

```bash
cat > ~/.aether-pipedrive.env <<'EOF'
export PIPEDRIVE_API_TOKEN="..."
export PIPEDRIVE_DOMAIN="..."
export PIPEDRIVE_FIELD_ARTICLE_URL="..."
export PIPEDRIVE_FIELD_DATE_POSTED="..."
export PIPEDRIVE_FIELD_DEAL_SIZE="..."
export PIPEDRIVE_FIELD_SOURCE_FEED="..."
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

`skill/tests/test_push_pipedrive.py`:

```python
"""Tests for push_pipedrive.py. Mocks urlopen — no network calls."""

from __future__ import annotations

import io
import json
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

# Ensure parent dir is on path so we can import push_pipedrive
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Set required env vars before import
os.environ.setdefault("PIPEDRIVE_API_TOKEN", "test-token")
os.environ.setdefault("PIPEDRIVE_DOMAIN", "test-co")
os.environ.setdefault("PIPEDRIVE_FIELD_ARTICLE_URL", "field_article_url_hash")
os.environ.setdefault("PIPEDRIVE_FIELD_DATE_POSTED", "field_date_posted_hash")
os.environ.setdefault("PIPEDRIVE_FIELD_DEAL_SIZE", "field_deal_size_hash")
os.environ.setdefault("PIPEDRIVE_FIELD_SOURCE_FEED", "field_source_feed_hash")

import push_pipedrive  # noqa: E402


class TestBuildLeadBody(unittest.TestCase):
    def test_includes_article_url_under_hashed_key(self):
        body = push_pipedrive.build_lead_body(
            article_title="Tempe retail tower signs Trader Joe's",
            article_url="https://example.com/article-1",
            date_posted="2026-05-15",
            deal_size="$45M",
            source_feed="phoenix-dev",
            org_id=None,
            person_id=None,
        )
        self.assertEqual(body["title"], "Tempe retail tower signs Trader Joe's")
        self.assertEqual(body["field_article_url_hash"], "https://example.com/article-1")
        self.assertEqual(body["field_date_posted_hash"], "2026-05-15")
        self.assertEqual(body["field_deal_size_hash"], "$45M")
        self.assertEqual(body["field_source_feed_hash"], "phoenix-dev")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test, verify it fails**

```bash
cd /Users/jon/Code/Master-AetherCleaning
python3 -m unittest skill.tests.test_push_pipedrive -v
```

Expected: `ModuleNotFoundError: No module named 'push_pipedrive'`.

- [ ] **Step 3: Create minimal `push_pipedrive.py` to make the test pass**

`skill/push_pipedrive.py`:

```python
#!/usr/bin/env python3
"""Push filtered Aether leads to Pipedrive Leads Inbox.

Reads filtered-leads JSON from stdin, POSTs each as a Pipedrive Lead.
Idempotent: deduplicates by the article_url custom field.
Stdlib only.
"""

from __future__ import annotations

import os

API_TOKEN = os.environ["PIPEDRIVE_API_TOKEN"]
DOMAIN = os.environ["PIPEDRIVE_DOMAIN"]
FIELD_ARTICLE_URL = os.environ["PIPEDRIVE_FIELD_ARTICLE_URL"]
FIELD_DATE_POSTED = os.environ["PIPEDRIVE_FIELD_DATE_POSTED"]
FIELD_DEAL_SIZE = os.environ["PIPEDRIVE_FIELD_DEAL_SIZE"]
FIELD_SOURCE_FEED = os.environ["PIPEDRIVE_FIELD_SOURCE_FEED"]

BASE_URL = f"https://{DOMAIN}.pipedrive.com/api/v1"


def build_lead_body(
    article_title: str,
    article_url: str,
    date_posted: str | None,
    deal_size: str | None,
    source_feed: str | None,
    org_id: int | None,
    person_id: int | None,
) -> dict:
    body = {
        "title": article_title,
        FIELD_ARTICLE_URL: article_url,
        FIELD_DATE_POSTED: date_posted,
        FIELD_DEAL_SIZE: deal_size,
        FIELD_SOURCE_FEED: source_feed,
    }
    if org_id is not None:
        body["organization_id"] = org_id
    if person_id is not None:
        body["person_id"] = person_id
    return body
```

- [ ] **Step 4: Run the test, verify it passes**

```bash
python3 -m unittest skill.tests.test_push_pipedrive -v
```

Expected: 1 test, OK.

- [ ] **Step 5: Commit**

```bash
git add skill/push_pipedrive.py skill/tests/__init__.py skill/tests/test_push_pipedrive.py
git commit -m "Add push_pipedrive.py skeleton with body-builder unit test"
```

---

## Task 3: Add `api_request` helper with mocked urlopen test

**Files:**
- Modify: `skill/push_pipedrive.py`
- Modify: `skill/tests/test_push_pipedrive.py`

- [ ] **Step 1: Write the failing test**

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
            push_pipedrive.api_request("GET", "/leads")

        self.assertIn("api_token=test-token", captured["url"])
        self.assertEqual(captured["method"], "GET")

    def test_post_sends_json_body(self):
        captured = {}

        def fake_urlopen(req):
            captured["body"] = req.data
            captured["content_type"] = req.headers.get("Content-type")
            return self._mock_urlopen({"success": True, "data": {"id": 42}})

        with patch("push_pipedrive.urlopen", fake_urlopen):
            push_pipedrive.api_request("POST", "/leads", {"title": "x"})

        self.assertEqual(json.loads(captured["body"]), {"title": "x"})
        self.assertEqual(captured["content_type"], "application/json")
```

- [ ] **Step 2: Run, verify it fails**

```bash
python3 -m unittest skill.tests.test_push_pipedrive -v
```

Expected: AttributeError on `push_pipedrive.api_request` and `push_pipedrive.urlopen`.

- [ ] **Step 3: Implement `api_request` in `push_pipedrive.py`**

Add imports and function:

```python
import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def api_request(method: str, path: str, body: dict | None = None) -> dict:
    """Call the Pipedrive REST API. Returns parsed JSON response."""
    sep = "&" if "?" in path else "?"
    url = f"{BASE_URL}{path}{sep}api_token={API_TOKEN}"
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = Request(url, data=data, headers=headers, method=method)
    with urlopen(req) as resp:
        return json.loads(resp.read())
```

- [ ] **Step 4: Run, verify all tests pass**

```bash
python3 -m unittest skill.tests.test_push_pipedrive -v
```

Expected: 3 tests, OK.

- [ ] **Step 5: Commit**

```bash
git add skill/push_pipedrive.py skill/tests/test_push_pipedrive.py
git commit -m "Add api_request helper to push_pipedrive"
```

---

## Task 4: Dedup-by-article-url (`find_lead_by_url`)

**Files:**
- Modify: `skill/push_pipedrive.py`
- Modify: `skill/tests/test_push_pipedrive.py`

- [ ] **Step 1: Write the failing tests**

Append to test file:

```python
class TestFindLeadByUrl(unittest.TestCase):
    def test_returns_lead_id_when_search_matches(self):
        def fake_urlopen(req):
            mock = MagicMock()
            mock.read.return_value = json.dumps({
                "success": True,
                "data": {"items": [{"item": {"id": 99}}]},
            }).encode()
            mock.__enter__ = MagicMock(return_value=mock)
            mock.__exit__ = MagicMock(return_value=False)
            return mock

        with patch("push_pipedrive.urlopen", fake_urlopen):
            result = push_pipedrive.find_lead_by_url("https://example.com/x")
        self.assertEqual(result, 99)

    def test_returns_none_when_no_match(self):
        def fake_urlopen(req):
            mock = MagicMock()
            mock.read.return_value = json.dumps({
                "success": True,
                "data": {"items": []},
            }).encode()
            mock.__enter__ = MagicMock(return_value=mock)
            mock.__exit__ = MagicMock(return_value=False)
            return mock

        with patch("push_pipedrive.urlopen", fake_urlopen):
            result = push_pipedrive.find_lead_by_url("https://example.com/x")
        self.assertIsNone(result)
```

- [ ] **Step 2: Run, verify fail**

Expected: AttributeError on `find_lead_by_url`.

- [ ] **Step 3: Implement**

```python
from urllib.parse import urlencode


def find_lead_by_url(article_url: str) -> int | None:
    """Search Leads by the Article URL custom field. Returns Lead id or None."""
    query = urlencode({"term": article_url, "exact_match": "true"})
    resp = api_request("GET", f"/leads/search?{query}")
    items = resp.get("data", {}).get("items", [])
    return items[0]["item"]["id"] if items else None
```

- [ ] **Step 4: Run, verify pass**

Expected: 5 tests, OK.

- [ ] **Step 5: Commit**

```bash
git commit -am "Add find_lead_by_url for article-URL dedup"
```

---

## Task 5: `find_or_create_org` and `find_or_create_person`

**Files:**
- Modify: `skill/push_pipedrive.py`
- Modify: `skill/tests/test_push_pipedrive.py`

- [ ] **Step 1: Write failing tests**

Append:

```python
class TestFindOrCreateOrg(unittest.TestCase):
    def test_returns_existing_id_when_found(self):
        calls = []

        def fake_urlopen(req):
            calls.append((req.get_method(), req.full_url))
            mock = MagicMock()
            mock.read.return_value = json.dumps({
                "data": {"items": [{"item": {"id": 7, "name": "Acme"}}]},
            }).encode()
            mock.__enter__ = MagicMock(return_value=mock)
            mock.__exit__ = MagicMock(return_value=False)
            return mock

        with patch("push_pipedrive.urlopen", fake_urlopen):
            org_id = push_pipedrive.find_or_create_org("Acme")
        self.assertEqual(org_id, 7)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "GET")

    def test_creates_when_not_found(self):
        responses = [
            {"data": {"items": []}},
            {"data": {"id": 42}},
        ]

        def fake_urlopen(req):
            mock = MagicMock()
            mock.read.return_value = json.dumps(responses.pop(0)).encode()
            mock.__enter__ = MagicMock(return_value=mock)
            mock.__exit__ = MagicMock(return_value=False)
            return mock

        with patch("push_pipedrive.urlopen", fake_urlopen):
            org_id = push_pipedrive.find_or_create_org("New Corp")
        self.assertEqual(org_id, 42)
```

(Add an analogous `TestFindOrCreatePerson` block — same shape, but verifies the search uses `fields=name`, matches on the requested `org_id`, and creates with `{"name": ..., "org_id": ...}` when no match.)

- [ ] **Step 2: Run, verify fail**

- [ ] **Step 3: Implement**

```python
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

- [ ] **Step 5: Commit**

```bash
git commit -am "Add find_or_create_org/person helpers"
```

---

## Task 6: `push_lead` end-to-end (create vs update)

**Files:**
- Modify: `skill/push_pipedrive.py`
- Modify: `skill/tests/test_push_pipedrive.py`

- [ ] **Step 1: Write failing tests**

Two cases: (a) no existing match → POST `/leads`; (b) existing match → PATCH `/leads/{id}`. In both, organization and any non-empty contact names are resolved first. Blank contact names are skipped (no fabrication).

- [ ] **Step 2: Run, verify fail**

- [ ] **Step 3: Implement**

```python
def push_lead(
    article_title: str,
    article_url: str,
    date_posted: str | None,
    deal_size: str | None,
    source_feed: str | None,
    lead_names: list[str],
    organization: str | None,
) -> dict:
    """Create or update one Pipedrive Lead. Idempotent on article_url."""
    existing_id = find_lead_by_url(article_url)
    org_id = find_or_create_org(organization) if organization else None
    person_id = None
    if org_id is not None:
        non_empty = [n for n in lead_names if n and n.strip()]
        if non_empty:
            person_id = find_or_create_person(non_empty[0], org_id)
    body = build_lead_body(
        article_title=article_title,
        article_url=article_url,
        date_posted=date_posted,
        deal_size=deal_size,
        source_feed=source_feed,
        org_id=org_id,
        person_id=person_id,
    )
    if existing_id is not None:
        return api_request("PATCH", f"/leads/{existing_id}", body)
    return api_request("POST", "/leads", body)
```

Note: Pipedrive Leads accept only a single `person_id`. Additional named decision-makers (Lead 2, Lead 3) are still created as Persons attached to the Org so they're searchable later — Task 7 adds a Lead Note that lists all of them inline.

- [ ] **Step 4: Run, verify pass**

- [ ] **Step 5: Commit**

```bash
git commit -am "Add push_lead with create/update branching"
```

---

## Task 7: Attach a Note listing all named contacts

**Files:**
- Modify: `skill/push_pipedrive.py`
- Modify: `skill/tests/test_push_pipedrive.py`

Pipedrive Leads only hold one primary Person. To preserve the Lead 1/2/3 list visibly, attach a Note to the Lead with the full named-contacts list.

- [ ] **Step 1: Write failing test**

Verifies that after `push_lead`, when 2+ contact names are provided, a POST to `/notes` is made with `lead_id` and a `content` body that contains every non-empty name.

- [ ] **Step 2: Run, verify fail**

- [ ] **Step 3: Implement: extend `push_lead` to call a new `add_lead_note(lead_id, names)` after lead creation when ≥1 named contact exists.**

```python
def add_lead_note(lead_id: str, names: list[str]) -> None:
    non_empty = [n for n in names if n and n.strip()]
    if not non_empty:
        return
    content = "Named contacts from article:\n" + "\n".join(f"- {n}" for n in non_empty)
    api_request("POST", "/notes", {"lead_id": lead_id, "content": content})
```

Wire into `push_lead` after the POST/PATCH; use the returned `data.id` for new leads or `existing_id` for updates.

- [ ] **Step 4: Run, verify pass**

- [ ] **Step 5: Commit**

```bash
git commit -am "Attach Pipedrive Note listing all named contacts"
```

---

## Task 8: CLI entrypoint reading stdin JSON

**Files:**
- Modify: `skill/push_pipedrive.py`
- Modify: `skill/tests/test_push_pipedrive.py`

Input contract (JSON written by Claude during the skill run):

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

- [ ] **Step 1: Write failing test** — feed stdin a 2-lead payload (one with contacts, one with all blanks); assert `push_lead` called twice with correct args; assert returned summary `{"pushed": 2, "errors": []}`.

- [ ] **Step 2: Run, verify fail**

- [ ] **Step 3: Implement**

```python
import sys


def main() -> int:
    payload = json.load(sys.stdin)
    leads = payload.get("leads", [])
    summary = {"pushed": 0, "errors": []}
    for lead in leads:
        try:
            push_lead(
                article_title=lead["article_title"],
                article_url=lead["article_link"],
                date_posted=lead.get("date_posted"),
                deal_size=lead.get("deal_size"),
                source_feed=lead.get("source"),
                lead_names=[lead.get("lead_1", ""), lead.get("lead_2", ""), lead.get("lead_3", "")],
                organization=lead.get("organization"),
            )
            summary["pushed"] += 1
        except HTTPError as e:
            summary["errors"].append({"url": lead.get("article_link"), "error": str(e)})
    json.dump(summary, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run, verify pass**

- [ ] **Step 5: Commit**

```bash
git commit -am "Add CLI entrypoint reading filtered-leads JSON from stdin"
```

---

## Task 9: Rewrite `skill/SKILL.md` — drop scoring, add Filter section, new schemas

**Files:**
- Modify: `skill/SKILL.md`

This is a single coordinated rewrite. The sections that disappear: "What counts as a hit (HIGH/MEDIUM/LOW)" scoring tiers, all `score` / `priority` field mentions in the data contract, the score-based sort/keep-top-25 instruction.

The sections that change or appear:

- [ ] **Step 1: Replace the "## Step 2: Score and Enrich" section with "## Step 2: Filter and Extract"**

New content covers:
- The binary filter (no scores). Same topical categories as before, reframed as include/exclude:
  - **Include if** the article describes any of: new tenant occupancy / lease signing, renovation or construction completion, new business opening, property management transition, major expansion or buildout, new apartment/condo lease-up, HOA stand-up, developer land acquisition, industrial/warehouse deal, or any commercial property transaction.
  - **Exclude if** the article is: macro market commentary, mortgage/rate news, residential-only coverage, an out-of-state story, rankings/awards without property activity, or an editorial.
- Geographic scope: Arizona only, Goodyear → Apache Junction plus Tucson. Out-of-state articles are excluded.
- For each *passing* article, extract:
  - `article_title`, `article_link`, `date_posted`, `deal_size` (article-stated value or `"N/A"`), `source` (the feed name from the input JSON), `organization` (the property owner / developer / operator named in the article — null if not clearly identifiable).
  - `lead_1`, `lead_2`, `lead_3`: **only** names actually appearing in the article text AND who are ≥90% probably decision-makers (owner, principal, GM, facilities director, COO, developer project lead, property manager named with a company). **Do not fabricate.** Leave blank if not present.
  - `filter_reason`: one short sentence — used for the Feed History audit, e.g. `"New retail tenants leasing"` or `"Out of state — Las Vegas"`.
- For each *rejected* article, record the same `filter_reason` so it can be audited in Feed History.

- [ ] **Step 2: Replace the Leads-tab section with the new 6-column schema**

New header row:

```bash
GOG_KEYRING_PASSWORD=aether gog sheets update 1DM5qOV3mfPcVbgx_Fj3gVEKS_PPYtAsinIu9Zg5Oxy4 "Leads!A1" --values-json '[["Article","Date Posted","Deal Size","Lead 1","Lead 2","Lead 3"]]' --input USER_ENTERED --no-input -a norgordjacob@gmail.com
```

Clear `A2:Z1000` each run (preserves header). Write filtered leads to `A2`, column order: Article hyperlink | Date Posted | Deal Size | Lead 1 | Lead 2 | Lead 3.

- [ ] **Step 3: Replace the Feed History header to add filter-audit columns**

```bash
GOG_KEYRING_PASSWORD=aether gog sheets update 1DM5qOV3mfPcVbgx_Fj3gVEKS_PPYtAsinIu9Zg5Oxy4 "'Feed History'!A1" --values-json '[["Run Date","Article","Date Posted","Source Feed","Passed Filter?","Reason","Pushed to Pipedrive?"]]' --input USER_ENTERED --no-input -a norgordjacob@gmail.com
```

Append every article (kept or rejected). `Passed Filter?` = Yes/No, `Pushed to Pipedrive?` = Yes/No/N/A (N/A when filter rejected).

Existing Feed History rows from prior runs have the old schema (`Score`, `Priority`, `Filter Reason`, `Included in Leads`). Document the cutover in the skill: do not migrate old rows; new rows use new columns. Old rows can be archived to a `Feed History (legacy)` tab by Jacob, manually, before first run.

- [ ] **Step 4: Add "## Step 4: Push to Pipedrive"**

```bash
source ~/.aether-pipedrive.env
echo '<filtered_leads_json>' | python3 ~/.claude/skills/aether-leads/push_pipedrive.py > /tmp/pipedrive_push_result.json
```

Read `/tmp/pipedrive_push_result.json` to confirm `pushed` count and any `errors`. The set of URLs that pushed successfully → `Pushed to Pipedrive? = Yes` in the Feed History rows; the rest → `No`.

- [ ] **Step 5: Update "## Output" section**

Report: number of articles fetched, number passed filter, number pushed to Pipedrive, any push errors, the top 3 by date with title and deal size, link to the sheet and to Pipedrive Leads Inbox.

- [ ] **Step 6: Update the trigger description in the frontmatter** to remove "Jacob" if it's wrong — confirm the actual operator before this step. (Currently says "Jacob"; README and gog account both use `norgordjacob@gmail.com`. Likely correct, but worth a glance.)

- [ ] **Step 7: Commit**

```bash
git add skill/SKILL.md
git commit -m "Rewrite SKILL.md: drop scoring, add filter+audit, new schemas, Pipedrive push"
```

---

## Task 10: Update `README.md` and add `.env.example`

**Files:**
- Modify: `README.md`
- Create: `skill/.env.example`

- [ ] **Step 1: Create `skill/.env.example`**

```
# Copy to ~/.aether-pipedrive.env, fill in real values, then chmod 600.
export PIPEDRIVE_API_TOKEN=""
export PIPEDRIVE_DOMAIN=""
export PIPEDRIVE_FIELD_ARTICLE_URL=""
export PIPEDRIVE_FIELD_DATE_POSTED=""
export PIPEDRIVE_FIELD_DEAL_SIZE=""
export PIPEDRIVE_FIELD_SOURCE_FEED=""
```

- [ ] **Step 2: Update `README.md`**

- Replace the Leads-tab column table with the new 6-column schema.
- Replace the Feed History column table with the 7-column audit schema.
- Delete the "Scoring Criteria" section (HIGH / MEDIUM / LOW).
- Add a "Pipedrive Setup" section before "How It Works" covering the four custom fields and the env-var file. Reference `skill/.env.example`.
- Update the install command to copy `push_pipedrive.py` and `.env.example` too.
- Add `skill/SKILLS_Scheduled.md` to the install command (it was missing).

- [ ] **Step 3: Commit**

```bash
git add README.md skill/.env.example
git commit -m "Update README and add .env.example for Pipedrive integration"
```

---

## Task 11: End-to-end dry run

**Files:** none — verification only.

- [ ] **Step 1: Run the unit tests once more**

```bash
python3 -m unittest discover skill/tests -v
```

Expected: all tests OK.

- [ ] **Step 2: Manual dry run against a single fixture article**

```bash
source ~/.aether-pipedrive.env
echo '{"leads":[{"article_title":"DRY RUN - Tempe retail tower signs Trader Joe'\''s","article_link":"https://example.com/dry-run-1","date_posted":"2026-05-15","deal_size":"$45M","source":"phoenix-dev","organization":"Aether Test Dev LLC","lead_1":"","lead_2":"","lead_3":""}]}' \
  | python3 skill/push_pipedrive.py
```

Expected: `{"pushed": 1, "errors": []}`. In the Pipedrive UI, the new Lead appears in the Leads Inbox with all four custom fields populated. The Organization "Aether Test Dev LLC" exists. No Person attached (all contacts blank). No Note attached (no named contacts).

- [ ] **Step 3: Manual dry run with one named contact**

Same payload but with `"lead_1": "Jane Doe, VP Operations at Aether Test Dev"`. Expected: Person created and linked, Note attached listing the one name.

- [ ] **Step 4: Idempotency check**

Run Step 2 again — same JSON. Expected: `{"pushed": 1, "errors": []}` and **no** duplicate Lead in Pipedrive (the existing one was PATCHed).

- [ ] **Step 5: Clean up dry-run artifacts**

Delete the test Lead, Org, and any Persons/Notes from the Pipedrive UI before the first real run.

- [ ] **Step 6: First real run**

In Claude Code, ask Jacob to invoke "run aether leads". Watch:
- Feed History tab populates with every article and `Passed Filter?` column.
- Leads tab populates with only the passing articles in the new 6-column schema.
- Pipedrive Leads Inbox shows the same set with article-URL dedup.
- `/tmp/pipedrive_push_result.json` summary matches the count of `Pushed to Pipedrive? = Yes` rows.

- [ ] **Step 7: Final commit if any tweaks were needed**

```bash
git commit -am "End-to-end verification fixes"
```

---

## Summary

11 tasks, ~6 commits, no new third-party dependencies. The Python side is fully unit-tested (mocked urlopen). The skill-side changes are not unit-testable; the dry run in Task 11 is the verification gate. Estimated effort: 4–6 focused hours for a developer who has the Pipedrive API docs open.
