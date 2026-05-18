# Aether CRE Lead Finder (Claude Skill)

A Claude Code skill that scrapes Arizona commercial real estate news, scores each story for cleaning/facility service opportunity using AI judgment, and writes ranked leads to Google Sheets.

**Client:** Jordan Whitehurst, Aether Facility Services (Phoenix, AZ)

## How It Works

1. **`fetch_feeds.py`** fetches 3 Google News RSS feeds in parallel (~1-2 seconds, stdlib Python, no dependencies)
2. **Claude scores** each article against Jordan's domain criteria (new tenants, renovations, business openings, property manager changes, etc.)
3. **Results written** to two Google Sheets tabs via `gog` CLI:
   - **Leads** - filtered, scored, and enriched with contact info
   - **Feed History** - full audit log of every article seen, with filter reasons

## Setup

### 1. Install the skill

Copy the `skill/` directory contents to `~/.claude/skills/aether-leads/`:

```bash
mkdir -p ~/.claude/skills/aether-leads
cp skill/SKILL.md skill/fetch_feeds.py skill/setup_sheets.py ~/.claude/skills/aether-leads/
```

### 2. Install and authenticate gog CLI

```bash
brew install pterm/tap/gog
```

Set up Google Cloud OAuth credentials (one-time):
1. Create a Google Cloud project at https://console.cloud.google.com
2. Enable the Google Sheets API
3. Create OAuth 2.0 Desktop credentials
4. Download the JSON and run: `gog auth credentials set --file <path-to-json>`
5. Add your email as a test user in the OAuth consent screen
6. Authenticate: `GOG_KEYRING_PASSWORD=aether gog login <your-email>`

### 3. Set up the Google Sheet tabs

```bash
GOG_KEYRING_PASSWORD=aether python3 ~/.claude/skills/aether-leads/setup_sheets.py
```

This creates the "Leads" and "Feed History" tabs in the target spreadsheet.

### 4. Run

In Claude Code, say any of:
- "run aether leads"
- "CRE leads"
- "aether pipeline"
- "Jordan's leads"

## Output

### Leads tab

Top 20-25 scored leads with columns:

| Column | Description |
|--------|-------------|
| Article | Hyperlinked article title |
| Date Posted | Publication date |
| Deal Size | Estimated property/deal value (if available) |
| Score | 0-100 opportunity score |
| Priority | HIGH (70-100), MEDIUM (40-69), LOW (<40) |
| Filter Reason | Why this article scored the way it did |
| Lead 1-3 | Specific contact names with titles and companies |
| Lead 1-3 Source | Google/LinkedIn search queries to find/verify each contact |
| Service Angle | One-sentence pitch using Aether's brand voice |

### Feed History tab

Full audit log of every article from every run:

| Column | Description |
|--------|-------------|
| Run Date | When the pipeline ran |
| Article | Hyperlinked article title |
| Date Posted | Publication date |
| Source Feed | Which RSS feed (az-cre, phoenix-dev, tucson-cre) |
| Score | Assigned score |
| Priority | HIGH/MEDIUM/LOW |
| Filter Reason | Why it was included or excluded |
| Included in Leads | Yes/No |

## Scoring Criteria

**HIGH (70-100):** New tenant occupancy, lease signings, renovations, construction completions, new business openings, property management changes, major expansions, multifamily lease-up, HOA stand-ups

**MEDIUM (40-69):** Land acquisitions, industrial/warehouse deals, general commercial transactions

**LOW (<40):** Market commentary, rate news, editorials, residential consumer stories, out-of-state mentions

## Contact Enrichment

Claude provides best-guess leads based on article context (names, titles, companies, LinkedIn search queries). For verified contact info (emails, phone numbers, direct LinkedIn profiles), a dedicated enrichment step is recommended:
- Grok (current best option for quick enrichment)
- Apollo.io, Vayne.io, or LinkedIn Sales Navigator (for production-grade lead finding)

## Architecture Decision

This is intentionally a Claude Code skill rather than a standalone Python application. Claude IS the scoring engine, so there's no need to call the Claude API from Python. The skill approach is lower maintenance, has no API costs (covered by Claude subscription), and keeps the scoring logic in natural language where it's easy to adjust.

Python components:
- `fetch_feeds.py` - parallel RSS fetching (stdlib, no dependencies)
- `setup_sheets.py` - one-time tab creation via Sheets API

## Sheet

https://docs.google.com/spreadsheets/d/1DM5qOV3mfPcVbgx_Fj3gVEKS_PPYtAsinIu9Zg5Oxy4/edit
