# Grok Web Explorer Enricher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace (or supplement) Apollo enrichment with a SuperGrok-via-Claude-in-Chrome enricher driven by subagents. Each article that passes qualification gets a Grok query for decision-makers, parsed into the existing `enrich.Lead` schema, fed into `pipeline.cli.push` unchanged.

**Architecture:** Two parts.

1. **Deterministic Python parser** (`pipeline/grok_parse.py` + `pipeline/cli/grok_parse.py`): takes Grok's markdown response as stdin, returns a `Lead` JSON or `null`. Pure logic, unit-tested with stdlib `unittest`.

2. **Skill-driven orchestration** (`skill/aether_daily_routine.md` update + new `skill/grok_enricher.md`): the Claude Code routine dispatches a subagent per article for enrichment. Subagent receives the company name + Chrome tab ID, drives the existing logged-in SuperGrok session via Claude-in-Chrome MCP tools, captures the response, and pipes it through `pipeline.cli.grok_parse` to produce a clean Lead JSON.

**Tech Stack:** Python 3.12 stdlib `re` for parsing (no new deps). Claude-in-Chrome MCP for browser automation. Existing pipeline modules unchanged (`push.py`, `enrich.Lead`, `pipeline.cli.push`).

**Why this supersedes the Apollo path:** Apollo costs ~$99/mo+ and gives variable contact quality. SuperGrok (already paid for the user's account) returns higher-quality decision-maker matches with verification (sourced from Rocketreach, company sites). Validated in spike: Mark-Taylor Residential query in Fast mode returned COO + Director of Facilities with LinkedIn URLs + email format guesses in ~6 seconds.

---

## Spike findings (validated 2026-05-22)

- ✅ SuperGrok login persists across browser sessions (no auth flow needed daily)
- ✅ Fast mode returns structured contact info in 6-10s (Heavy mode took 5+ min — unusable for batch)
- ✅ Response shape is predictable and regex-parseable:
  ```
  1. <Name>
  Current Title: <Title>, <Company>. (verification info)
  LinkedIn: <URL>
  Professional Email: Likely <email>... (format notes). [optional source tag]
  
  2. <Name>
  ...
  ```
- ✅ Trailing meta (sources count, suggested follow-ups) is skippable
- ✅ Multiple decision-makers per query — we take the highest-priority one

## Open items (resolve before execution)

1. **Subagent vs. inline orchestration trade-off.** Spawning a subagent per article adds ~10-20s of dispatch overhead. For 50 articles/day, that's ~12 extra minutes on top of the ~6s/Grok-query. Alternative: batch dispatch (one subagent per 5 articles). Plan defaults to per-article for context isolation; revisit if total run time exceeds 30 min.
2. **Standardized prompt template.** Locked in below; iterate if quality drops on edge-case companies.
3. **Browser session expiry.** SuperGrok session cookies could lapse. The routine should pre-check (visit grok.com, verify "Intern One" is shown) and surface a clear error if logged out.
4. **Apollo coexistence.** Both paths supported. If `APOLLO_API_KEY` is set, Apollo is preferred (cheaper API call, no browser dep). If unset, fall back to Grok-via-Chrome (which IS the primary path for the user's setup).

### Design decisions baked in

- **Parser is pure Python** — deterministic, unit-tested, doesn't depend on LLM reliability. The subagent does I/O (browser) and serialization; parsing is offline.
- **`enrich.Lead` schema unchanged** — `apollo_id` field becomes a provenance marker (`"grok"` or `"apollo:<id>"`). No new dataclass fields.
- **Subagent has scoped responsibility:** open Grok, send prompt, capture response, pipe through `pipeline.cli.grok_parse`, return JSON. No parsing logic in the prompt.
- **One Grok conversation per article.** Click "New Chat" before each query to keep responses clean (no contamination from prior context).
- **Fast mode mandatory.** Subagent must verify the mode indicator before sending each query.

---

## File structure

| Path | Change | Responsibility |
|---|---|---|
| [pipeline/grok_parse.py](../../../pipeline/grok_parse.py) | **Create** | Pure parser. `parse_grok_response(text: str) -> Lead \| None`. Plus `_derive_seniority(title) -> str`. |
| [pipeline/cli/grok_parse.py](../../../pipeline/cli/grok_parse.py) | **Create** | CLI shim. Stdin = Grok response text; stdout = Lead JSON or `null`. |
| [tests/test_grok_parse.py](../../../tests/test_grok_parse.py) | **Create** | Unit tests with fixed-shape inputs (including the spike's Mark-Taylor response verbatim). |
| [tests/test_cli_grok_parse.py](../../../tests/test_cli_grok_parse.py) | **Create** | CLI tool tests. |
| [skill/grok_enricher.md](../../../skill/grok_enricher.md) | **Create** | Subagent-dispatch template. Self-contained instructions for the enricher subagent. |
| [skill/aether_daily_routine.md](../../../skill/aether_daily_routine.md) | **Modify** | Replace Step 2d (Apollo CLI) with subagent-dispatch flow. Apollo CLI stays as fallback when `APOLLO_API_KEY` is set. |
| [README.md](../../../README.md) | **Modify** | Document the Grok-via-Chrome enrichment path + SuperGrok account requirement. |

---

## Task 1: Build the deterministic parser

**Files:**
- Create: `pipeline/grok_parse.py`
- Create: `tests/test_grok_parse.py`

- [ ] **Step 1: Write failing tests** using the spike's verbatim response

`tests/test_grok_parse.py`:

```python
"""Tests for pipeline/grok_parse.py — using fixed-shape Grok Fast-mode responses."""

from __future__ import annotations

import unittest

from pipeline import grok_parse


# Verbatim from the 2026-05-22 spike against Mark-Taylor Residential
SPIKE_RESPONSE = """1. Michael Wilson
Current Title: Chief Operating Officer (COO), Mark-Taylor, Inc. (verified current via company site and recent announcements).
LinkedIn: https://www.linkedin.com/in/michael-wilson-2a982625a
Professional Email: Likely michael.wilson@mark-taylor.com (common format: first.last@mark-taylor.com).⁠Rocketreach

2. Chris Madison (Christopher Madison)
Current Title: Director of Facilities & Renovation Management / Associate Director of Facilities, Mark-Taylor, Inc. (verified current via company leadership page).
LinkedIn: https://www.linkedin.com/in/christophermadison235
Professional Email: Likely chris.madison@mark-taylor.com or christopher.madison@mark-taylor.com (common format).⁠Rocketreach

These individuals align with high-priority roles (COO and Facilities leadership) with likely authority over janitorial/cleaning service contracts for multifamily properties.

95 sources"""


class TestParseGrokResponse(unittest.TestCase):
    def test_returns_first_entry_as_lead(self):
        lead = grok_parse.parse_grok_response(SPIKE_RESPONSE)
        self.assertIsNotNone(lead)
        self.assertEqual(lead.name, "Michael Wilson")
        self.assertIn("Chief Operating Officer", lead.title)
        self.assertEqual(lead.email, "michael.wilson@mark-taylor.com")
        self.assertEqual(lead.linkedin_url,
                         "https://www.linkedin.com/in/michael-wilson-2a982625a")
        self.assertEqual(lead.seniority, "c_suite")
        self.assertEqual(lead.apollo_id, "grok")
        self.assertIsNone(lead.phone)

    def test_handles_name_with_parenthetical(self):
        """Chris Madison (Christopher Madison) — keep just the first name form."""
        # Skip ahead so the parser sees entry 2 as the first
        from_entry_2 = SPIKE_RESPONSE.split("\n\n2.", 1)[1]
        lead = grok_parse.parse_grok_response("2." + from_entry_2)
        self.assertIsNotNone(lead)
        self.assertEqual(lead.name, "Chris Madison")  # parenthetical stripped

    def test_returns_none_on_empty_response(self):
        self.assertIsNone(grok_parse.parse_grok_response(""))

    def test_returns_none_when_no_entries(self):
        text = "Sorry, I couldn't find decision-makers at that company. 0 sources"
        self.assertIsNone(grok_parse.parse_grok_response(text))

    def test_email_field_handles_likely_prefix(self):
        text = "1. Jane Doe\nCurrent Title: VP Ops, Acme.\nProfessional Email: Likely jane@acme.com"
        lead = grok_parse.parse_grok_response(text)
        self.assertEqual(lead.email, "jane@acme.com")

    def test_email_field_handles_no_email(self):
        text = "1. Jane Doe\nCurrent Title: VP Ops, Acme.\nLinkedIn: https://linkedin.com/in/jane"
        lead = grok_parse.parse_grok_response(text)
        self.assertIsNotNone(lead)
        self.assertIsNone(lead.email)
        self.assertEqual(lead.linkedin_url, "https://linkedin.com/in/jane")

    def test_picks_first_email_when_two_offered(self):
        """Grok often gives 'X or Y' — take X."""
        text = "1. Chris Madison\nCurrent Title: Director of Facilities, Mark-Taylor.\nProfessional Email: Likely chris.madison@mark-taylor.com or christopher.madison@mark-taylor.com"
        lead = grok_parse.parse_grok_response(text)
        self.assertEqual(lead.email, "chris.madison@mark-taylor.com")


class TestDeriveSeniority(unittest.TestCase):
    def test_owner(self):
        self.assertEqual(grok_parse._derive_seniority("Owner, Acme"), "owner")
        self.assertEqual(grok_parse._derive_seniority("Founder & CEO"), "owner")
        self.assertEqual(grok_parse._derive_seniority("Principal"), "owner")

    def test_c_suite(self):
        self.assertEqual(grok_parse._derive_seniority("Chief Operating Officer"), "c_suite")
        self.assertEqual(grok_parse._derive_seniority("COO"), "c_suite")
        self.assertEqual(grok_parse._derive_seniority("CFO"), "c_suite")

    def test_vp(self):
        self.assertEqual(grok_parse._derive_seniority("VP of Facilities"), "vp")
        self.assertEqual(grok_parse._derive_seniority("Vice President, Ops"), "vp")

    def test_director(self):
        self.assertEqual(grok_parse._derive_seniority("Director of Facilities & Renovation"), "director")

    def test_manager(self):
        self.assertEqual(grok_parse._derive_seniority("Property Manager"), "manager")
        self.assertEqual(grok_parse._derive_seniority("Operations Manager"), "manager")

    def test_unknown(self):
        self.assertEqual(grok_parse._derive_seniority("Some Random Title"), "")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run, verify fail**

```bash
uv run python -m unittest tests.test_grok_parse -v
```

Expected: `ModuleNotFoundError: No module named 'pipeline.grok_parse'`.

- [ ] **Step 3: Implement `pipeline/grok_parse.py`**

```python
"""Parse SuperGrok Fast-mode responses into pipeline.enrich.Lead objects.

The Grok response shape is predictable:

  1. <Name> [or "<Name> (<AltName>)"]
  Current Title: <Title>, <Company>. [verification clauses]
  LinkedIn: <URL>
  Professional Email: [Likely] <email> [or <email2>] [format notes] [source tag]

  2. ...

Trailing meta (sources counts, suggested follow-ups) is ignored. We return
the first valid entry as the primary Lead — Grok orders by relevance.
"""
from __future__ import annotations

import re

from pipeline.enrich import Lead

# One entry runs from "N. " through the next "N+1. " or end-of-text.
_ENTRY_HEAD = re.compile(r"^(\d+)\.\s+(.+?)$", re.MULTILINE)
_TITLE_LINE = re.compile(r"Current Title:\s*(.+?)\s*$", re.MULTILINE)
_LINKEDIN = re.compile(r"LinkedIn:\s*(https?://\S+)")
_EMAIL = re.compile(r"Professional Email:[^\n]*?([\w.+-]+@[\w.-]+\.\w+)")
# Strip "(AltName)" or "(Christopher Madison)" parentheticals from names.
_PAREN_TAIL = re.compile(r"\s*\([^)]+\)\s*$")


def parse_grok_response(text: str) -> Lead | None:
    """Return the first qualifying decision-maker, or None if none found."""
    if not text or not text.strip():
        return None

    # Split into entries on "1. ", "2. ", etc. — keep heads.
    matches = list(_ENTRY_HEAD.finditer(text))
    if not matches:
        return None

    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        block = text[start:end]
        lead = _parse_block(block)
        if lead is not None:
            return lead
    return None


def _parse_block(block: str) -> Lead | None:
    head = _ENTRY_HEAD.match(block)
    if not head:
        return None
    raw_name = head.group(2).strip()
    name = _PAREN_TAIL.sub("", raw_name).strip()

    title_m = _TITLE_LINE.search(block)
    if not title_m:
        return None
    # Strip trailing period and verification parentheticals
    title = title_m.group(1).strip().rstrip(".")

    linkedin_m = _LINKEDIN.search(block)
    email_m = _EMAIL.search(block)

    return Lead(
        name=name,
        title=title,
        email=email_m.group(1).strip() if email_m else None,
        phone=None,
        linkedin_url=linkedin_m.group(1).strip() if linkedin_m else None,
        seniority=_derive_seniority(title),
        apollo_id="grok",
    )


_SENIORITY_RULES = (
    # Check order matters: 'owner' before 'manager' (since "Founder" beats nothing,
    # but check carefully). 'c_suite' before 'vp' (CEO not VP).
    (("owner", "founder", "principal"), "owner"),
    (("chief ", "ceo", "coo", "cfo", "cmo", "cto", "chief"), "c_suite"),
    (("vp ", "vice president", "vp,"), "vp"),
    (("director",), "director"),
    (("manager",), "manager"),
)


def _derive_seniority(title: str) -> str:
    t = title.lower()
    for needles, label in _SENIORITY_RULES:
        if any(n in t for n in needles):
            return label
    return ""
```

- [ ] **Step 4: Run, verify pass**

```bash
uv run python -m unittest tests.test_grok_parse -v
```

Expected: ~12 tests OK.

- [ ] **Step 5: Commit**

```bash
git add pipeline/grok_parse.py tests/test_grok_parse.py
git commit -m "Add pipeline/grok_parse — deterministic SuperGrok response parser"
```

---

## Task 2: CLI shim for the parser

**Files:**
- Create: `pipeline/cli/grok_parse.py`
- Create: `tests/test_cli_grok_parse.py`

- [ ] **Step 1: Write failing tests**

`tests/test_cli_grok_parse.py`:

```python
"""Tests for pipeline.cli.grok_parse — stdin (Grok text) → stdout (Lead JSON or null)."""

from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch

from pipeline.cli import grok_parse as cli_grok_parse


SPIKE_RESPONSE = """1. Michael Wilson
Current Title: Chief Operating Officer (COO), Mark-Taylor, Inc.
LinkedIn: https://www.linkedin.com/in/michael-wilson-2a982625a
Professional Email: Likely michael.wilson@mark-taylor.com"""


class TestGrokParseCli(unittest.TestCase):
    def test_prints_lead_json_on_match(self):
        with patch("sys.stdin", io.StringIO(SPIKE_RESPONSE)), \
             patch("sys.stdout", new_callable=io.StringIO) as stdout:
            rc = cli_grok_parse.main()
        self.assertEqual(rc, 0)
        data = json.loads(stdout.getvalue())
        self.assertEqual(data["name"], "Michael Wilson")
        self.assertEqual(data["email"], "michael.wilson@mark-taylor.com")
        self.assertEqual(data["seniority"], "c_suite")
        self.assertEqual(data["apollo_id"], "grok")

    def test_prints_null_when_no_match(self):
        with patch("sys.stdin", io.StringIO("Sorry, no results.")), \
             patch("sys.stdout", new_callable=io.StringIO) as stdout:
            rc = cli_grok_parse.main()
        self.assertEqual(rc, 0)
        self.assertEqual(stdout.getvalue().strip(), "null")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run, verify fail**

- [ ] **Step 3: Implement `pipeline/cli/grok_parse.py`**

```python
"""`python -m pipeline.cli.grok_parse` (stdin Grok text) — print Lead JSON or `null`.

Used by the daily routine's enrichment step: the Grok response captured
via Claude-in-Chrome is piped through this CLI, which produces a Lead
JSON object that can flow straight into `pipeline.cli.push`.
"""
from __future__ import annotations

import dataclasses
import json
import sys

from pipeline import grok_parse


def main() -> int:
    text = sys.stdin.read()
    lead = grok_parse.parse_grok_response(text)
    if lead is None:
        sys.stdout.write("null\n")
    else:
        json.dump(dataclasses.asdict(lead), sys.stdout)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run, verify pass**

```bash
uv run python -m unittest discover tests -v
```

Expected: ~46 tests OK (44 from prior + 2 new).

- [ ] **Step 5: Commit**

```bash
git add pipeline/cli/grok_parse.py tests/test_cli_grok_parse.py
git commit -m "Add pipeline.cli.grok_parse CLI shim for the routine to use"
```

---

## Task 3: Subagent prompt template

**Files:**
- Create: `skill/grok_enricher.md`

This is the prompt the daily routine sends to each enricher subagent. Self-contained — the subagent gets no context from the parent.

- [ ] **Step 1: Create `skill/grok_enricher.md`**

```markdown
---
name: grok-enricher
description: Per-article decision-maker enrichment via SuperGrok Fast mode in Claude-in-Chrome. Receives a company name, returns Lead JSON or null.
---

# Grok Enricher Subagent

You are the contact-enrichment subagent. Your one job: given a company name (and optional city/description), use the parent's open SuperGrok session to find 1-3 decision-makers who would have buying authority for janitorial/cleaning service contracts, then return clean JSON.

## Inputs (provided in the dispatch prompt)

- `company_name`: string (e.g. "Mark-Taylor Residential")
- `city`: string or null (e.g. "Phoenix")
- `description`: string or null (e.g. "multifamily property management")
- `tab_id`: integer — the Chrome tab where grok.com is already open and logged in

## Steps

1. **Verify the session.** Take a screenshot of `tab_id`. Confirm:
   - URL contains `grok.com`
   - "Intern One" or the user's name is shown in the sidebar
   - The mode selector shows "Fast" (not Heavy/Expert)

   If logged out OR mode is wrong, STOP. Return `{"error": "session_invalid", "details": "<what you saw>"}`. The parent will handle re-auth.

2. **Start a fresh chat.** Click the "New Chat" button in the sidebar so prior queries don't contaminate the response.

3. **Inject the standardized prompt.** Use `javascript_tool` to set the editor's text via `execCommand('insertText', ...)` — the `type` action collapses spaces in ProseMirror editors.

   Standardized prompt template:

   ```
   Find decision-makers at {company_name}{city_phrase}{description_phrase}. Return 1-3 people who would have buying authority for janitorial/cleaning service contracts. Priority roles: Owner, COO, VP/Director of Facilities, Asset Manager, Operations Manager. For each: full name, current title, LinkedIn URL if findable, professional email if findable. Numbered list, no preamble.
   ```

   Where:
   - `city_phrase` = ` ({city})` if city is set, else `""`
   - `description_phrase` = ` — {description}` if description is set, else `""`

4. **Submit.** Click the submit button (arrow up icon, ref via `find` for "submit button" or coordinate-click).

5. **Wait + capture.** Wait ~10 seconds. Take a screenshot to confirm response rendered. Then call `get_page_text` to capture the full conversation text.

6. **Parse via the CLI tool.** Extract just the assistant response (everything after "Thought for Xs" or after the user's prompt re-display). Pipe through:

   ```bash
   echo '<grok response text>' | uv run python -m pipeline.cli.grok_parse
   ```

   The CLI returns a Lead JSON object or `null`.

7. **Return.** Output the JSON exactly as received from the CLI. Wrap in:

   ```json
   {"company_name": "<input company_name>", "lead": <CLI output>}
   ```

## Failure modes

- **Session invalid** → `{"error": "session_invalid", ...}` (parent re-auths and retries)
- **Grok refused / no results** → `{"company_name": "...", "lead": null}` (parent treats as `lead_gap=True`)
- **Network/UI flake** → retry once. If still failing → `{"error": "ui_flake", ...}`
- **Parser returned null** → `{"company_name": "...", "lead": null}` is the correct output. Don't make up data.

## Quality bar

- Never fabricate contact info. If Grok says "no email findable," return `email: null`.
- The parser already filters parentheticals from names. Don't second-guess it.
- One Grok query per company. No follow-up clarifications.
```

- [ ] **Step 2: Commit**

```bash
git add skill/grok_enricher.md
git commit -m "Add skill/grok_enricher.md — subagent prompt for SuperGrok contact enrichment"
```

---

## Task 4: Wire the routine to dispatch enricher subagents

**Files:**
- Modify: `skill/aether_daily_routine.md`

Replace Step 2d (Apollo CLI call) with a subagent-dispatch branch. Keep Apollo CLI as the fallback when `APOLLO_API_KEY` is set.

- [ ] **Step 1: Edit `skill/aether_daily_routine.md` Step 2d**

Replace the existing 2d block:

```markdown
### 2d. Enrich (optional — only if a domain is present)

```bash
DOMAIN=$(echo '<extracted_json>' | jq -r '.company_domain_guess // empty')
if [ -n "$DOMAIN" ]; then
  uv run python -m pipeline.cli.enrich "$DOMAIN" > /tmp/lead.json
else
  echo 'null' > /tmp/lead.json
fi
```
```

With:

```markdown
### 2d. Enrich the lead

**Decide the enrichment source:**

```bash
if [ -n "$APOLLO_API_KEY" ]; then
  ENRICH_VIA=apollo
else
  ENRICH_VIA=grok
fi
```

#### Apollo path (when `APOLLO_API_KEY` is set)

```bash
DOMAIN=$(echo '<extracted_json>' | jq -r '.company_domain_guess // empty')
if [ -n "$DOMAIN" ]; then
  uv run python -m pipeline.cli.enrich "$DOMAIN" > /tmp/lead.json
else
  echo 'null' > /tmp/lead.json
fi
```

#### Grok path (when Apollo is not configured — default)

1. Confirm the Chrome MCP tab is open at `grok.com`, logged in as the user, with Fast mode selected. If not, set it up before continuing (one-time per run).

2. For this article, dispatch a subagent following `skill/grok_enricher.md`. Pass:
   - `company_name`: from extracted JSON
   - `city`: from extracted JSON (or null)
   - `description`: short paraphrase of the article context (e.g., `"multifamily property management"`) — use the property_type field
   - `tab_id`: the Chrome tab ID

3. The subagent returns either:
   - `{"company_name": "...", "lead": {<Lead JSON>}}` — success
   - `{"company_name": "...", "lead": null}` — no decision-maker found (`lead_gap=True` downstream)
   - `{"error": "session_invalid", ...}` — re-check Chrome login, retry the batch

4. Extract the `lead` field and write to `/tmp/lead.json`:

   ```bash
   echo '<subagent_output>' | jq '.lead' > /tmp/lead.json
   ```
```

- [ ] **Step 2: Commit**

```bash
git add skill/aether_daily_routine.md
git commit -m "Wire daily routine to dispatch Grok enricher subagents (Apollo fallback)"
```

---

## Task 5: Update README

**Files:**
- Modify: `README.md`

Add a section documenting the SuperGrok requirement (Chrome browser + active session) and the dual-path enrichment.

- [ ] **Step 1: Edit `README.md`** — add a subsection after "Configure Pipedrive":

```markdown
### 2.5 Configure enrichment (choose one)

The pipeline enriches qualifying leads with decision-maker contact info. Two paths:

**Path A: Grok via Chrome (default, no extra cost beyond your SuperGrok subscription)**

- Open SuperGrok ([grok.com](https://grok.com)) in Chrome with the [Claude in Chrome](https://www.anthropic.com/news/claude-in-chrome) extension active.
- Log in to your SuperGrok account.
- Verify "Fast" mode is selected in the chat input (not Heavy/Expert).
- The daily routine's enricher subagent uses Claude in Chrome MCP to drive the session per-article.

**Path B: Apollo.io API (set `APOLLO_API_KEY` in `.env`)**

- Requires an Apollo subscription (~$99/mo+).
- When `APOLLO_API_KEY` is set, the routine uses Apollo and skips Grok entirely.
- Useful for headless CI or environments without a Chrome session.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "README: document SuperGrok-via-Chrome enrichment path"
```

---

## Task 6: End-to-end validation

**Files:** none — validation only.

- [ ] **Step 1: Run all tests**

```bash
uv run python -m unittest discover tests -v
```

Expected: ~46 tests OK.

- [ ] **Step 2: Manual parser smoke test** with the spike fixture

```bash
cat <<'EOF' | uv run python -m pipeline.cli.grok_parse
1. Michael Wilson
Current Title: Chief Operating Officer (COO), Mark-Taylor, Inc.
LinkedIn: https://www.linkedin.com/in/michael-wilson-2a982625a
Professional Email: Likely michael.wilson@mark-taylor.com
EOF
```

Expected stdout: JSON with `"name": "Michael Wilson"`, `"seniority": "c_suite"`, `"apollo_id": "grok"`.

- [ ] **Step 3: End-to-end via the routine** — manually walk one article

In a Claude Code session in the repo:
- Set env vars (`source ~/.aether-pipedrive.env`), with `APOLLO_API_KEY` UNSET so Grok path runs
- Pick one article from `uv run python -m pipeline.cli.fetch` output
- Manually craft an extracted JSON (since Google News extraction is the separate `#2` issue) with a real company name
- Dispatch the enricher subagent per `skill/grok_enricher.md`
- Verify the returned Lead JSON parses cleanly
- Pipe into `pipeline.cli.push`
- Verify the Lead lands in Pipedrive sandbox with a Person attached (from Grok's lead)

- [ ] **Step 4: Clean up sandbox + commit any fixes**

- [ ] **Step 5: Force-update PR #4** with the new commits, or open a follow-up PR

---

## Summary

6 tasks. ~250 lines of new Python (parser + CLI + tests), 2 new skill markdowns, README update. No new third-party deps. Test count goes 44 → ~46.

**What this does NOT do:**
- Does not resolve Google News URL extraction (#2) — orthogonal
- Does not change `BROWSER_UA` (#3) — orthogonal
- Does not parallelize Grok queries across articles — the browser is the serialization bottleneck; per-article subagents are for context isolation, not parallelism
- Does not handle SuperGrok session expiry beyond surfacing the error — automated re-auth needs the user's credentials at runtime, which we don't want to store
