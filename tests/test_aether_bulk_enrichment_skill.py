"""Repository-local explicit bulk enrichment skill behavior."""
from __future__ import annotations

import gzip
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = ROOT / "skills" / "aether-bulk-enrichment" / "scripts"
sys.path.insert(0, str(ROOT / "scout"))
sys.path.insert(0, str(SCRIPT_DIR))

from bulk_lib import (  # noqa: E402
    BulkOptions,
    BulkRunner,
    _validate_variant,
)
from v2.contracts import (  # noqa: E402
    DiscoveryCandidate,
    Evidence,
    LeadEvent,
    Organization,
    StageStatus,
)
from v2.http import FetchResponse  # noqa: E402
from v2.state import StateStore  # noqa: E402


def response(url: str, text: str | bytes) -> FetchResponse:
    content = text if isinstance(text, bytes) else text.encode()
    return FetchResponse(url=url, content=content)


def sources_file(tmp_path: Path) -> Path:
    path = tmp_path / "sources.csv"
    path.write_text("Resource Name,URL\nExample,https://example.com/\n")
    return path


def options(tmp_path: Path, **changes) -> BulkOptions:
    values = {
        "since": "2026-07-24",
        "until": "2026-08-28",
        "output_dir": tmp_path / "output",
        "sources_csv": sources_file(tmp_path),
        "workers": 2,
        "model": "grok-4.3",
        "run_id": "bulk-run",
        "search_fallback": False,
    }
    values.update(changes)
    return BulkOptions(**values)


def test_skill_is_explicit_only():
    metadata = (ROOT / "skills/aether-bulk-enrichment/agents/openai.yaml").read_text()
    assert "allow_implicit_invocation: false" in metadata


def test_archive_discovery_reads_gzip_sitemap_and_exact_dates(tmp_path):
    urlset = b"""<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://example.com/2026/07/25/major-commercial-project-opens</loc>
      <lastmod>2026-07-25</lastmod></url>
      <url><loc>https://example.com/2026/07/20/old-commercial-project-opens</loc>
      <lastmod>2026-07-20</lastmod></url>
    </urlset>"""
    sitemap_index = """<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <sitemap><loc>https://example.com/posts.xml.gz</loc></sitemap>
    </sitemapindex>"""
    pages = {
        "https://example.com/robots.txt": "Sitemap: https://example.com/sitemap.xml",
        "https://example.com/sitemap.xml": sitemap_index,
        "https://example.com/posts.xml.gz": gzip.compress(urlset),
        "https://example.com/2026/07/25/major-commercial-project-opens": (
            '<title>Major commercial project opens</title>'
            '<meta property="article:published_time" content="2026-07-25T08:00:00Z">'
        ),
    }

    def fetch(url):
        if url not in pages:
            raise RuntimeError("not found")
        return response(url, pages[url])

    runner = BulkRunner(options(tmp_path), fetch=fetch, model_call=lambda *args: ("{}", {}))
    source = runner.sources[0]
    runner.state.upsert_source(
        source.source_id, source.name, source.url, source.domain, source.state, source.enabled
    )
    coverage, candidates = runner._discover_source_archive(source)

    assert coverage.sitemap_documents == 2
    assert coverage.dated_candidates == 1
    assert candidates[0].published_at.date().isoformat() == "2026-07-25"


def test_why_line_validation_is_sourced_and_fail_closed():
    valid = _validate_variant(
        {
            "text": "Acme opened a new Phoenix marketplace, creating an immediate need for reliable facilities support that protects the asset as tenants and customers begin using the property.",
            "confidence": "high",
            "source_urls": ["https://example.com/story"],
        }
    )
    unsupported = _validate_variant(
        {
            "text": "Acme has a facilities opportunity that should be pursued immediately because the company operates many properties and appears ready for a new strategic service partner today.",
            "confidence": "high",
            "source_urls": [],
        }
    )

    assert valid.status == "valid"
    assert unsupported.status == "review" and unsupported.text == ""


def test_seed_import_clones_completed_run_into_bulk_state(tmp_path):
    seed_path = tmp_path / "seed.sqlite"
    seed = StateStore(seed_path)
    seed.migrate()
    manifest_path = tmp_path / "seed-manifest.json"
    manifest_path.write_text(json.dumps({"status": "completed", "artifacts": []}))
    seed.create_run("seed-run", "2026-08-28", "2026-08-28", manifest_path=str(manifest_path))
    seed.set_run_status("seed-run", StageStatus.COMPLETED)
    seed.upsert_source("source-1", "Example", "https://example.com/", "example.com")
    candidate = DiscoveryCandidate(
        candidate_id="candidate-1",
        run_id="seed-run",
        provider="curated",
        discovered_url="https://example.com/story",
        resolved_url="https://example.com/story",
        canonical_url="https://example.com/story",
        title="Story",
        source_id="source-1",
        source_name="Example",
        source_domain="example.com",
        published_at=datetime(2026, 8, 28, tzinfo=timezone.utc),
    )
    seed.save_candidate(candidate)
    organization = Organization(organization_id="org-1", canonical_name="Acme")
    seed.save_organization(organization)
    seed.save_lead_event(
        LeadEvent(
            lead_event_id="event-1",
            run_id="seed-run",
            organization_id="org-1",
            primary_candidate_id="candidate-1",
            supporting_candidate_ids=["candidate-1"],
            event="Opened a marketplace",
            location="Phoenix, Arizona",
            priority="high",
            evidence=[Evidence(url="https://example.com/story", supports="Reports opening")],
        )
    )
    runner = BulkRunner(
        options(tmp_path, seed_db=seed_path, seed_run_id="seed-run"),
        fetch=lambda url: (_ for _ in ()).throw(AssertionError(url)),
        model_call=lambda *args: ("{}", {}),
    )

    counters = runner._seed()

    assert counters["events"] == 1
    imported = runner.state.events_for_run("bulk-run")[0]
    assert imported.run_id == "bulk-run" and imported.lead_event_id == "event-1"


def test_bulk_runner_exports_leads_and_companies_without_delivery(tmp_path):
    article = "https://example.com/2026/07/25/new-commercial-marketplace-opens-phoenix"
    sitemap = f"""<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>{article}</loc><lastmod>2026-07-25</lastmod></url>
    </urlset>"""
    index = f'<a href="{article}">A new commercial marketplace opens in Phoenix</a>'
    page = (
        "<title>A new commercial marketplace opens in Phoenix</title>"
        '<meta property="article:published_time" content="2026-07-25T08:00:00Z">'
    )
    pages = {
        "https://example.com/": index,
        "https://example.com/robots.txt": "Sitemap: https://example.com/sitemap.xml",
        "https://example.com/sitemap.xml": sitemap,
        article: page,
    }

    def fetch(url):
        if url not in pages:
            raise RuntimeError("not found")
        return response(url, pages[url])

    why_a = "Acme opened a new Phoenix marketplace, creating an immediate need for reliable facilities support that protects the asset as tenants and customers begin using the property."
    why_b = "Acme operates a customer-facing commercial marketplace where consistent maintenance, presentation, and facility reliability directly support tenant satisfaction, asset value, and dependable daily business operations."
    why_c = "The new Phoenix marketplace expands Acme's operating footprint, making timely facilities support valuable for opening stability while establishing a scalable asset-preservation standard across future company locations."

    def model_call(model, prompt, tools):
        if "Candidate ID:" in prompt:
            return json.dumps(
                {
                    "qualified": True,
                    "business_name": "Acme Marketplace",
                    "person": "",
                    "event": "Opened a new commercial marketplace",
                    "date_posted": "2026-07-25",
                    "location": "Phoenix, Arizona",
                    "summary": "Acme opened a customer-facing marketplace.",
                    "state": "Arizona",
                    "priority": "high",
                    "property_type": "retail",
                    "service_angle": "Protect the newly operating asset.",
                    "filter_reason": "Specific opening",
                    "confidence": "high",
                }
            ), {}
        if "Score each Arizona" in prompt:
            event_id = runner.state.active_events_for_run("bulk-run")[0].lead_event_id
            return json.dumps({event_id: 88}), {}
        if "Resolve one Aether" in prompt:
            return json.dumps(
                {
                    "canonical_name": "Acme Marketplace",
                    "domain": "acme.example",
                    "employee_count": "100-250",
                    "variants": {
                        "a": {"text": why_a, "confidence": "high", "source_urls": [article]},
                        "b": {"text": why_b, "confidence": "high", "source_urls": [article]},
                        "c": {"text": why_c, "confidence": "high", "source_urls": [article]},
                    },
                }
            ), {}
        raise AssertionError(prompt[:100])

    runner = BulkRunner(options(tmp_path), fetch=fetch, model_call=model_call)
    result = runner.run()

    assert result["leads"] == 1 and result["companies"] == 1
    assert (tmp_path / "output/leads.csv").exists()
    assert (tmp_path / "output/companies.csv").exists()
    assert not list((tmp_path / "output").glob("*.html"))
