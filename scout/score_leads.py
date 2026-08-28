"""Score every AEC business lead 0-100 for outreach priority."""
from __future__ import annotations

import csv
import json
import sys

import config
import llm

PROMPT = """You score Arizona commercial-property leads for Aether Facility Services.
For each lead below, give an integer score 0-100 for outreach priority:
- Activity fit: lease-up, opening, new tenancy, management change, construction completion high
- Property fit: multifamily and active commercial operations high
- Timeline urgency: operating now or soon high; land acquisition lower
- Location: Phoenix metro and Tucson higher
- Reachability: named decision maker with email or phone higher

Hard zero rule:
- Score 0 if the row is not Arizona commercial-property activity.
- Score 0 if it is only macro commentary, rankings, awards, or residential consumer news.

Leads (JSON):
{leads}

Return STRICT JSON only: {{"id": score, ...}} with every lead's "id" from the
input as a key, exactly as given."""


def score_businesses(rows):
    leads = [
        {"id": str(i)}
        | {k: row.get(k, "") for k in (
            "business_name", "event", "date_posted", "location", "summary",
            "Employee_Count", "Decision_Makers", "priority", "property_type",
        )}
        for i, row in enumerate(rows)
    ]
    raw = llm.call(config.GROK_MODEL, PROMPT.format(leads=json.dumps(leads)))
    scores = llm.parse_json(raw) or {}
    return {
        rows[int(i)]["business_name"]: int(value)
        for i, value in scores.items()
        if str(i).isdigit() and int(i) < len(rows)
        and isinstance(value, (int, float)) and 0 <= int(value) <= 100
    }


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as file:
        return list(csv.DictReader(file))


def write_csv(path, rows, fields):
    with open(path, "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fields)
        writer.writeheader()
        writer.writerows({f: row.get(f, "") for f in fields} for row in rows)


def score_value(row):
    try:
        return int(row.get("score") or 0)
    except (TypeError, ValueError):
        return 0


def apply_scores(leads_path, contacts_path):
    leads = read_csv(leads_path)
    if not leads:
        return print("no leads to score", file=sys.stderr)
    lead_fields = list(leads[0]) + (["score"] if "score" not in leads[0] else [])
    original_count = len(leads)
    scores = score_businesses(leads)
    for row in leads:
        row["score"] = scores.get(row["business_name"], row.get("score", ""))
    leads = [row for row in leads if score_value(row) != 0]
    write_csv(leads_path, leads, lead_fields)
    kept_businesses = {row["business_name"] for row in leads}
    contacts = read_csv(contacts_path) if contacts_path else []
    contact_fields = list(contacts[0]) + (["score"] if contacts and "score" not in contacts[0] else []) if contacts else []
    for row in contacts:
        row["score"] = scores.get(row["business_name"], row.get("score", ""))
    contacts = [row for row in contacts if row["business_name"] in kept_businesses]
    contacts.sort(key=lambda r: -score_value(r))
    if contact_fields:
        write_csv(contacts_path, contacts, contact_fields)
    scored = sorted(scores.items(), key=lambda kv: -kv[1])
    print(
        f"scored {len(scores)}/{original_count} leads: "
        + ", ".join(f"{name} {score}" for name, score in scored[:5])
        + (" ..." if len(scored) > 5 else ""),
        file=sys.stderr,
    )


def _self_check():
    llm.call = lambda model, prompt, tools=(): '{"0": 90, "1": 101, "2": "high", "3": 55.0, "9": 80, "x": 70}'
    rows = [{"business_name": n} for n in ("A", "B", "C", "D")]
    assert score_businesses(rows) == {"A": 90, "D": 55}
    print("score_leads self-check passed")


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-check"]:
        _self_check()
    elif len(sys.argv) == 3:
        apply_scores(sys.argv[1], sys.argv[2])
    else:
        raise SystemExit(f"Usage: {sys.argv[0]} raw_leads.csv contacts.csv | --self-check")
