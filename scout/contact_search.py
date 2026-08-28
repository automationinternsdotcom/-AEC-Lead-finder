"""Grok web-search lookup of one person's LinkedIn / email / phone."""
from __future__ import annotations

import json
import re
import sys

import config
import llm


def find_contact(name, business, location):
    prompt = f"""Use web search to research this person:
Name: {name}
Business/project/operator: {business}
Location: {location}
Search multiple public sources for their LinkedIn profile, professional email,
and professional phone. Verify each result matches the person and business.
Never guess.
Return only JSON: {{"name":"{name}","linkedin":"","email":"","phone":"","sources":[]}}
Use "" when not found and put source URLs in "sources"."""
    text, usage = llm.call(
        config.GROK_MODEL,
        prompt,
        tools=[{"type": "web_search"}],
        text_format="json_object",
        with_usage=True,
    )
    return parse_contact(text), usage


def parse_contact(text):
    match = re.search(r"\{.*\}", re.sub(r"<<ccr:[^>]+>>", "", text), re.DOTALL)
    return json.loads(match.group()) if match else {}


def people_of(row):
    for entry in row.get("Decision_Makers", "").split(";"):
        name, _, title = entry.strip().partition(" — ")
        if name.strip():
            yield name.strip(), title.strip()


def pinpoint(lead):
    loc = lead.get("location", "").strip(" ,")
    state = lead.get("state", "").strip()
    return loc if state.lower() in loc.lower() else ", ".join(x for x in (loc, state) if x)


def headcount(value):
    numbers = [int(n) for n in re.findall(r"\d+", value.split("(")[0])]
    return max(numbers) if numbers else -1


def contact_row(task):
    try:
        result, usage = find_contact(task["person"], task["business_name"], task["location"])
    except Exception as error:
        print(f"  {task['person']} -> FAILED {error!r}", file=sys.stderr)
        return task | {"_tokens": 0}
    found = task | {
        "linkedin": str(result.get("linkedin") or "").strip(),
        "email": str(result.get("email") or "").strip(),
        "phone": str(result.get("phone") or "").strip(),
        "sources": " ".join(str(s or "").strip() for s in result.get("sources") or []),
        "_tokens": usage.get("total_tokens", 0),
    }
    hits = [key for key in ("linkedin", "email", "phone") if found[key]]
    print(f"  {task['person']} ({task['business_name']}) -> {', '.join(hits) or '(nothing)'}", file=sys.stderr)
    return found


if __name__ == "__main__":
    result, usage = find_contact(*sys.argv[1:])
    print(json.dumps(result, ensure_ascii=False))
    print(f"Tokens: {usage['total_tokens']} total", file=sys.stderr)
