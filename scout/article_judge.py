"""One Grok call that visits an article URL, qualifies it, and extracts an AEC lead."""
from __future__ import annotations

import json
from urllib.parse import urlparse

import config
import db
import llm
import logbook
from prompts import JUDGE_PROMPT

FIELDS = [
    "business_name", "person", "event", "date_posted", "location", "summary",
    "state", "priority", "property_type", "service_angle", "filter_reason",
]


def judge_article(feed_entry):
    prompt = JUDGE_PROMPT.format(link=feed_entry["link"], title=feed_entry["title"])
    try:
        raw = llm.call(config.GROK_MODEL, prompt, tools=[{"type": "web_search"}])
        judged_article = llm.parse_json(raw)
    except Exception as e:
        logbook.log("judge", f"error on {feed_entry.get('title', '')}: {e}")
        return None

    is_qualified = (
        isinstance(judged_article, dict)
        and judged_article.get("qualified") is not False
        and judged_article.get("business_name")
        and judged_article.get("state") == "Arizona"
        and judged_article.get("priority") in {"high", "medium"}
    )
    if not is_qualified:
        db.mark_rejected(feed_entry["link"])
        logbook.log("judge", f"rejected: {feed_entry.get('title', '')}")
        return None

    row = {field: str(judged_article.get(field, "") or "") for field in FIELDS}
    row["confidence"] = "low" if judged_article.get("confidence") == "low" else "high"
    row["date_posted"] = row["date_posted"] or str(feed_entry.get("published_iso", ""))
    row["link"] = str(feed_entry["link"])
    row["source_site"] = str(feed_entry["source_site"])
    real_article_url = str(judged_article.get("article_url", "") or "")
    if real_article_url.startswith("http") and "news.google.com" not in real_article_url:
        row["link"] = real_article_url
        parsed = urlparse(real_article_url)
        row["source_site"] = f"{parsed.scheme}://{parsed.netloc}"
    logbook.log(
        "judge",
        f"{'uncertain' if row['confidence'] == 'low' else 'qualified'}: {row['business_name']}",
    )
    return row


def _self_check():
    import tempfile

    config.DB_PATH = tempfile.mktemp(suffix=".db")
    db.init_db()
    entry = {
        "title": "Tempe retail center signs tenants",
        "link": "https://example.com/a",
        "published_iso": "2026-08-26",
        "source_site": "https://example.com",
    }
    llm.call = lambda model, prompt, tools=(): json.dumps({
        "qualified": True,
        "business_name": "Tempe Retail Center",
        "person": "",
        "event": "New tenants signed leases.",
        "date_posted": "2026-08-26",
        "location": "Tempe, Arizona",
        "summary": "A retail center signed new tenants.",
        "state": "Arizona",
        "article_url": "https://publisher.test/a",
        "priority": "high",
        "property_type": "retail",
        "service_angle": "Aether can support asset preservation as occupancy ramps.",
        "filter_reason": "New tenants create active operating space.",
        "confidence": "high",
    })
    row = judge_article(entry)
    assert row is not None and row["business_name"] == "Tempe Retail Center"
    assert row["link"] == "https://publisher.test/a"

    llm.call = lambda model, prompt, tools=(): json.dumps({"qualified": False})
    assert judge_article(entry) is None
    print("article_judge self-check passed")


if __name__ == "__main__":
    _self_check()
