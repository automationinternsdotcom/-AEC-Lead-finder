---
name: aether-daily-routine
description: Daily Aether lead pipeline. Fetches CRE news from Google News + RSS sources, extracts structured data per article, qualifies for Arizona CRE signals, enriches via Apollo (if configured), and pushes qualified items into Pipedrive's Leads Inbox for Jordan to triage. Drive this from a Codex session or a local cron with `codex exec`.
---

# Aether Daily Lead Pipeline

You are running the Aether daily lead pipeline. Your job: discover new commercial real-estate news in Arizona, decide which ones represent lead opportunities, enrich them with decision-maker contact info, and push qualified items into **Pipedrive Leads** — the inbox surface where Jordan triages incoming opportunities and converts the promising ones to Deals.

## Setup check

Source the env file (it contains all required secrets):

```bash
source ~/.aether-pipedrive.env
```

Verify env vars are loaded:

```bash
# Required: PIPEDRIVE_API_TOKEN, _DOMAIN, _FIELD_ARTICLE_URL — pipeline aborts without all 3.
env | grep -E '^PIPEDRIVE_(API_TOKEN|DOMAIN|FIELD_ARTICLE_URL)=' | wc -l  # Expect 3
# Optional but expected for the per-lead contact schema:
env | grep -E '^PIPEDRIVE_FIELD_(DATE_POSTED|LEAD_[123])=' | wc -l       # Expect 4 — warn if 0
```

If the required count is fewer than 3, stop and report the missing variables.
If the optional count is 0, warn that Date Posted + Lead 1/2/3 fields won't be populated and the routine will create degraded leads.

## Step 1: Discover URLs

```bash
uv run python -m pipeline.cli.fetch > /tmp/urls.json
jq length /tmp/urls.json
```

If 0 URLs, stop. Log "no new articles" and exit.

## Step 2: Per-article loop

For each entry in `/tmp/urls.json`:

```bash
URL_HASH=...   # from JSON entry
URL=...        # from JSON entry
```

### 2a. Extract article text

```bash
uv run python -m pipeline.cli.extract "$URL" > /tmp/article.txt 2> /tmp/extract.err
EXTRACT_RC=$?
```

If `EXTRACT_RC != 0`:
```bash
uv run python -m pipeline.cli.mark "$URL_HASH" failed
```
Continue to next article.

### 2b. Read the article text + apply the campaign qualification prompt

Render the assessment prompt from the campaign spec:

```bash
uv run python -m pipeline.cli.render_prompt assess --campaign aether-cleaning-az > /tmp/assess_prompt.md
```

Read `/tmp/assess_prompt.md` and `/tmp/article.txt`. Apply the rendered prompt to the article text, then write the structured JSON result to `/tmp/extracted.json`.

### 2c. Qualify

Pipe the JSON into qualify:

```bash
echo '<extracted_json>' | uv run python -m pipeline.cli.qualify
QUALIFY_RC=$?
```

If `QUALIFY_RC != 0`:
```bash
uv run python -m pipeline.cli.mark "$URL_HASH" filtered
```
Continue to next article.

### 2d. Enrich the lead

The enrichment order: **cache → Apollo (if configured) OR Grok with optional Assessor hint → cache the result.**

#### Cache check (always first)

```bash
COMPANY=$(echo '<extracted_json>' | jq -r '.company_name')
uv run python -m pipeline.cli.cache_lookup "$COMPANY" \
  | jq 'if . == null then {leads: []} else {leads: [.]} end' > /tmp/lead.json
if [ "$(jq '.leads | length' /tmp/lead.json)" -gt 0 ]; then
  echo "Cache hit for $COMPANY — skipping external enrichment"
  # Skip to 2e (push) — /tmp/lead.json holds {leads:[<cached Lead>]}
fi
```

`/tmp/lead.json` is the **canonical enrichment envelope** for the rest of the per-article loop: always `{"leads": [<Lead>, ...]}` (zero-or-more entries). Cache hit / Apollo / Grok all converge on this shape, and Step 2e reads `.leads` from it.

If `.leads | length` is > 0, skip ahead to 2e.

#### Maricopa Assessor hint (when address is present)

Aether is AZ-CRE, so most acquisition / development articles include a Maricopa-area property address. The Assessor returns the **legally-recorded owning entity** — often a holding LLC distinct from the operating company in the article. Used as an extra hint for the Grok query downstream.

```bash
ADDRESS=$(echo '<extracted_json>' | jq -r '.address // empty')
if [ -n "$ADDRESS" ]; then
  uv run python -m pipeline.cli.assessor_lookup "$ADDRESS" > /tmp/assessor.json
  OWNER_HINT=$(jq -r '.owner // empty' /tmp/assessor.json)
else
  OWNER_HINT=""
fi
```

The Assessor short-circuits non-Maricopa cities (Tucson, Flagstaff, etc.) with no HTTP request.

#### Apollo path (when `APOLLO_API_KEY` is set)

```bash
DOMAIN=$(echo '<extracted_json>' | jq -r '.company_domain_guess // empty')
if [ -n "$APOLLO_API_KEY" ] && [ -n "$DOMAIN" ]; then
  uv run python -m pipeline.cli.enrich "$DOMAIN" \
    | jq 'if . == null then {leads: []} else {leads: [.]} end' > /tmp/lead.json
  ENRICH_VIA=apollo
fi
```

#### Grok path (default — uses Claude in Chrome + SuperGrok)

**One-time setup per run (first article only):** confirm the Claude-in-Chrome tab is open at `grok.com`, logged in as the user, with **Fast mode** selected.

**Per article:** dispatch a subagent following `skill/grok_enricher.md` with these inputs:

- `company_name` — from `<extracted_json>.company_name`
- `city` — from `<extracted_json>.city` (or null)
- `description` — short paraphrase using `<extracted_json>.property_type` (e.g. `"multifamily property management"`)
- `owner_entity` — `$OWNER_HINT` from the Assessor step (or null if unset)
- `article_summary` — from `<extracted_json>.summary_2sent`
- `article_url` — `$URL` (the resolved publisher URL)
- `tab_id` — the Chrome tab ID from `tabs_context_mcp`

The subagent returns one of (matches `skill/grok_enricher.md` Step 8):

- `{"company_name": "...", "mode": "fast"|"expert", "leads": [<Lead>, <Lead>, <Lead>]}` — success (1–3 leads)
- `{"company_name": "...", "mode": "fast", "leads": []}` — no decision-maker found → `lead_gap=True` downstream
- `{"error": "session_invalid", ...}` — re-check the Chrome login, then retry the article

Save the full subagent envelope as the enrichment file:

```bash
echo '<subagent_output>' > /tmp/lead.json
ENRICH_VIA=grok
```

If the output is an `error` envelope (no `leads` key), Step 2e's `jq '.leads // []'` will degrade to `[]` and the article will push with no contacts attached. For session errors, prefer re-checking the Chrome login and retrying the article *before* writing to `/tmp/lead.json` — see `skill/grok_enricher.md` for the recovery flow.

#### Cache the successful enrichment

`cache_write` stores one Lead per org (the primary), so pull `.leads[0]` from the envelope before piping. Skip the cache step on cache hits (would be a no-op write of what's already there) and on empty enrichments.

```bash
if [ "${ENRICH_VIA:-}" != "" ] && [ "$(jq '.leads | length' /tmp/lead.json)" -gt 0 ]; then
  jq '.leads[0]' /tmp/lead.json \
    | uv run python -m pipeline.cli.cache_write "$COMPANY" "$ENRICH_VIA"
fi
```

(CLI args handle shell quoting correctly — company names with embedded
quotes or other punctuation pass through unchanged. The earlier heredoc
approach broke Python syntax on names like `Some "Quoted" Co`.)

### 2e. Push to Pipedrive Leads

The Grok enricher (skill/grok_enricher.md) now returns up to 3 leads as `{"company_name": "...", "leads": [<Lead>, <Lead>, <Lead>]}` — the first becomes the primary (linked Person record), the rest populate the Lead 2 / Lead 3 custom fields.

```bash
# /tmp/lead.json is always the {leads: [...]} envelope by this point
# (cache hit, Apollo, and Grok all write that shape; empty enrichments = {leads: []})
LEADS=$(jq '.leads // []' /tmp/lead.json)    # [] when no enrichment
PRIMARY=$(echo "$LEADS" | jq '.[0] // null')
EXTRAS=$(echo "$LEADS" | jq '.[1:]')         # [] when only one or zero

jq -n --argjson article '<extracted_json>' \
      --argjson lead "$PRIMARY" \
      --argjson extras "$EXTRAS" \
      --arg url "$URL" \
  '{article: $article, lead: $lead, extra_contacts: $extras, url: $url}' \
  | uv run python -m pipeline.cli.push > /tmp/push_result.json
```

The push CLI populates:
- Lead title = `article.title`
- Date Posted = `article.published_date` (when set, and `PIPEDRIVE_FIELD_DATE_POSTED` configured)
- Lead 1 / Lead 2 / Lead 3 = `Name | Title | Email | Phone` (when contacts exist + field hashes configured)

Read `/tmp/push_result.json`. If `skipped: true`, the URL was already in Pipedrive Leads (treat as success).

### 2f. Mark seen

```bash
uv run python -m pipeline.cli.mark "$URL_HASH" pushed
```

## Step 3: Jordan's feedback (since last run)

Pull the current set of Leads Jordan has flagged as not relevant — these are signals to manually tune the protocol over time (no automated suppression).

```bash
uv run python -m pipeline.cli.feedback > /tmp/feedback.json
FEEDBACK_RC=$?
```

If `FEEDBACK_RC == 3`: the `NOT RELEVANT` label doesn't exist in Pipedrive yet. Note this in the report and tell Jacob to create it in **Pipedrive → Settings → Lead labels**. Skip the rest of this step.

Otherwise read `/tmp/feedback.json` (a JSON array, possibly empty). For each flagged Lead, include in the report:
- Lead title
- Article URL
- Flagged-at timestamp

These flags don't change pipeline behavior — they're shown so the operator can spot patterns (e.g., "Tucson articles get flagged 80% of the time → consider disabling the tucson source") and update `skill/aether_daily_routine.md` Step 2b accordingly.

## Step 4: Summary

After the loop, report:
- Total URLs fetched
- Pushed (new Leads in Pipedrive's Leads Inbox, awaiting Jordan's triage)
- Skipped (already existed in Pipedrive)
- Filtered (didn't pass qualification rules)
- Failed (extract errors — usually paywalls or JS-rendered pages)
- **Jordan's feedback:** count of `NOT RELEVANT`-flagged Leads + the list from Step 3

Log a final `run_finished` event with the counts.
