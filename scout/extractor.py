"""Dedup same-project AEC leads via a small LLM, matching GPS extractor.py."""
from __future__ import annotations

import json

import config
import llm

SLIM_FIELDS = ["link", "business_name", "event", "date_posted", "location", "state"]


def dedup_leads(rows):
    if len(rows) < 2:
        return rows
    slim = [{k: row.get(k, "") for k in SLIM_FIELDS} for row in rows]
    prompt = (
        "These are Arizona commercial-real-estate leads. Some rows describe the SAME "
        "property event reported by different outlets, or the same event collected twice. "
        "Group rows by property/project/transaction. Per group keep ONE row: prefer the "
        "row with a named person, then the most complete business_name and event. "
        "Return STRICT JSON only: an object mapping each KEPT row's link to a "
        "comma-separated string of the OTHER business_name spellings merged into it "
        "(\"\" if nothing was merged). Every event must appear exactly once.\n\n"
        + json.dumps(slim)
    )
    try:
        kept = llm.parse_json(llm.call(config.EXTRACTOR_MODEL, prompt))
    except Exception as e:
        print(f"dedup_leads: falling back to original rows after error: {e}")
        return rows
    if not isinstance(kept, dict) or not kept:
        return rows
    by_link = {row.get("link", ""): row for row in rows}
    out = []
    for link, aka in kept.items():
        row = by_link.get(link)
        if row is None:
            continue
        merged_aka = ", ".join(
            a for a in (row.get("aka", "").strip(), str(aka or "").strip()) if a
        )
        out.append({**row, "aka": merged_aka})
    return out or rows


if __name__ == "__main__":
    assert dedup_leads([{"business_name": "A"}]) == [{"business_name": "A"}]
    llm.call = lambda model, prompt: json.dumps({"http://a": "A LLC"})
    result = dedup_leads([
        {"link": "http://a", "business_name": "A", "person": "Jane", "score": "90"},
        {"link": "http://b", "business_name": "A LLC", "person": ""},
    ])
    assert result == [{"link": "http://a", "business_name": "A", "person": "Jane", "score": "90", "aka": "A LLC"}]
    print("extractor self-check passed")
