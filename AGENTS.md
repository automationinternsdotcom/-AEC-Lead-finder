# Aether AEC Scout Pipeline

This repository follows the `gps-grok-leadfinder` operating pattern.

The canonical daily command is:

```bash
uv run scout/pipeline.py
```

The pipeline runs seven GPS-style steps:

1. `scout/run.py` discovers articles, judges them, deduplicates them, and writes
   `raw_leads.csv` plus `uncertain_leads.csv`.
2. `scout/find_decision_maker.py` fills decision makers in `raw_leads.csv`.
3. `scout/agent_lead_enrichment.py` writes one contact row per person to
   `contacts.csv`.
4. `scout/apollo_lead_enrichment.py` optionally fills missing contact data.
5. `scout/score_leads.py` scores and sorts the lead/contact CSVs.
6. `scout/build_email.py` writes `leads_email.html`.
7. `scout/push_deals.py` creates article deals in Aether's Pipeline.

The only intentional architecture difference from `gps-grok-leadfinder` is discovery:
GPS uses Google News/provider expansion, while Aether AEC uses the curated
`news_websites.csv` file in the repo root.

Start commands from the repo root. Keep secrets in `.env`; do not commit them.
Use `--apollo-go` only when the operator explicitly wants Apollo credits spent.
