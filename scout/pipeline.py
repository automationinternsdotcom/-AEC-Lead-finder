# /// script
# requires-python = ">=3.12"
# dependencies = ["feedparser", "googlenewsdecoder", "httpx", "python-dotenv", "pyyaml", "trafilatura", "certifi"]
# ///
"""Chains the AEC scout pipeline: website discovery -> decision makers ->
enrichment -> Apollo -> scoring -> email -> Pipedrive Deals.
Stops on the first failing step.
"""
from __future__ import annotations

import argparse
import csv
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import config

REPO_ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--discover-states", type=int, default=0, help="accepted for GPS CLI compatibility; ignored")
    parser.add_argument("--apollo-go", action="store_true", help="spend Apollo credits")
    parser.add_argument("--max-articles", type=int, default=0, help="judge at most N new articles (0 = no limit)")
    parser.add_argument("--since", default=(date.today() - timedelta(days=1)).isoformat())
    args = parser.parse_args()

    stamp = date.today().isoformat()
    raw = str(Path(config.RESULTS_DIR) / stamp / "raw_leads.csv")
    contacts = str(Path(config.RESULTS_DIR) / stamp / "contacts.csv")

    steps = [
        (
            "step 1/7: scout fetch (daily, AEC websites)",
            [
                "uv", "run", "scout/run.py",
                "--workers", str(args.workers),
                "--discover-states", str(args.discover_states),
                "--stamp", stamp,
                "--max-articles", str(args.max_articles),
                "--since", args.since,
            ],
        ),
        ("step 2/7: decision makers (pass 1)", ["python3", "scout/find_decision_maker.py", "--csv", raw]),
        ("step 2/7: decision makers (pass 2, retry empties)", ["python3", "scout/find_decision_maker.py", "--csv", raw]),
        ("step 3/7: contact enrichment (pass 1)", ["python3", "scout/agent_lead_enrichment.py", "--csv", raw, contacts]),
        ("step 3/7: contact enrichment (pass 2, retry empties)", ["python3", "scout/agent_lead_enrichment.py", "--csv", raw, contacts]),
        (
            "step 4/7: apollo fallback lookup",
            ["uv", "run", "scout/apollo_lead_enrichment.py", "--csv", contacts, *(["--go"] if args.apollo_go else [])],
        ),
        ("step 5/7: score leads", ["python3", "scout/score_leads.py", raw, contacts]),
        ("step 6/7: build lead email", ["python3", "scout/build_email.py", stamp]),
        ("step 7/7: push article deals to Pipedrive", ["python3", "scout/push_deals.py", stamp]),
    ]

    for banner, cmd in steps:
        print(f"== {banner} ==", file=sys.stderr)
        subprocess.run(cmd, cwd=REPO_ROOT, check=True)

    text, lead_count = summary(stamp)
    print(text, file=sys.stderr)
    if lead_count == 0:
        print("ERROR: 0 leads found - failing the run", file=sys.stderr)
        sys.exit(1)


def summary(stamp):
    day_dir = REPO_ROOT / "results" / stamp

    def read(name):
        path = day_dir / name
        return list(csv.DictReader(path.open(encoding="utf-8"))) if path.exists() else []

    leads, uncertain, people = read("raw_leads.csv"), read("uncertain_leads.csv"), read("contacts.csv")
    with_dm = {r["business_name"] for r in leads if r.get("Decision_Makers", "").strip()}
    emails = sum(1 for r in people if r.get("email", "").strip())
    phone_only = sum(1 for r in people if r.get("phone", "").strip() and not r.get("email", "").strip())
    reachable = {r["business_name"] for r in people if r.get("email", "").strip() or r.get("phone", "").strip()}
    apollo = sum(1 for r in people if not (r.get("email", "").strip() or r.get("phone", "").strip()))
    top = sorted(leads, key=lambda r: -int(r.get("score") or 0))[:3]
    return (
        f"== summary {stamp} ==\n"
        f"leads: {len(leads)} sales-ready (+{len(uncertain)} uncertain)\n"
        f"top scores: "
        + ", ".join(f"{r['business_name']} {r.get('score', '?')}" for r in top)
        + "\n"
        f"decision makers: {len(with_dm)}/{len(leads)} businesses, {len(people)} people\n"
        f"contacts: {emails} with email, {phone_only} phone-only; {len(reachable)} businesses reachable\n"
        f"apollo candidates: {apollo} people (<= {apollo} credits)\n"
        f"files: {day_dir}/raw_leads.csv , contacts.csv , leads_email.html"
    ), len(leads)


if __name__ == "__main__":
    main()
