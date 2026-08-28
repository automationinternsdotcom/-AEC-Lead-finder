"""Explode each lead's decision makers into contact rows and enrich them."""
from __future__ import annotations

import csv
import os
import sys
from concurrent.futures import ThreadPoolExecutor

from contact_search import contact_row, headcount, people_of, pinpoint
from csvio import write_csv

CONTACT_FIELDS = (
    "business_name state location event date_posted summary link "
    "employee_count person title linkedin email phone sources"
).split()


def enrich_leads(leads_path, contacts_path):
    with open(leads_path, newline="", encoding="utf-8") as file:
        leads = list(csv.DictReader(file))
    tasks = [
        dict.fromkeys(CONTACT_FIELDS, "")
        | {
            field: lead.get(field, "")
            for field in ("business_name", "state", "event", "date_posted", "summary", "link")
        }
        | {
            "location": pinpoint(lead),
            "employee_count": lead.get("Employee_Count", ""),
            "person": name,
            "title": title,
        }
        for lead in leads
        for name, title in people_of(lead)
    ]
    previous = previous_rows(contacts_path)
    todo = [task for task in tasks if not reachable(previous.get(key_of(task)))]
    print(f"{len(leads)} leads, {len(tasks)} people, {len(todo)} to search", file=sys.stderr)
    with ThreadPoolExecutor(6) as pool:
        results = {key_of(row): row for row in pool.map(contact_row, todo)}
    tokens = sum(row.pop("_tokens", 0) for row in results.values())
    rows = []
    for task in tasks:
        found = results.get(key_of(task)) or {}
        rows.append(
            (previous.get(key_of(task)) or {})
            | {f: v for f, v in task.items() if v}
            | {f: v for f, v in found.items() if v}
        )
    rows.sort(key=lambda row: (-headcount(row.get("employee_count", "")), row["business_name"]))
    fields = CONTACT_FIELDS + [f for row in rows for f in row if f not in CONTACT_FIELDS and f != "_tokens"]
    fields = list(dict.fromkeys(fields))
    write_csv(contacts_path, rows, fields)
    hit = sum(1 for row in rows if any(row.get(k) for k in ("linkedin", "email", "phone")))
    print(
        f"wrote {contacts_path}: {len(rows)} people, {hit} with a contact, "
        f"{len(rows) - hit} with none ({tokens} tokens)",
        file=sys.stderr,
    )


def key_of(row):
    return row["business_name"], row["person"]


def reachable(row):
    return bool(row) and any(row.get(k, "").strip() for k in ("linkedin", "email", "phone"))


def previous_rows(path):
    if not os.path.exists(path):
        return {}
    with open(path, newline="", encoding="utf-8") as file:
        return {key_of(row): row for row in csv.DictReader(file)}


def self_test():
    from contact_search import parse_contact

    assert parse_contact('```json\n{"email":"a@b.c"}\n```') == {"email": "a@b.c"}
    assert parse_contact("no json") == {}
    assert list(people_of({"Decision_Makers": "Al Bryan — Owner (M); Jo Lee — GM"})) == [
        ("Al Bryan", "Owner (M)"),
        ("Jo Lee", "GM"),
    ]
    assert list(people_of({})) == [] and list(people_of({"Decision_Makers": "Solo"})) == [("Solo", "")]
    assert [headcount(v) for v in ("201-500 (x)", "51-200 (company, 2026)", "11-50 (location, 2026)", "")] == [500, 200, 50, -1]
    assert reachable({"email": "a@b.c"}) and not reachable({"email": " "}) and not reachable(None)


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        self_test()
        raise SystemExit(print("ok"))
    if sys.argv[1:2] == ["--csv"] and len(sys.argv) == 4:
        raise SystemExit(enrich_leads(sys.argv[2], sys.argv[3]))
    raise SystemExit(
        f"Usage: {sys.argv[0]} --csv raw_leads.csv contacts.csv | --self-test\n"
        f"       (single-person lookup lives in contact_search.py)"
    )
