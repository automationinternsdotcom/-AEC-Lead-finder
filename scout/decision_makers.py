"""Grok web-search lookup of an AEC lead's decision makers."""
from __future__ import annotations

from datetime import date

import config
import llm


def find_decision_maker(business, location):
    prompt = f"""Search the web for up to three current decision makers for this Arizona commercial-property lead.
Business/project/operator: {business}
Area or region: {location}
Date: {date.today().isoformat()}

Prefer people with authority over the property, local operations, facilities,
asset management, property management, development, leasing, or ownership. Do
not return unrelated global executives when a local/regional operator exists.
Verify each person's current role and geographic responsibility. Never guess.

For the employee count, run a separate search. Report the count as a number or
range, say whether it covers the whole company or only this location, and give
the year it refers to.

Return only JSON:
{{"business":"","location":"","decision_makers":[{{"name":"","title":"","scope":""}}],"employee_count":{{"value":"","scope":"company|location","as_of":"","confidence":"high|medium|low"}},"confidence":"high|medium|low","sources":[{{"url":"","supports":""}}]}}
If no person can be verified, return an empty "decision_makers" list.
If the employee count cannot be verified, return null for "employee_count"."""
    text = llm.call(config.GROK_MODEL, prompt, tools=[{"type": "web_search"}])
    return parse_result(text)


def parse_result(text):
    result = llm.parse_json(text) or {}
    result["decision_makers"] = (result.get("decision_makers") or [])[:3]
    result.setdefault("employee_count", None)
    result["sources"] = [
        s for s in result.get("sources", []) if str(s.get("supports", "")).strip()
    ]
    return result


def format_person(person):
    label = " — ".join(
        p for p in (person.get("name"), person.get("title")) if str(p or "").strip()
    )
    scope = str(person.get("scope") or "").strip()
    return (f"{label} ({scope})" if label and scope else label).replace(";", ",")


def format_employees(count):
    value = str((count or {}).get("value") or "").strip()
    detail = ", ".join(
        str((count or {}).get(k) or "").strip()
        for k in ("scope", "as_of")
        if str((count or {}).get(k) or "").strip()
    )
    return f"{value} ({detail})" if value and detail else value


def people(result):
    return [p for p in (format_person(x) for x in result["decision_makers"]) if p]
