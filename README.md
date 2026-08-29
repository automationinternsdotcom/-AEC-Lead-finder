# Aether AEC Lead Finder

Aether AEC Lead Finder is a GPS-style lead pipeline for Aether Facility Services.
It finds Arizona commercial-real-estate activity, enriches each qualified lead with
decision makers and contact data, scores the list, builds a daily HTML lead email,
and creates Pipedrive deals in Aether's Pipeline.

The canonical runner is:

```bash
uv run scout/pipeline.py
```

## Architecture

This repo follows the same `scout/` architecture as
[`gps-grok-leadfinder`](https://github.com/automationinternsdotcom/gps-grok-leadfinder).
The one intentional difference is discovery:

- GPS discovers articles through Google News and provider expansion.
- Aether AEC discovers articles from the curated root file `news_websites.csv`.

The active article pipeline writes CSV outputs plus `leads_email.html`, then pushes
each article lead to Pipedrive as a deal in Aether's Pipeline. The separate
AltaVista mailbox worker pushes forwarded AltaVista opportunities to Phoenix-BizDev.

## Folder Layout

| Path | Purpose |
|---|---|
| `scout/` | Active GPS-style pipeline code. |
| `news_websites.csv` | Curated source list used by AEC discovery. |
| `results/YYYY-MM-DD/` | Generated lead CSVs and `leads_email.html`. Ignored by git. |
| `scout/logs/` | Stage logs. Ignored by git. |
| `.github/workflows/test.yml` | CI tests. Does not spend Apollo credits. |
| `.github/workflows/nightly-scout.yml` | Manual/scheduled production run. Can spend Apollo credits. |
| `.github/workflows/altavista-leads.yml` | Scheduled AltaVista mailbox worker for Phoenix-BizDev. |
| `scripts/altavista-leads.js` | Standalone AltaVista email-to-deal worker. |
| `check.sh` | Fast local self-checks for the `scout/` modules. |
| `run-nightly.sh` | Local LaunchAgent wrapper around `uv run scout/pipeline.py`. |
| `pipeline/` | Legacy AEC/Pipedrive path kept for compatibility and historical tests. Not the canonical runner. |

## Requirements

- Python 3.12 or newer
- `uv`
- A Responses-compatible API endpoint with access to Grok and web search
- `news_websites.csv`
- Apollo.io API key if you want Apollo fallback enrichment

## Configuration

Local runs read `.env` from the repository root. Start from `.env.example`:

```env
CLIPROXY_BASE_URL=http://localhost:8317/v1
CLIPROXY_API_KEY=your-key-here
GROK_MODEL=grok-4.3
EXTRACTOR_MODEL=grok-3-mini
DB_PATH=scout.db
RESULTS_DIR=results
NEWS_WEBSITES_CSV=news_websites.csv

APOLLO_API_KEY=your-apollo-key-here
APOLLO_WEBHOOK_URL=

PIPEDRIVE_API_TOKEN=your-pipedrive-token-here
PIPEDRIVE_DOMAIN=aether
PIPEDRIVE_FIELD_ARTICLE_URL=cad02131af9ab1c52e857604a0271aeb82e5cbe7
PIPEDRIVE_ARTICLE_DEAL_PIPELINE_ID=47
PIPEDRIVE_ARTICLE_DEAL_STAGE_ID=311
```

Do not commit `.env` or any real API key.

## GitHub Secrets

The production GitHub workflow reads these secrets:

| Secret | Required | Used for |
|---|---:|---|
| `CLIPROXY_BASE_URL` | Yes | Responses API endpoint. |
| `CLIPROXY_API_KEY` | Yes | Responses API auth. |
| `APOLLO_API_KEY` | Yes | Apollo fallback when `--apollo-go` is enabled. |
| `APOLLO_WEBHOOK_URL` | No | Apollo phone reveal webhook if phone reveal is added. |
| `PIPEDRIVE_API_TOKEN` | Yes | Creates article deals and AltaVista deals in Pipedrive. |

Optional repository variables:

| Variable | Default |
|---|---|
| `GROK_MODEL` | `grok-4.3` |
| `EXTRACTOR_MODEL` | `grok-3-mini` |

Add the Apollo key in GitHub at:

`Settings -> Secrets and variables -> Actions -> New repository secret -> APOLLO_API_KEY`

## Running Locally

Install dependencies:

```bash
uv sync
```

Run the full pipeline without Apollo spending:

```bash
uv run scout/pipeline.py
```

Run with Apollo fallback enabled:

```bash
uv run scout/pipeline.py --apollo-go
```

Useful spend controls:

```bash
uv run scout/pipeline.py --max-articles 10
uv run scout/pipeline.py --workers 10
```

Apollo credits are only spent when `--apollo-go` is present.

## Pipeline Steps

| Step | Program | Output |
|---|---|---|
| 1 | `scout/run.py` | `raw_leads.csv`, `uncertain_leads.csv` |
| 2 | `scout/find_decision_maker.py` pass 1 and pass 2 | Adds `Decision_Makers` and headcount data |
| 3 | `scout/agent_lead_enrichment.py` pass 1 and pass 2 | `contacts.csv` |
| 4 | `scout/apollo_lead_enrichment.py` | Fills missing email/phone/LinkedIn fields |
| 5 | `scout/score_leads.py` | Scores and sorts leads/contacts |
| 6 | `scout/build_email.py` | `leads_email.html` |
| 7 | `scout/push_deals.py` | Creates article deals in Aether's Pipeline |

## Outputs

Each run writes to `results/YYYY-MM-DD/`:

| File | Contents |
|---|---|
| `raw_leads.csv` | Qualified sales-ready AEC leads. |
| `uncertain_leads.csv` | Plausible but low-confidence leads. |
| `contacts.csv` | Decision makers and contact data. |
| `leads_email.html` | HTML lead digest ready to review/send. |

The database `scout.db` stores seen articles and rejected links so the same article is
not judged repeatedly.

Article deal duplicate protection uses the Pipedrive `Article URL` deal custom
field. The production key is `cad02131af9ab1c52e857604a0271aeb82e5cbe7`.

## AltaVista Leads

`.github/workflows/altavista-leads.yml` runs every 15 minutes and reads the
Pipedrive-synced mailbox for `akhil@automationinterns.com`. AltaVista messages
from or forwarded from `mward@altavistasp.com` are added as deals in pipeline
`28` (`Phoenix-BizDev`), stage `178` (`Qualified`). Discussion threads that only
mention AltaVista or Panera are skipped unless they contain the required lead
fields.

## GitHub Production Run

`.github/workflows/nightly-scout.yml` can run the pipeline from GitHub Actions:

- Manually through `Actions -> nightly scout -> Run workflow`
- Automatically every day at `13:00 UTC`

The workflow validates required secrets, restores `scout.db` from the Actions cache,
runs `uv run scout/pipeline.py --apollo-go`, uploads the generated results as an
artifact, and saves the updated database cache.

The final scout step pushes article leads to pipeline `47` (`Aether's Pipeline`),
stage `311` (`Qualified Lead`).

## Local Nightly Run

`run-nightly.sh` is the local wrapper for launchd:

```bash
./run-nightly.sh --max-articles 5
```

It loads `.env`, writes a timestamped log under `logs/`, and exits with the same status
as the scout pipeline.

## Checks

Fast module self-checks:

```bash
./check.sh
```

Full test suite:

```bash
uv run pytest -q
```

AltaVista worker syntax check:

```bash
node --check scripts/altavista-leads.js
```
