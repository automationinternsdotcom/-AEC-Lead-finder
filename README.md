# Aether CRE Lead Finder (Claude Skill)

A Claude Code skill that scrapes Arizona commercial real estate news, scores each story for cleaning/facility service opportunity using AI judgment, and writes ranked leads to Google Sheets.

**Client:** Jordan Whitehurst, Aether Facility Services (Phoenix, AZ)

## How It Works

1. **`fetch_feeds.py`** fetches 3 Google News RSS feeds in parallel (~1-2 seconds, stdlib Python, no dependencies)
2. **Claude scores** each article against Jordan's domain criteria (new tenants, renovations, business openings, property manager changes, etc.)
3. **Results written** to Google Sheets via `gog` CLI

## Setup

### 1. Install the skill

Copy the `skill/` directory contents to `~/.claude/skills/aether-leads/`:

```bash
mkdir -p ~/.claude/skills/aether-leads
cp skill/SKILL.md skill/fetch_feeds.py ~/.claude/skills/aether-leads/
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

### 3. Run

In Claude Code, say any of:
- "run aether leads"
- "CRE leads"
- "aether pipeline"
- "Jordan's leads"

## Output

25 scored leads written to [Google Sheets](https://docs.google.com/spreadsheets/d/1DM5qOV3mfPcVbgx_Fj3gVEKS_PPYtAsinIu9Zg5Oxy4/edit) with columns:

| Column | Description |
|--------|-------------|
| Company | Developer or company involved |
| Property | Specific property or project name |
| Market | Phoenix, Scottsdale, Tempe, Mesa, etc. |
| Asset Type | industrial, office, retail, multifamily, etc. |
| Score | 0-100 opportunity score |
| Priority | HIGH (70-100), MEDIUM (40-69), LOW (<40) |
| Decision Maker | Role to target (Property Manager, Facilities Manager, etc.) |
| Contact Search | Google search query to find the decision maker |
| Service Angle | One-sentence pitch using Aether's brand voice |
| News Context | Summary of the article's relevance |
| Title | Article headline |
| Link | Source URL |
| Published | Publication date |

## Scoring Criteria

**HIGH (70-100):** New tenant occupancy, lease signings, renovations, construction completions, new business openings, property management changes, major expansions, multifamily lease-up, HOA stand-ups

**MEDIUM (40-69):** Land acquisitions, industrial/warehouse deals, general commercial transactions

**LOW (<40):** Market commentary, rate news, editorials, residential consumer stories, out-of-state mentions

## Architecture Decision

This is intentionally a Claude Code skill rather than a standalone Python application. Claude IS the scoring engine, so there's no need to call the Claude API from Python. The skill approach is lower maintenance, has no API costs (covered by Claude subscription), and keeps the scoring logic in natural language where it's easy to adjust.

The only Python component is `fetch_feeds.py` which handles the network I/O that Claude's WebFetch tool is too slow for.
