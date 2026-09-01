"""End-to-end in-process pipeline and resume behavior with no live services."""
from __future__ import annotations

import csv
import json
import re
import sys
from dataclasses import replace
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scout"))

from v2.http import FetchResponse  # noqa: E402
from v2.orchestrator import PipelineOptions, PipelineRunner  # noqa: E402


def test_pipeline_runs_end_to_end_and_resume_skips_completed_stages(tmp_path):
    sources = tmp_path / "news_websites.csv"
    sources.write_text("Resource Name,URL\nExample,https://example.com/\n")
    pages = {
        "https://example.com/": """
            <link rel="alternate" type="application/rss+xml" href="/feed.xml">
            <a href="/2026/08/28/new-commercial-marketplace-opens-phoenix">A new commercial marketplace opens in Phoenix</a>
        """,
        "https://example.com/2026/08/28/new-commercial-marketplace-opens-phoenix": (
            '<meta property="article:published_time" content="2026-08-28T08:00:00Z">'
        ),
        "https://example.com/feed.xml": """<?xml version="1.0"?><rss version="2.0"><channel>
            <item><guid>story-1</guid><title>A new commercial marketplace opens in Phoenix</title>
            <link>https://example.com/2026/08/28/new-commercial-marketplace-opens-phoenix</link>
            <pubDate>Fri, 28 Aug 2026 08:00:00 GMT</pubDate></item>
        </channel></rss>""",
    }
    fetch_calls = []

    def fetch(url):
        fetch_calls.append(url)
        if url not in pages:
            raise RuntimeError("not found")
        return FetchResponse(url=url, content=pages[url].encode())

    model_calls = []

    def model_call(model, prompt, tools):
        model_calls.append(prompt)
        if "Qualify this bounded batch" in prompt:
            candidate_ids = re.findall(r'"candidate_id": "([^"]+)"', prompt)
            payload = {
                "qualified": True,
                "business_name": "Acme Marketplace",
                "person": "",
                "event": "Opened a new commercial marketplace.",
                "date_posted": "2026-08-28",
                "location": "Phoenix, Arizona",
                "summary": "Acme opened a marketplace.",
                "state": "Arizona",
                "priority": "high",
                "property_type": "retail",
                "service_angle": "Aether can serve as a strategic partner.",
                "filter_reason": "The property is beginning operations.",
                "confidence": "high",
            }
            return (
                json.dumps({candidate_id: payload for candidate_id in candidate_ids}),
                {"total_tokens": 100},
            )
        if "current decision makers" in prompt:
            return (
                json.dumps(
                    {
                        "decision_makers": [
                            {"name": "Jane Manager", "title": "General Manager", "scope": "Phoenix"}
                        ],
                        "employee_count": None,
                        "sources": [
                            {"url": "https://acme.example/team", "supports": "Lists Jane as GM."}
                        ],
                    }
                ),
                {"total_tokens": 50},
            )
        if "Find sourced professional" in prompt:
            return (
                json.dumps(
                    {
                        "name": "Jane Manager",
                        "organization": "Acme Marketplace",
                        "email": "jane@acme.example",
                        "phone": "",
                        "linkedin": "https://linkedin.com/in/jane-manager",
                        "sources": [
                            {
                                "url": "https://acme.example/team",
                                "supports": "Lists Jane's professional details.",
                            }
                        ],
                    }
                ),
                {"total_tokens": 60},
            )
        if "Score each Arizona" in prompt:
            event_id = re.search(r'"lead_event_id": "([^"]+)"', prompt).group(1)
            return json.dumps({event_id: 88}), {"total_tokens": 20}
        if "Select one approved Aether cold-email opening template" in prompt:
            event_id = re.search(r'"lead_event_id": "([^"]+)"', prompt).group(1)
            return (
                json.dumps(
                    {
                        "canonical_name": "Acme Marketplace",
                        "domain": "acme.example",
                        "employee_count": "",
                        "selection": {
                            "template_key": "opening",
                            "lead_event_id": event_id,
                            "slots": {
                                "property": "acme marketplace",
                                "location": "Phoenix",
                            },
                            "confidence": "high",
                            "source_urls": ["https://acme.example/team"],
                        }
                    }
                ),
                {"total_tokens": 15},
            )
        raise AssertionError(f"unexpected model prompt: {prompt[:80]}")

    options = PipelineOptions(
        db_path=str(tmp_path / "scout.db"),
        results_dir=str(tmp_path / "results"),
        sources_csv=str(sources),
        stamp="2026-08-28",
        since="2026-08-27",
        run_id="run-1",
    )
    result = PipelineRunner(
        options,
        fetch=fetch,
        model_call=model_call,
        mx_lookup=lambda domain: True,
    ).run()

    assert result.lead_count == 1 and result.contact_count == 1
    with Path(result.paths["raw_leads"]).open(newline="", encoding="utf-8") as file:
        lead = next(csv.DictReader(file))
    assert lead["score"] == "88"
    assert lead["why_line"].startswith(
        "Hi [first name], I wanted to reach out after seeing"
    )
    assert len(lead["supporting_candidate_ids"].split(",")) == 2
    with Path(result.paths["contacts"]).open(newline="", encoding="utf-8") as file:
        contact = next(csv.DictReader(file))
    assert contact["why_line"].startswith(
        "Hi Jane, I wanted to reach out after seeing"
    )
    manifest = json.loads(Path(result.manifest_path).read_text())
    assert manifest["status"] == "completed"
    assert all(manifest["stages"][stage]["status"] == "completed" for stage in PipelineRunner.STAGES)
    assert not any(item["stage"] == "manifest" for item in manifest["artifacts"])
    assert manifest["counts"]["relevant_people"] == 1
    assert manifest["counts"]["selected_contacts"] == 1

    manifest["errors"] = [
        {"type": "ZeroLeadError", "message": "0 leads found - failing the run"}
    ]
    Path(result.manifest_path).write_text(json.dumps(manifest), encoding="utf-8")

    fetch_count, model_count = len(fetch_calls), len(model_calls)
    resumed = PipelineRunner(
        replace(options, resume=True),
        fetch=lambda url: (_ for _ in ()).throw(AssertionError("resume fetched network")),
        model_call=lambda model, prompt, tools: (_ for _ in ()).throw(
            AssertionError("resume called model")
        ),
        mx_lookup=lambda domain: True,
    ).run()
    assert resumed.lead_count == 1
    resumed_manifest = json.loads(Path(resumed.manifest_path).read_text())
    assert resumed_manifest["errors"] == []
    assert len(fetch_calls) == fetch_count and len(model_calls) == model_count

    with pytest.raises(ValueError, match="different or obsolete"):
        PipelineRunner(
            replace(options, resume=True, workers=7),
            fetch=fetch,
            model_call=model_call,
            mx_lookup=lambda domain: True,
        )

    handoff_path = Path(result.paths["sales_handoff"])
    handoff_path.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="artifact integrity mismatch"):
        PipelineRunner(
            replace(options, resume=True),
            fetch=lambda url: (_ for _ in ()).throw(
                AssertionError("integrity failure fetched network")
            ),
            model_call=lambda model, prompt, tools: (_ for _ in ()).throw(
                AssertionError("integrity failure called model")
            ),
            mx_lookup=lambda domain: True,
        ).run()
