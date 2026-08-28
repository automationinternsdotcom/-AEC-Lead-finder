# /// script
# requires-python = ">=3.12"
# dependencies = ["python-dotenv", "certifi"]
# ///
"""Apollo.io People Match -- last-resort contact lookup."""
from __future__ import annotations

import csv
import os
import sys
from datetime import date

from apollo_api import find_contact
from csvio import write_csv

ORG, CHECKED, PHONE_REQ = ("apollo_org", "apollo_checked", "apollo_phone_requested")


def needs_apollo(row):
    return not (row.get("email", "").strip() or row.get("phone", "").strip())


def should_reveal_phone(row, found):
    return (
        not (row.get("email", "").strip() or row.get("phone", "").strip())
        and bool(found.get("org") or found.get("linkedin"))
        and not row.get(PHONE_REQ, "").strip()
    )


def enrich_csv(path, spend=False, limit=None, phones=False):
    phone_webhook = os.getenv("APOLLO_WEBHOOK_URL", "")
    if phones and not phone_webhook:
        raise SystemExit("--phones needs APOLLO_WEBHOOK_URL in .env")
    with open(path, newline="", encoding="utf-8") as file:
        rows = list(csv.DictReader(file))
    if not rows:
        return
    fields = list(rows[0]) + [f for f in (ORG, CHECKED, PHONE_REQ) if f not in rows[0]]
    todo = [row for row in rows if needs_apollo(row) and not row.get(CHECKED, "").strip()][:limit]
    print(f"{len(rows)} people, {len(todo)} to look up, <= {len(todo)} credits", file=sys.stderr)
    if not spend:
        for row in todo:
            print(f"  would look up {row['person']} ({row['business_name']})", file=sys.stderr)
        print("dry run - pass --go to spend credits", file=sys.stderr)
        return
    billed = 0
    try:
        for row in todo:
            found = find_contact(row["person"], row["business_name"])
            for column in ("email", "phone", "linkedin"):
                row[column] = row.get(column, "").strip() or found[column]
            row[ORG], row[CHECKED] = found["org"], date.today().isoformat()
            billed += any(found[k] for k in ("email", "phone", "linkedin", "org"))
            if phones and should_reveal_phone(row, found):
                find_contact(row["person"], row["business_name"], phone_webhook)
                row[PHONE_REQ] = date.today().isoformat()
                print(f"  {row['person']} -> phone reveal requested", file=sys.stderr)
            print(f"  {row['person']} -> {found['email'] or found['phone'] or '(nothing)'} [{found['org'] or 'no match'}]", file=sys.stderr)
    finally:
        write_csv(path, rows, fields)
        print(f"wrote {path}: {billed} matched (~{billed} credits)", file=sys.stderr)


def self_test():
    import apollo_api

    apollo_api._self_check()
    assert needs_apollo({"email": "", "phone": "", "linkedin": "li"})
    assert not needs_apollo({"email": "a@b.c"}) and not needs_apollo({"email": "", "phone": "555"})
    assert should_reveal_phone({"email": "", "phone": ""}, {"org": "Acme", "linkedin": ""})
    assert not should_reveal_phone({"email": "a@b.c", "phone": ""}, {"org": "Acme"})
    assert not should_reveal_phone({"email": "", "phone": ""}, {"org": "", "linkedin": ""})
    assert not should_reveal_phone({"email": "", "phone": "", PHONE_REQ: "2026-08-05"}, {"org": "Acme"})


if __name__ == "__main__":
    argv = sys.argv[1:]
    if argv[:1] == ["--self-test"]:
        self_test()
        print("ok")
    elif argv[:1] == ["--csv"] and len(argv) >= 2:
        limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else None
        enrich_csv(argv[1], spend="--go" in argv, limit=limit, phones="--phones" in argv)
    else:
        raise SystemExit(
            f"Usage: {sys.argv[0]} --csv contacts.csv [--limit N] [--go] [--phones]\n"
            f"       (dry run without --go; --phones needs APOLLO_WEBHOOK_URL)"
        )
