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

import bulk_lib  # noqa: E402
from bulk_lib import (  # noqa: E402
    BulkOptions,
    BulkRunner,
    CompanyProfile,
    WhyVariant,
    _archive_url_in_scope,
    _bulk_sales_handoff,
    _first_name,
    _likely_duplicate_event,
    _offline_screen,
    _personalize_why_line,
    _single_sendable_company_name,
    _template_catalog,
    _uses_sentence_case_only,
    _why_line_from_payload,
)
from v2.contracts import (  # noqa: E402
    ContactCandidate,
    DiscoveryCandidate,
    Evidence,
    LeadEvent,
    Organization,
    Person,
    RecordStatus,
    StageStatus,
    VerificationStatus,
)
from integration.handoff import handoff_content_hash  # noqa: E402
from integration.models import EligibilityStatus  # noqa: E402
from v2.http import FetchResponse  # noqa: E402
from v2.discovery import load_curated_sources  # noqa: E402
from v2.state import StateStore  # noqa: E402
from v2.verification import ContactVerifier as RealContactVerifier  # noqa: E402


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


def test_recipient_first_name_personalization_strips_honorifics():
    line = "Hi [first name] just wanted to reach out. Is there any chance?"

    assert _first_name("Dr. Michael Hudson") == "Michael"
    assert _first_name("Ana Garcia") == "Ana"
    assert _personalize_why_line(line, "Michael").startswith("Hi Michael ")
    assert "[first name]" not in _personalize_why_line(line, "Michael")
    assert _single_sendable_company_name("Gorman & Company")
    assert not _single_sendable_company_name("Costco and Sprouts")


def test_bulk_sales_handoff_ranks_and_gates_recipients():
    run_id = "bulk-handoff-run"
    candidate = DiscoveryCandidate(
        candidate_id="candidate-1",
        run_id=run_id,
        provider="archive",
        discovered_url="https://example.com/acme-opens",
        resolved_url="https://example.com/acme-opens",
        canonical_url="https://example.com/acme-opens",
        title="Acme opens Phoenix warehouse",
        source_id="source-1",
        source_name="Example",
        source_domain="example.com",
        published_at=datetime(2026, 1, 5, tzinfo=timezone.utc),
    )
    event = LeadEvent(
        lead_event_id="event-1",
        run_id=run_id,
        organization_id="legacy-org",
        primary_candidate_id=candidate.candidate_id,
        supporting_candidate_ids=[candidate.candidate_id],
        event="Acme opened a warehouse",
        location="Phoenix",
        date_posted=datetime(2026, 1, 5, tzinfo=timezone.utc).date(),
        summary="Acme opened a warehouse in Phoenix.",
        priority="high",
        confidence="high",
    )
    why = WhyVariant(
        text=(
            "Hi [first name], I wanted to reach out after seeing on the news that "
            "the new warehouse is opening in phoenix. Is there any chance we could "
            "stay in touch regarding your future janitorial needs?"
        ),
        template_key="opening",
        lead_event_id=event.lead_event_id,
        slots={"property": "the new warehouse", "location": "phoenix"},
        confidence="high",
        source_urls=[candidate.canonical_url],
        status="valid",
    )
    profile = CompanyProfile(
        profile_key="acme",
        company_id="company-1",
        canonical_name="Acme",
        organization_ids=["legacy-org"],
        lead_event_ids=[event.lead_event_id],
        anchor_lead_event_id=event.lead_event_id,
        variants={"primary": why},
        record_status="valid",
    )
    operator = Person(
        person_id="person-operator",
        organization_id=profile.company_id,
        name="Olivia Operator",
        title="Regional Operations Manager",
        scope="Arizona operations",
        evidence=[Evidence(url="https://acme.example/team", supports="role")],
    )
    owner = Person(
        person_id="person-owner",
        organization_id=profile.company_id,
        name="Owen Owner",
        title="Owner",
        evidence=[Evidence(url="https://acme.example/about", supports="role")],
    )
    weak = Person(
        person_id="person-weak",
        organization_id=profile.company_id,
        name="Wendy Weak",
        title="Analyst",
        evidence=[Evidence(url="https://acme.example/team", supports="role")],
    )

    def contact(person, email, reason="domain_mx_valid_mailbox_unverified"):
        return ContactCandidate(
            contact_candidate_id=f"contact-{person.person_id}",
            run_id=run_id,
            lead_event_id=event.lead_event_id,
            organization_id=profile.company_id,
            person_id=person.person_id,
            person_name=person.name,
            title=person.title,
            email=email,
            provider="model",
            verification_status=VerificationStatus.UNKNOWN,
            verification_reason=reason,
            selected=True,
            evidence=[Evidence(url="https://acme.example/team", supports="email")],
        )

    handoff = _bulk_sales_handoff(
        run_id=run_id,
        profiles=[profile],
        events=[event],
        candidates={candidate.candidate_id: candidate},
        scores={event.lead_event_id: 90},
        people=[operator, owner, weak],
        contacts=[
            contact(operator, "olivia@acme.example"),
            contact(owner, "owen@acme.example"),
            contact(weak, "wendy@acme.example", reason="mailbox_unverified"),
        ],
        open_review_ids=set(),
    )

    assert len(handoff.companies) == 1
    assert len(handoff.lead_events) == 1
    assert handoff.lead_events[0].crm_eligible
    assert len(handoff.recipients) == 2
    assert handoff.recipients[0].person_id == operator.person_id
    assert handoff.recipients[0].primary and handoff.recipients[0].rank == 1
    assert len(handoff.sequences) == 1
    assert handoff.sequences[0].eligibility_status == EligibilityStatus.READY
    assert handoff.content_hash == handoff_content_hash(handoff)


@pytest.mark.parametrize(
    ("current", "prior"),
    [
        ("1,200-unit development approval", "approved for 1200-unit apartment complex"),
        ("plans new industrial condominium project", "industrial condo development planned"),
        ("new TSMC expansion", "may build several new fabs"),
    ],
)
def test_cross_run_duplicate_signal_blocks_nearby_repeat_stories(current, prior):
    assert _likely_duplicate_event(
        {
            "event": current,
            "location": "Phoenix, Arizona",
            "date_posted": "2026-01-16",
            "article_url": "https://example.com/current",
        },
        {
            "event": prior,
            "location": "Phoenix",
            "date_posted": "2026-01-14",
            "article_url": "https://example.com/prior",
        },
        "tsmc",
    )


def test_cross_run_duplicate_signal_keeps_distant_unrelated_story():
    assert not _likely_duplicate_event(
        {
            "event": "Vineyard Towne Center completion and full leasing",
            "location": "Queen Creek",
            "date_posted": "2026-01-23",
            "article_url": "https://example.com/current",
        },
        {
            "event": "Mesa considers tax reimbursement for Legacy Park",
            "location": "Mesa",
            "date_posted": "2026-08-30",
            "article_url": "https://example.com/prior",
        },
        "vestar",
    )


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


def test_external_corpus_reuse_clones_only_hash_verified_window_candidates(tmp_path):
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    corpus_options = options(corpus_dir, run_id="corpus-run")
    corpus_runner = BulkRunner(
        corpus_options,
        fetch=lambda url: (_ for _ in ()).throw(AssertionError(url)),
        model_call=lambda *args: ("{}", {}),
    )
    source = corpus_runner.sources[0]
    corpus_runner.state.upsert_source(
        source.source_id, source.name, source.url, source.domain
    )
    corpus_runner.state.set_stage_status(
        "corpus-run", "discover", StageStatus.COMPLETED
    )
    corpus_runner.coverage = [
        bulk_lib.SourceCoverage(
            source_id=source.source_id,
            source_name=source.name,
            source_url=source.url,
        )
    ]
    corpus_runner._write_coverage()
    for candidate_id_value, published_at in (
        ("inside", datetime(2026, 8, 1, tzinfo=timezone.utc)),
        ("outside", datetime(2026, 7, 1, tzinfo=timezone.utc)),
    ):
        artifact = corpus_runner.artifacts.raw_dir / f"{candidate_id_value}.html"
        artifact.write_text(
            f'<title>{candidate_id_value} Acme Warehouse</title>'
            f'<link rel="canonical" href="https://example.com/{candidate_id_value}">'
            f'<meta property="article:published_time" content="{published_at.isoformat()}">',
            encoding="utf-8",
        )
        corpus_runner.state.save_candidate(
            DiscoveryCandidate(
                candidate_id=candidate_id_value,
                run_id="corpus-run",
                provider="archive",
                discovered_url=f"https://example.com/{candidate_id_value}",
                resolved_url=f"https://example.com/{candidate_id_value}",
                canonical_url=f"https://example.com/{candidate_id_value}",
                title=f"{candidate_id_value} Acme Warehouse",
                source_id=source.source_id,
                source_name=source.name,
                source_domain=source.domain,
                published_at=published_at,
                record_status=RecordStatus.REJECTED,
                raw_artifact_path=str(artifact),
                raw_artifact_hash=(
                    "0" * 64
                    if candidate_id_value == "inside"
                    else bulk_lib.hashlib.sha256(artifact.read_bytes()).hexdigest()
                ),
                metadata={"bulk_qualified": True},
            )
        )

    child_dir = tmp_path / "child"
    child_dir.mkdir()
    child = BulkRunner(
        options(
            child_dir,
            run_id="child-run",
            corpus_db=corpus_options.output_dir / "state.sqlite",
            corpus_run_id="corpus-run",
        ),
        fetch=lambda url: (_ for _ in ()).throw(AssertionError(url)),
        model_call=lambda *args: ("{}", {}),
    )

    counters = child._discover()

    assert counters["corpus_reused"] == 1
    imported = child.state.candidates_for_run("child-run")
    assert [item.candidate_id for item in imported] == ["inside"]
    assert imported[0].record_status == RecordStatus.VALID
    assert "bulk_qualified" not in imported[0].metadata
    assert imported[0].metadata["corpus_hash_revalidated"] is True
    assert Path(imported[0].raw_artifact_path).is_relative_to(child.artifacts.raw_dir)
    assert counters["distinct_urls"] == 1


def test_why_line_selection_renders_approved_template_and_fails_closed():
    valid = _why_line_from_payload(
        {
            "selection": {
                "template_key": "opening",
                "lead_event_id": "event-1",
                "slots": {
                    "property": "the new Phoenix marketplace",
                    "location": "Phoenix",
                },
                "confidence": "high",
                "source_urls": ["https://example.com/story"],
            }
        },
        allowed_event_ids={"event-1"},
    )
    unsupported = _why_line_from_payload(
        {
            "selection": {
                "template_key": "opening",
                "lead_event_id": "event-1",
                "slots": {
                    "property": "the new Phoenix marketplace",
                    "location": "Phoenix",
                },
                "confidence": "high",
                "source_urls": [],
            }
        },
        allowed_event_ids={"event-1"},
    )

    assert valid.status == "valid"
    assert valid.text == (
        "Hi [first name], I wanted to reach out after seeing on the news that "
        "the new phoenix marketplace is opening in phoenix. "
        "Is there any chance we could stay in touch regarding your future janitorial needs?"
    )
    assert unsupported.status == "review" and unsupported.text == ""
    assert "why_line_unsourced" in unsupported.validation_errors


@pytest.mark.parametrize(
    ("raw_location", "expected"),
    [
        ("Tempe, Arizona", "tempe"),
        ("Deer Valley, North Phoenix", "deer valley"),
        ("Tucson and Gilbert", "tucson"),
        ("Southeast Mesa near Ray and Sossaman Roads, AZ", "southeast mesa"),
        ("Buckeye Arizona", "buckeye"),
        ("Phoenix Deer Valley AZ", "deer valley"),
    ],
)
def test_why_line_location_is_reduced_to_one_leaf_locality(raw_location, expected):
    why_line = _why_line_from_payload(
        {
            "selection": {
                "template_key": "opening",
                "lead_event_id": "event-1",
                "slots": {"property": "the new marketplace", "location": raw_location},
                "confidence": "high",
                "source_urls": ["https://example.com/story"],
            }
        },
        allowed_event_ids={"event-1"},
    )

    assert why_line.status == "valid"
    assert why_line.slots["location"] == expected
    assert "," not in why_line.slots["location"]


@pytest.mark.parametrize("raw_location", ["Arizona", "Pinal County, Arizona", "West Valley Arizona"])
def test_why_line_location_rejects_broad_parent_geography(raw_location):
    why_line = _why_line_from_payload(
        {
            "selection": {
                "template_key": "opening",
                "lead_event_id": "event-1",
                "slots": {"property": "the new marketplace", "location": raw_location},
                "confidence": "high",
                "source_urls": ["https://example.com/story"],
            }
        },
        allowed_event_ids={"event-1"},
    )

    assert why_line.status == "review"
    assert why_line.text == ""
    assert "why_line_location" in why_line.validation_errors


def test_why_line_selection_routes_non_actionable_signals_and_rejects_bad_slots():
    routed = _why_line_from_payload(
        {
            "selection": {
                "template_key": "route_new_owner",
                "lead_event_id": "event-sale",
                "slots": {},
                "confidence": "high",
                "source_urls": ["https://example.com/sale"],
            }
        },
        allowed_event_ids={"event-sale"},
    )
    bad_slots = _why_line_from_payload(
        {
            "selection": {
                "template_key": "acquisition",
                "lead_event_id": "event-sale",
                "slots": {"company": "Buyer Co", "property": "the warehouse"},
                "confidence": "high",
                "source_urls": ["https://example.com/sale"],
            }
        },
        allowed_event_ids={"event-sale"},
    )
    overlong_company = _why_line_from_payload(
        {
            "selection": {
                "template_key": "acquisition",
                "lead_event_id": "event-sale",
                "slots": {
                    "company": "JLL Income Property Trust",
                    "property": "the warehouse",
                    "location": "Surprise",
                },
                "confidence": "high",
                "source_urls": ["https://example.com/sale"],
            }
        },
        allowed_event_ids={"event-sale"},
        known_company_names=["JLL Income Property Trust"],
    )
    shortened_company = _why_line_from_payload(
        {
            "selection": {
                "template_key": "acquisition",
                "lead_event_id": "event-sale",
                "slots": {
                    "company": "jll",
                    "property": "the warehouse",
                    "location": "Surprise",
                },
                "confidence": "high",
                "source_urls": ["https://example.com/sale"],
            }
        },
        allowed_event_ids={"event-sale"},
        known_company_names=["JLL Income Property Trust"],
    )
    unknown_company = _why_line_from_payload(
        {
            "selection": {
                "template_key": "acquisition",
                "lead_event_id": "event-sale",
                "slots": {
                    "company": "invented buyer",
                    "property": "the warehouse",
                    "location": "Surprise",
                },
                "confidence": "high",
                "source_urls": ["https://example.com/sale"],
            }
        },
        allowed_event_ids={"event-sale"},
        known_company_names=["JLL Income Property Trust"],
    )

    assert routed.status == "skip" and routed.text == ""
    assert bad_slots.status == "review" and bad_slots.text == ""
    assert "why_line_slots" in bad_slots.validation_errors
    assert overlong_company.status == "review"
    assert "why_line_reference_length" in overlong_company.validation_errors
    assert shortened_company.status == "valid"
    assert shortened_company.slots["company"] == "JLL"
    assert "JLL took ownership" in shortened_company.text
    assert unknown_company.status == "review"
    assert "why_line_company_reference" in unknown_company.validation_errors
    assert "route_new_owner" in _template_catalog()


def test_every_approved_template_renders_brief_copy_or_an_intentional_skip():
    samples = {
        "acquisition": {"company": "Acme", "property": "the Mesa warehouse", "location": "Mesa"},
        "opening": {"property": "the new Phoenix marketplace", "location": "Phoenix"},
        "planned_development": {"project": "west valley center", "location": "Goodyear"},
        "approval": {"project": "belmont energy center", "approval": "its rezoning approval"},
        "construction_start": {"project": "employee development campus", "location": "Phoenix"},
        "lease_relocation": {"company": "Acme", "property": "the Deer Valley warehouse", "location": "Phoenix"},
        "site_acquisition": {"company": "Acme", "site": "the Vistancia health club site", "location": "Peoria"},
        "expansion": {"company": "tsmc", "location": "Tucson"},
        "funded_facility": {"funding": "the newly awarded federal grant", "project_or_expansion": "arizona tradeport expansion"},
        "renovation_conversion": {"property": "the downtown office tower", "new_use": "a hotel"},
        "construction_progress": {"project": "tempe residential community", "milestone": "its topping-out milestone"},
        "completion": {"project": "Nexus Commerce Center", "location": "Phoenix"},
    }

    for key in (
        *samples,
        "route_new_owner",
        "skip_negative",
        "skip_general",
    ):
        why_line = _why_line_from_payload(
            {
                "selection": {
                    "template_key": key,
                    "lead_event_id": "event-1",
                    "slots": samples.get(key, {}),
                    "confidence": "high",
                    "source_urls": ["https://example.com/story"],
                }
            },
            allowed_event_ids={"event-1"},
            known_company_names=["Acme", "TSMC Arizona Corporation"],
        )
        if key in samples:
            assert why_line.status == "valid"
            assert why_line.text.endswith("?")
            assert why_line.text.startswith(
                "Hi [first name], I wanted to reach out after seeing"
            )
            assert _uses_sentence_case_only(
                why_line.text,
                company_references=[why_line.slots["company"]]
                if why_line.slots.get("company")
                else [],
            )
            assert 20 <= len(why_line.text.split()) <= 55
            if why_line.slots.get("location"):
                assert "," not in why_line.slots["location"]
                assert why_line.slots["location"].split()[-1] not in {"az", "arizona"}
            if key == "expansion":
                assert why_line.text.endswith(
                    "Is there any chance you'll be reviewing your janitorial needs, with the additional space?"
                )
            elif key in {
                "acquisition", "opening", "planned_development", "approval",
                "construction_start", "lease_relocation", "site_acquisition",
                "funded_facility", "construction_progress", "completion",
            }:
                assert why_line.text.endswith(
                    "Is there any chance we could stay in touch regarding your future janitorial needs?"
                )
            else:
                assert why_line.text.endswith(
                    "Is there any chance you'll be reviewing your janitorial needs?"
                )
        else:
            assert why_line.status == "skip"
            assert why_line.text == ""


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
                title=f"Acme Warehouse opens in Phoenix {index}",
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


def test_bulk_qualification_enforces_window_and_business_grounding(tmp_path):
    def model_call(model, prompt, tools):
        assert "2026-07-24 through 2026-08-28" in prompt
        rows = json.loads(prompt.split("Candidates:\n", 1)[1])
        return json.dumps(
            {
                rows[0]["candidate_id"]: {
                    "qualified": True,
                    "business_name": "Unrelated Hotels",
                    "event": "Opened a hotel",
                    "date_posted": "2026-08-01",
                    "location": "Phoenix",
                    "summary": "A hotel opened.",
                    "state": "Arizona",
                    "priority": "high",
                    "confidence": "high",
                },
                rows[1]["candidate_id"]: {
                    "qualified": True,
                    "business_name": "Acme Warehouse",
                    "event": "Opened a warehouse",
                    "date_posted": "2026-09-01",
                    "location": "Phoenix",
                    "summary": "A warehouse opened.",
                    "state": "Arizona",
                    "priority": "high",
                    "confidence": "high",
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
    rows = (
        ("ungrounded", "Hospital capacity update", datetime(2026, 8, 1, tzinfo=timezone.utc)),
        ("outside", "Acme Warehouse opens", datetime(2026, 9, 1, tzinfo=timezone.utc)),
    )
    for candidate_id_value, title, published_at in rows:
        url = f"https://example.com/{candidate_id_value}"
        runner.state.save_candidate(
            DiscoveryCandidate(
                candidate_id=candidate_id_value,
                run_id="bulk-run",
                provider="archive",
                discovered_url=url,
                resolved_url=url,
                canonical_url=url,
                title=title,
                source_id="source-1",
                source_name="Example",
                source_domain="example.com",
                published_at=published_at,
                metadata={"selected_for_qualification": True},
            )
        )

    counters = runner._qualify()

    assert counters == {
        "submitted": 2,
        "batches": 1,
        "qualified": 0,
        "rejected": 1,
        "reviews": 1,
    }
    assert not runner.state.events_for_run("bulk-run")


def test_qualification_audit_quarantines_persisted_out_of_window_event(tmp_path):
    runner = BulkRunner(
        options(tmp_path),
        fetch=lambda url: (_ for _ in ()).throw(AssertionError(url)),
        model_call=lambda *args: ("{}", {}),
    )
    runner.state.upsert_source(
        "source-1", "Example", "https://example.com/", "example.com"
    )
    candidate = DiscoveryCandidate(
        candidate_id="candidate-old",
        run_id="bulk-run",
        provider="archive",
        discovered_url="https://example.com/acme",
        resolved_url="https://example.com/acme",
        canonical_url="https://example.com/acme",
        title="Acme Warehouse opens",
        source_id="source-1",
        source_name="Example",
        source_domain="example.com",
        published_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )
    runner.state.save_candidate(candidate)
    runner.state.save_organization(
        Organization(organization_id="org-acme", canonical_name="Acme Warehouse")
    )
    runner.state.save_lead_event(
        LeadEvent(
            lead_event_id="event-old",
            run_id="bulk-run",
            organization_id="org-acme",
            primary_candidate_id=candidate.candidate_id,
            supporting_candidate_ids=[candidate.candidate_id],
            event="Opened a warehouse",
            location="Phoenix",
            date_posted=datetime(2026, 7, 1).date(),
            priority="high",
            evidence=[Evidence(url=candidate.canonical_url, supports="Opening")],
        )
    )

    counters = runner._qualification_audit()

    assert counters == {"submitted": 1, "valid": 0, "reviews": 1}
    assert not runner.state.active_events_for_run("bulk-run")


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
        variants={"a": WhyVariant()},
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
    line = (
        "Hi [first name], I wanted to reach out after seeing on the news that "
        "the new warehouse is opening in phoenix. "
        "Is there any chance we could stay in touch regarding your future janitorial needs?"
    )

    def model_call(model, prompt, tools):
        calls.append((model, tools, prompt))
        return json.dumps(
            {
                "selection": {
                    "template_key": "opening",
                    "lead_event_id": "event-anchor",
                    "slots": {"property": "the new warehouse", "location": "Phoenix"},
                    "confidence": "high",
                    "source_urls": [article],
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
    assert first["counts"]["valid_lines"] == 1
    assert second["counts"]["model_calls"] == 1
    assert second["counts"]["new_model_calls"] == 0
    assert second["counts"]["cached_companies"] == 1
    revised = Path(first["output"])
    with (revised / "leads.csv").open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        row = next(reader)
    assert row["why_line_status"] == "valid"
    assert row["why_template_key"] == "opening"
    assert row["why_line"] == line
    assert "why_line_a" not in reader.fieldnames


def test_recipient_why_refresh_reuses_base_company_response(tmp_path):
    initial = BulkRunner(
        options(tmp_path),
        fetch=lambda url: (_ for _ in ()).throw(AssertionError(url)),
        model_call=lambda *args: (_ for _ in ()).throw(
            AssertionError("base company response should prevent another model call")
        ),
    )
    initial.manifest.status = StageStatus.COMPLETED
    initial._refresh_manifest()
    article = "https://example.com/anchor"
    why = WhyVariant(
        text=(
            "Hi [first name], I wanted to reach out after seeing on the news that "
            "the new warehouse is opening in phoenix. Is there any chance we could "
            "stay in touch regarding your future janitorial needs?"
        ),
        template_key="opening",
        lead_event_id="event-anchor",
        slots={"property": "the new warehouse", "location": "phoenix"},
        confidence="high",
        source_urls=[article],
        status="valid",
    )
    response_path = initial.artifacts.raw_dir / "base-response.txt"
    response_path.write_text(
        json.dumps(
            {
                "selection": {
                    "template_key": "opening",
                    "lead_event_id": "event-anchor",
                    "slots": {
                        "property": "the new warehouse",
                        "location": "Phoenix",
                    },
                    "confidence": "high",
                    "source_urls": [article],
                }
            }
        ),
        encoding="utf-8",
    )
    profile = CompanyProfile(
        profile_key="profile-anchor",
        company_id="company-anchor",
        canonical_name="Anchor Co",
        organization_ids=["org-anchor"],
        lead_event_ids=["event-anchor"],
        anchor_lead_event_id="event-anchor",
        variants={"primary": why},
        record_status="valid",
    )
    initial.artifacts.final_dir.mkdir(parents=True, exist_ok=True)
    (initial.artifacts.final_dir / "company_profiles.jsonl").write_text(
        profile.model_dump_json() + "\n", encoding="utf-8"
    )
    with (initial.artifacts.final_dir / "leads.csv").open(
        "w", newline="", encoding="utf-8"
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=["lead_event_id", "company_id", "record_status"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "lead_event_id": "event-anchor",
                "company_id": "company-anchor",
                "record_status": "valid",
            }
        )
    base_dir = initial.artifacts.raw_dir / "company-profiles"
    base_dir.mkdir(parents=True, exist_ok=True)
    base_profile = profile.model_copy(
        update={"company_id": "", "raw_artifact_path": str(response_path)}
    )
    (base_dir / "profile-anchor.json").write_text(
        base_profile.model_dump_json(), encoding="utf-8"
    )

    runner = BulkRunner(
        options(tmp_path, resume=True),
        fetch=lambda url: (_ for _ in ()).throw(AssertionError(url)),
        model_call=lambda *args: (_ for _ in ()).throw(
            AssertionError("base company response should prevent another model call")
        ),
    )
    result = runner.refresh_why_lines()

    assert result["counts"]["new_model_calls"] == 0
    assert result["counts"]["migrated_companies"] == 1
    assert result["counts"]["valid_lines"] == 1
    assert result["counts"]["model_calls"] == 0


def test_recipient_enrichment_adds_real_names_and_resumes_without_repeat_calls(
    tmp_path, monkeypatch
):
    initial = BulkRunner(
        options(tmp_path),
        fetch=lambda url: (_ for _ in ()).throw(AssertionError(url)),
        model_call=lambda *args: ("{}", {}),
    )
    initial.state.upsert_source(
        "source-1", "Example", "https://example.com/", "example.com"
    )
    article = "https://example.com/anchor"
    initial.state.save_candidate(
        DiscoveryCandidate(
            candidate_id="candidate-anchor",
            run_id="bulk-run",
            provider="archive",
            discovered_url=article,
            resolved_url=article,
            canonical_url=article,
            title="Acme opens in Phoenix",
            source_id="source-1",
            source_name="Example",
            source_domain="example.com",
            published_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        )
    )
    initial.state.save_organization(
        Organization(organization_id="org-anchor", canonical_name="Acme")
    )
    initial.state.save_lead_event(
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
    line = (
        "Hi [first name], I wanted to reach out after seeing on the news that "
        "the warehouse is opening in phoenix. Is there any chance we could stay "
        "in touch regarding your future janitorial needs?"
    )
    profile = CompanyProfile(
        profile_key="profile-anchor",
        company_id="company-anchor",
        canonical_name="Acme",
        domain="acme.com",
        organization_ids=["org-anchor"],
        lead_event_ids=["event-anchor"],
        anchor_lead_event_id="event-anchor",
        evidence_urls=[article],
        variants={
            "primary": WhyVariant(
                text=line,
                template_key="opening",
                lead_event_id="event-anchor",
                slots={"property": "the warehouse", "location": "phoenix"},
                confidence="high",
                source_urls=[article],
                status="valid",
            )
        },
    )
    revision = initial.artifacts.final_dir / "recipient-outreach-v4"
    revision.mkdir(parents=True)
    (revision / "company_profiles.jsonl").write_text(
        profile.model_dump_json() + "\n", encoding="utf-8"
    )
    initial.manifest.status = StageStatus.COMPLETED
    initial._refresh_manifest()

    calls = []

    def model_call(model, prompt, tools):
        calls.append(prompt)
        if "up to three current decision makers" in prompt:
            return json.dumps(
                {
                    "decision_makers": [
                        {
                            "name": "Dr. Michael Hudson",
                            "title": "Facilities Director",
                            "scope": "Phoenix",
                        }
                    ],
                    "employee_count": None,
                    "sources": [
                        {"url": "https://acme.com/team", "supports": "Lists Michael."}
                    ],
                }
            ), {"total_tokens": 40}
        if "Use web search to research this exact person" in prompt:
            return json.dumps(
                {
                    "name": "Dr. Michael Hudson",
                    "organization": "Acme",
                    "email": "michael@acme.com",
                    "phone": "",
                    "linkedin": "https://linkedin.com/in/michael-hudson",
                    "sources": [
                        {"url": "https://acme.com/team", "supports": "Lists contact."}
                    ],
                }
            ), {"total_tokens": 30}
        raise AssertionError(prompt[:100])

    monkeypatch.setattr(
        bulk_lib,
        "ContactVerifier",
        lambda state: RealContactVerifier(state, mx_lookup=lambda domain: True),
    )
    runner = BulkRunner(
        options(tmp_path, resume=True),
        fetch=lambda url: (_ for _ in ()).throw(AssertionError(url)),
        model_call=model_call,
    )

    first = runner.enrich_recipients(apollo_go=False)
    second = runner.enrich_recipients(apollo_go=False)

    assert len(calls) == 2
    assert first == second
    assert first["counts"]["people"] == 1
    assert first["counts"]["recipients_with_email"] == 1
    assert first["counts"]["apollo_new_requests"] == 0
    assert first["email_delivery"] is False
    with (Path(first["output"]) / "recipients.csv").open(
        newline="", encoding="utf-8"
    ) as file:
        row = next(csv.DictReader(file))
    assert row["first_name"] == "Michael"
    assert row["full_name"] == "Dr. Michael Hudson"
    assert row["why_line"].startswith(
        "Hi Michael, I wanted to reach out after seeing"
    )
    assert "[first name]" not in row["why_line"]


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

    why_line = (
        "Hi [first name], I wanted to reach out after seeing on the news that "
        "the new commercial marketplace is opening in phoenix. "
        "Is there any chance we could stay in touch regarding your future janitorial needs?"
    )

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
        if "Select one approved Aether cold-email" in prompt:
            return json.dumps(
                {
                    "canonical_name": "Acme Marketplace",
                    "domain": "acme.example",
                    "employee_count": "100-250",
                    "selection": {
                        "template_key": "opening",
                        "lead_event_id": runner.state.active_events_for_run("bulk-run")[0].lead_event_id,
                        "slots": {
                            "property": "the new commercial marketplace",
                            "location": "Phoenix",
                        },
                        "confidence": "high",
                        "source_urls": [article],
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
    with (final_dir / "leads.csv").open(newline="", encoding="utf-8") as file:
        row = next(csv.DictReader(file))
    assert row["why_line"] == why_line
    assert row["why_template_key"] == "opening"
    with runner.state.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM v2_people").fetchone()[0] == 0
