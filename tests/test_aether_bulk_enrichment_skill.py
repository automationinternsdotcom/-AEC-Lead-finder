"""Repository-local explicit bulk enrichment skill behavior."""
from __future__ import annotations

import gzip
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = ROOT / "skills" / "aether-bulk-enrichment" / "scripts"
sys.path.insert(0, str(ROOT / "scout"))
sys.path.insert(0, str(SCRIPT_DIR))

from bulk_lib import (  # noqa: E402
    BulkOptions,
    BulkRunner,
    CompanyProfile,
    WhyVariant,
    _archive_url_in_scope,
    _offline_screen,
    _require_distinct_variants,
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
from v2.discovery import load_curated_sources  # noqa: E402
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


def test_resume_rejects_changed_source_snapshot(tmp_path):
    BulkRunner(
        options(tmp_path),
        fetch=lambda url: (_ for _ in ()).throw(AssertionError(url)),
        model_call=lambda *args: ("{}", {}),
    )
    resume_options = options(tmp_path, resume=True)
    resume_options.sources_csv.write_text(
        "Resource Name,URL\nChanged,https://changed.example/\n"
    )

    with pytest.raises(ValueError, match="sources_sha256"):
        BulkRunner(
            resume_options,
            fetch=lambda url: (_ for _ in ()).throw(AssertionError(url)),
            model_call=lambda *args: ("{}", {}),
        )


def test_regional_source_does_not_expand_into_national_sitemap(tmp_path):
    source_path = tmp_path / "sources.csv"
    source_path.write_text(
        "Resource Name,URL\nJLL Phoenix,https://www.jll.com/en-us/locations/west/phoenix\n"
    )
    runner_options = options(tmp_path, sources_csv=source_path)
    source_path.write_text(
        "Resource Name,URL\nJLL Phoenix,https://www.jll.com/en-us/locations/west/phoenix\n"
    )
    runner = BulkRunner(
        runner_options,
        fetch=lambda url: (_ for _ in ()).throw(AssertionError(url)),
        model_call=lambda *args: ("{}", {}),
    )
    source = runner.sources[0]

    assert not _archive_url_in_scope(
        source, "https://www.jll.com/en-us/insights/national-market-report"
    )
    assert _archive_url_in_scope(
        source, "https://www.jll.com/en-us/insights/phoenix-industrial-market"
    )


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


def test_archive_resume_reuses_persisted_candidate_without_refetching_article(tmp_path):
    article = "https://example.com/2026/07/25/major-commercial-project-opens"
    sitemap = f"""<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>{article}</loc><lastmod>2026-07-25</lastmod></url>
    </urlset>"""
    pages = {
        "https://example.com/robots.txt": "Sitemap: https://example.com/sitemap.xml",
        "https://example.com/sitemap.xml": sitemap,
    }

    def fetch(url):
        if url == article:
            raise AssertionError("persisted archive article was fetched again")
        if url not in pages:
            raise RuntimeError("not found")
        return response(url, pages[url])

    BulkRunner(
        options(tmp_path),
        fetch=fetch,
        model_call=lambda *args: ("{}", {}),
    )
    runner = BulkRunner(
        options(tmp_path, resume=True),
        fetch=fetch,
        model_call=lambda *args: ("{}", {}),
    )
    source = runner.sources[0]
    persisted = DiscoveryCandidate(
        candidate_id="persisted-archive-candidate",
        run_id="bulk-run",
        provider="archive",
        discovered_url=article,
        resolved_url=article,
        canonical_url=article,
        title="Major commercial project opens",
        source_id=source.source_id,
        source_name=source.name,
        source_domain=source.domain,
        published_at=datetime(2026, 7, 25, tzinfo=timezone.utc),
    )
    runner._persisted_archive_by_source = {source.source_id: [persisted]}

    coverage, candidates = runner._discover_source_archive(source)

    assert coverage.dated_candidates == 1
    assert [item.candidate_id for item in candidates] == [persisted.candidate_id]


def test_legacy_interrupted_run_reuses_saved_corpus_without_fetching(tmp_path):
    base_options = options(tmp_path)
    state = StateStore(base_options.output_dir / "state.sqlite")
    state.migrate()
    state.create_run(
        "bulk-run",
        "2026-08-28",
        "2026-07-24",
        configuration={
            "kind": "explicit_bulk_enrichment",
            "model": "grok-4.3",
            "archive_until": "2026-08-28",
            "seed_db": "",
            "seed_run_id": "",
            "apollo": False,
            "email_delivery": False,
            "search_fallback": False,
        },
    )
    source = load_curated_sources(base_options.sources_csv)[0]
    state.upsert_source(
        source.source_id, source.name, source.url, source.domain
    )
    raw_path = tmp_path / "saved.html"
    raw_path.write_text(
        "<title>Phoenix warehouse opens</title><p>A Phoenix warehouse opened.</p>"
    )
    article = "https://example.com/2026/08/01/phoenix-warehouse-opens"
    state.save_candidate(
        DiscoveryCandidate(
            candidate_id="saved-candidate",
            run_id="bulk-run",
            provider="archive",
            discovered_url=article,
            resolved_url=article,
            canonical_url=article,
            title="Phoenix warehouse opens",
            source_id=source.source_id,
            source_name=source.name,
            source_domain=source.domain,
            published_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
            raw_artifact_path=str(raw_path),
        )
    )
    runner = BulkRunner(
        options(tmp_path, resume=True, reuse_discovery_corpus=True),
        fetch=lambda url: (_ for _ in ()).throw(
            AssertionError(f"saved-corpus recovery fetched {url}")
        ),
        model_call=lambda *args: ("{}", {}),
    )

    counters = runner._discover()

    assert counters["corpus_reused"] == 1
    assert counters["distinct_urls"] == 1


def test_why_line_validation_is_sourced_and_fail_closed():
    valid = _validate_variant(
        {
            "text": "Saw your team opened the new Phoenix marketplace on July 25, adding a customer-facing commercial property to Acme's local operating footprint after the announced development milestone.",
            "confidence": "high",
            "source_urls": ["https://example.com/story"],
        }
    )
    unsupported = _validate_variant(
        {
            "text": "Saw your team opened a new Phoenix marketplace and appears ready for a strategic service partner because this creates an immediate facilities opportunity for Acme.",
            "confidence": "high",
            "source_urls": [],
        }
    )

    assert valid.status == "valid"
    assert unsupported.status == "review" and unsupported.text == ""


def test_why_line_validation_rejects_analyst_copy_and_duplicate_variants():
    analyst = _validate_variant(
        {
            "text": "The company opened a Phoenix warehouse in August, creating an immediate need for facilities services and making this an ideal time for outreach to its operating team.",
            "confidence": "high",
            "source_urls": ["https://example.com/story"],
        }
    )
    valid_text = "Saw your team opened the new Phoenix warehouse on August 1, adding a customer-facing commercial property to Acme's local operating footprint after the announced development milestone."
    variants = _require_distinct_variants(
        {
            key: _validate_variant(
                {
                    "text": valid_text,
                    "confidence": "high",
                    "source_urls": ["https://example.com/story"],
                }
            )
            for key in ("a", "b", "c")
        }
    )

    assert analyst.status == "review" and analyst.text == ""
    assert "why_line_not_recipient_facing" in analyst.validation_errors
    assert "why_line_sales_inference" in analyst.validation_errors
    assert variants["a"].status == "valid"
    assert variants["b"].validation_errors == ["why_line_duplicate_variant"]
    assert variants["c"].validation_errors == ["why_line_duplicate_variant"]


def test_short_sourced_why_line_gets_deterministic_fact_free_completion():
    variant = _validate_variant(
        {
            "text": "Noticed Vestar owns the 1.2 million square foot Desert Ridge Marketplace regional entertainment lifestyle and power center in northeast Phoenix Arizona.",
            "confidence": "high",
            "source_urls": ["https://vestar.com/"],
        },
        key="b",
    )

    assert variant.status == "valid"
    assert 25 <= len(variant.text.split()) <= 45
    assert variant.confidence == "medium"
    assert variant.text.endswith("learning more about your work.")


def test_offline_screen_treats_missing_or_ambiguous_geography_conservatively(tmp_path):
    article_path = tmp_path / "article.html"
    article_path.write_text(
        "<title>Mesa Capital Partners</title>"
        "<p>Mesa Capital Partners announced construction of a new warehouse.</p>"
    )
    candidate = DiscoveryCandidate(
        candidate_id="candidate-screen",
        run_id="bulk-run",
        provider="archive",
        discovered_url="https://example.com/story",
        resolved_url="https://example.com/story",
        canonical_url="https://example.com/story",
        title="Mesa Capital Partners announces construction",
        source_id="source-1",
        source_name="Example",
        source_domain="example.com",
        published_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        raw_artifact_path=str(article_path),
    )

    assert _offline_screen(candidate) == "ambiguous"
    article_path.write_text(
        "<title>Texas warehouse</title><p>A new warehouse opened in Texas.</p>"
    )
    assert _offline_screen(candidate) == "reject"


def test_bulk_qualification_is_bounded_exact_and_person_free(tmp_path):
    calls = []

    def model_call(model, prompt, tools):
        rows = json.loads(prompt.split("Candidates:\n", 1)[1])
        calls.append((len(rows), tools))
        return json.dumps(
            {
                row["candidate_id"]: {
                    "qualified": False,
                    "filter_reason": "Not a specific Arizona commercial-property event",
                }
                for row in rows
            }
        ), {}

    runner = BulkRunner(
        options(tmp_path, batch_size=5),
        fetch=lambda url: (_ for _ in ()).throw(AssertionError(url)),
        model_call=model_call,
    )
    runner.state.upsert_source(
        "source-1", "Example", "https://example.com/", "example.com"
    )
    for index in range(13):
        url = f"https://example.com/2026/08/{index + 1:02d}/story"
        runner.state.save_candidate(
            DiscoveryCandidate(
                candidate_id=f"candidate-{index}",
                run_id="bulk-run",
                provider="archive",
                discovered_url=url,
                resolved_url=url,
                canonical_url=url,
                title=f"Story {index}",
                source_id="source-1",
                source_name="Example",
                source_domain="example.com",
                published_at=datetime(2026, 8, index + 1, tzinfo=timezone.utc),
                metadata={"selected_for_qualification": True},
            )
        )

    counters = runner._qualify()

    assert counters == {
        "submitted": 13,
        "batches": 3,
        "qualified": 0,
        "rejected": 13,
        "reviews": 0,
    }
    assert sorted(size for size, _ in calls) == [3, 5, 5]
    assert all(tools == [] for _, tools in calls)
    with runner.state.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM v2_people").fetchone()[0] == 0


def test_bulk_qualification_normalizes_timestamp_and_isolates_invalid_item(tmp_path):
    def model_call(model, prompt, tools):
        rows = json.loads(prompt.split("Candidates:\n", 1)[1])
        first, second = (row["candidate_id"] for row in rows)
        return json.dumps(
            {
                first: {
                    "qualified": True,
                    "business_name": "Acme Warehouse",
                    "event": "Opened a warehouse",
                    "date_posted": "2026-08-01T08:30:00+00:00",
                    "location": "Phoenix, Arizona",
                    "summary": "A warehouse opened.",
                    "state": "Arizona",
                    "priority": "high",
                    "property_type": "industrial",
                    "service_angle": "Support the operating warehouse.",
                    "confidence": "high",
                },
                second: {
                    "qualified": True,
                    "business_name": "Broken Result",
                    "event": "Opened a site",
                    "state": "Arizona",
                    "priority": "high",
                },
            }
        ), {}

    runner = BulkRunner(
        options(tmp_path, batch_size=2),
        fetch=lambda url: (_ for _ in ()).throw(AssertionError(url)),
        model_call=model_call,
    )
    runner.state.upsert_source(
        "source-1", "Example", "https://example.com/", "example.com"
    )
    for index in range(2):
        url = f"https://example.com/story-{index}"
        runner.state.save_candidate(
            DiscoveryCandidate(
                candidate_id=f"candidate-isolated-{index}",
                run_id="bulk-run",
                provider="archive",
                discovered_url=url,
                resolved_url=url,
                canonical_url=url,
                title=f"Phoenix warehouse {index}",
                source_id="source-1",
                source_name="Example",
                source_domain="example.com",
                published_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
                metadata={"selected_for_qualification": True},
            )
        )

    counters = runner._qualify()

    assert counters["qualified"] == 1
    assert counters["reviews"] == 1
    event = runner.state.events_for_run("bulk-run")[0]
    assert event.date_posted.isoformat() == "2026-08-01"
    statuses = {
        item.candidate_id: item.record_status.value
        for item in runner.state.candidates_for_run("bulk-run")
    }
    assert statuses == {
        "candidate-isolated-0": "valid",
        "candidate-isolated-1": "review",
    }


def test_recipient_why_refresh_is_one_call_per_business_and_resumable(tmp_path):
    initial = BulkRunner(
        options(tmp_path),
        fetch=lambda url: (_ for _ in ()).throw(AssertionError(url)),
        model_call=lambda *args: ("{}", {}),
    )
    initial.manifest.status = StageStatus.COMPLETED
    initial._refresh_manifest()
    runner = BulkRunner(
        options(tmp_path, resume=True),
        fetch=lambda url: (_ for _ in ()).throw(AssertionError(url)),
        model_call=lambda *args: ("{}", {}),
    )
    runner.state.upsert_source(
        "source-1", "Example", "https://example.com/", "example.com"
    )
    article = "https://example.com/anchor"
    runner.state.save_candidate(
        DiscoveryCandidate(
            candidate_id="candidate-anchor",
            run_id="bulk-run",
            provider="archive",
            discovered_url=article,
            resolved_url=article,
            canonical_url=article,
            title="Phoenix warehouse opens",
            source_id="source-1",
            source_name="Example",
            source_domain="example.com",
            published_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        )
    )
    runner.state.save_organization(
        Organization(organization_id="org-anchor", canonical_name="Anchor Co")
    )
    runner.state.save_lead_event(
        LeadEvent(
            lead_event_id="event-anchor",
            run_id="bulk-run",
            organization_id="org-anchor",
            primary_candidate_id="candidate-anchor",
            supporting_candidate_ids=["candidate-anchor"],
            event="Opened a warehouse",
            location="Phoenix, Arizona",
            priority="high",
            evidence=[Evidence(url=article, supports="Reports the opening")],
        )
    )
    profile = CompanyProfile(
        profile_key="profile-anchor",
        company_id="company-anchor",
        canonical_name="Anchor Co",
        domain="anchor.example",
        organization_ids=["org-anchor"],
        lead_event_ids=["event-anchor"],
        anchor_lead_event_id="event-anchor",
        variants={key: WhyVariant() for key in ("a", "b", "c")},
    )
    initial.artifacts.final_dir.mkdir(parents=True, exist_ok=True)
    (initial.artifacts.final_dir / "company_profiles.jsonl").write_text(
        profile.model_dump_json() + "\n",
        encoding="utf-8",
    )
    with (initial.artifacts.final_dir / "leads.csv").open(
        "w", newline="", encoding="utf-8"
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "lead_event_id", "company_id", "why_line_a", "why_line_b",
                "why_line_c", "why_sources_a", "why_sources_b", "why_sources_c",
                "record_status",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "lead_event_id": "event-anchor",
                "company_id": "company-anchor",
                "record_status": "valid",
            }
        )

    calls = []
    official = "https://anchor.example/operations"
    lines = {
        "a": "Saw your team opened the new Phoenix warehouse on August 1, adding a customer-facing commercial property to Anchor Co's local operating footprint after the announced development milestone.",
        "b": "Your official site highlights Anchor Co's Phoenix warehouse operation, including receiving areas, customer pickup space, and scheduled inventory handling within one managed commercial location serving regional clients.",
        "c": "Noticed Anchor Co paired its August warehouse opening with a regional inventory model, bringing receiving, storage, and customer pickup operations together at the new Phoenix commercial property.",
    }

    def model_call(model, prompt, tools):
        calls.append((model, tools, prompt))
        return json.dumps(
            {
                "variants": {
                    "a": {"text": lines["a"], "confidence": "high", "source_urls": [article]},
                    "b": {"text": lines["b"], "confidence": "high", "source_urls": [official]},
                    "c": {"text": lines["c"], "confidence": "high", "source_urls": [article, official]},
                }
            }
        ), {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}

    runner.model_call = model_call

    first = runner.refresh_why_lines()
    second = runner.refresh_why_lines()

    assert len(calls) == 1
    assert calls[0][0] == "grok-4.3"
    assert calls[0][1] == [{"type": "web_search"}]
    assert first["counts"]["companies"] == 1
    assert first["counts"]["model_calls"] == 1
    assert first["counts"]["valid_profiles"] == 1
    assert second["counts"]["model_calls"] == 1
    assert second["counts"]["new_model_calls"] == 0
    assert second["counts"]["cached_companies"] == 1
    revised = Path(first["output"])
    with (revised / "leads.csv").open(newline="", encoding="utf-8") as file:
        row = next(csv.DictReader(file))
    assert row["why_line_status"] == "valid"
    assert [row[f"why_line_{key}"] for key in ("a", "b", "c")] == [
        lines[key] for key in ("a", "b", "c")
    ]


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
    seed.save_lead_event(
        LeadEvent(
            lead_event_id="event-merged",
            run_id="seed-run",
            organization_id="org-1",
            primary_candidate_id="candidate-1",
            supporting_candidate_ids=["candidate-1"],
            event="Duplicate marketplace opening",
            location="Phoenix, Arizona",
            priority="high",
            evidence=[Evidence(url="https://example.com/story", supports="Duplicate")],
        )
    )
    seed.save_event_merge("seed-run", "event-merged", "event-1")
    runner = BulkRunner(
        options(
            tmp_path,
            archive_until="2026-08-27",
            seed_db=seed_path,
            seed_run_id="seed-run",
        ),
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

    why_a = "Saw your team opened the new Phoenix marketplace on July 25, adding a customer-facing commercial property to Acme's local operating footprint after the announced development milestone."
    why_b = "Your official site highlights Acme's Phoenix marketplace format, combining local vendors, shared customer areas, and recurring community programming within one managed commercial destination serving neighborhood shoppers."
    why_c = "Noticed Acme paired its July marketplace opening with a vendor-focused operating model, bringing local merchants and shared public areas together at the new Phoenix commercial property."

    def model_call(model, prompt, tools):
        if "bounded batch" in prompt:
            candidate_id = next(
                item.candidate_id
                for item in runner.state.candidates_for_run("bulk-run")
                if item.metadata.get("selected_for_qualification")
            )
            return json.dumps(
                {candidate_id: {
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
                    "confidence": "high"
                }}
            ), {}
        if "Score each Arizona" in prompt:
            assert tools == []
            assert '"contacts"' not in prompt
            event_id = runner.state.active_events_for_run("bulk-run")[0].lead_event_id
            return json.dumps({event_id: 88}), {}
        if "Write three alternative cold-email" in prompt:
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
    final_dir = tmp_path / "output/2026-08-28/runs/bulk-run/final"
    assert (final_dir / "leads.csv").exists()
    assert (final_dir / "companies.csv").exists()
    assert not (tmp_path / "output/leads.csv").exists()
    assert not list((tmp_path / "output").glob("*.html"))
    with runner.state.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM v2_people").fetchone()[0] == 0
