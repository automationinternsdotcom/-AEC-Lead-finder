"""Fill Decision_Makers / Employee_Count columns of a leads CSV, in place."""
from __future__ import annotations

import csv
import json
import sys
from concurrent.futures import ThreadPoolExecutor

import decision_makers as dm
from csvio import write_csv

DM_FIELDS = ["Decision_Makers", "Employee_Count", "Decision_Maker_Sources"]


def search_row(row):
    business, loc, state = (
        row["business_name"],
        row.get("location", "").strip(" ,"),
        row.get("state", "").strip(),
    )
    query_location = loc if state.lower() in loc.lower() else ", ".join(x for x in (loc, state) if x)
    try:
        people = dm.people(result := dm.find_decision_maker(business, query_location))
        for aka in (
            filter(None, (a.strip() for a in row.get("aka", "").split(",")))
            if not people
            else ()
        ):
            people = dm.people(result := dm.find_decision_maker(aka, query_location))
            if people:
                print(f"  {business} -> aka '{aka}' found decision makers", file=sys.stderr)
                break
    except Exception as error:
        print(f"  {business} -> FAILED {error!r}", file=sys.stderr)
        return {}
    print(f"  {business} -> {len(people)}: {'; '.join(people) or '(none)'}", file=sys.stderr)
    return {
        "Decision_Makers": "; ".join(people),
        "Employee_Count": dm.format_employees(result.get("employee_count")),
        "Decision_Maker_Sources": " ".join(
            str(s.get("url") or "").strip() for s in result["sources"]
        ).strip(),
    }


def enrich_csv(path):
    with open(path, newline="", encoding="utf-8") as file:
        rows = list(csv.DictReader(file))
    if not rows:
        return
    fields = list(rows[0]) + [f for f in DM_FIELDS if f not in rows[0]]
    todo = [row for row in rows if not row.get("Decision_Makers", "").strip()]
    print(f"{len(rows)} leads, {len(todo)} to search", file=sys.stderr)
    with ThreadPoolExecutor(6) as pool:
        for row, found in zip(todo, pool.map(search_row, todo)):
            row.update(found)
    write_csv(path, rows, fields)
    counts = [
        len(row.get("Decision_Makers", "").split("; ")) if row.get("Decision_Makers") else 0
        for row in rows
    ]
    print(
        f"wrote {path}: {sum(1 for c in counts if c > 1)} leads with multiple, "
        f"{sum(1 for c in counts if c == 1)} with one, {sum(1 for c in counts if not c)} with none",
        file=sys.stderr,
    )


if __name__ == "__main__":
    if sys.argv[1:2] == ["--csv"] and len(sys.argv) == 3:
        enrich_csv(sys.argv[2])
    elif len(sys.argv) != 3:
        raise SystemExit(
            f'Usage: {sys.argv[0]} "Business Name" "Location"\n'
            f"       {sys.argv[0]} --csv results/DATE/raw_leads.csv"
        )
    else:
        print(json.dumps(dm.find_decision_maker(*sys.argv[1:]), ensure_ascii=False, indent=2))
