"""Implementation for the explicit-only Aether bulk enrichment skill."""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import urljoin, urlsplit

from pydantic import BaseModel, ConfigDict, Field

from v2.artifacts import ArtifactStore, new_manifest
from v2.contracts import (
    DiscoveryCandidate,
    Evidence,
    LeadEvent,
    Organization,
    RecordStatus,
    ReviewItem,
    StageStatus,
)
from v2.dedup import FuzzyEventDeduper, dedupe_candidates_exact
from v2.discovery import (
    CuratedSiteAdapter,
    CuratedSource,
    article_score,
    date_from_url,
    load_curated_sources,
    parse_datetime,
    publication_date,
    same_registrable_domain,
)
from v2.http import FetchResponse, HttpFetcher
from v2.ids import candidate_id, canonicalize_url, normalize_text, stable_hash, stable_uuid
from v2.qualification import QualificationService
from v2.scoring import ScoringService
from v2.state import SCHEMA_VERSION, StateStore


ModelCall = Callable[[str, str, list[dict]], tuple[str, dict]]
MAX_SITEMAP_DEPTH = 4
MAX_SITEMAP_DOCUMENTS = 100
MAX_URLS_PER_SOURCE = 10_000
CONVENTIONAL_SITEMAPS = (
    "/sitemap.xml",
    "/sitemap_index.xml",
    "/sitemap-index.xml",
    "/wp-sitemap.xml",
)
WHY_KEYS = ("a", "b", "c")


@dataclass(frozen=True, slots=True)
class BulkOptions:
    since: str
    until: str
    output_dir: Path
    sources_csv: Path
    workers: int
    model: str
    run_id: str
    archive_until: str = ""
    resume: bool = False
    seed_db: Path | None = None
    seed_run_id: str = ""
    search_fallback: bool = True


class SourceCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    source_name: str
    source_url: str
    sitemap_documents: int = 0
    sitemap_urls: int = 0
    pages_fetched: int = 0
    dated_candidates: int = 0
    undated_pages: int = 0
    fallback_used: bool = False
    fallback_candidates: int = 0
    incomplete: bool = False
    errors: list[str] = Field(default_factory=list)


class WhyVariant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = ""
    confidence: str = "low"
    source_urls: list[str] = Field(default_factory=list)
    status: str = "review"
    validation_errors: list[str] = Field(default_factory=list)


class CompanyProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_key: str
    company_id: str = ""
    canonical_name: str
    domain: str = ""
    aliases: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    employee_count: str = ""
    organization_ids: list[str] = Field(default_factory=list)
    lead_event_ids: list[str] = Field(default_factory=list)
    anchor_lead_event_id: str
    variants: dict[str, WhyVariant]
    evidence_urls: list[str] = Field(default_factory=list)
    record_status: str = "review"
    raw_artifact_path: str = ""


ARCHIVE_FALLBACK_PROMPT = """Search the web for articles published from {since} through {until} on this exact publication site that report Arizona commercial-property events relevant to facilities services.

Source ID: {source_id}
Publication: {source_name}
Site: {source_url}

Include specific openings, occupancy, leases, completed construction, redevelopment, management transitions, business expansions, multifamily lease-up, industrial activation, retail/hospitality openings, and property transactions. Exclude macro commentary and residential consumer stories.

Return strict JSON only as {{"source_id":"{source_id}","urls":["https://..."]}}. Return an empty urls list when none are found. Invent no URLs."""


COMPANY_PROMPT = """Resolve one Aether Facility Services prospect company from its sourced Arizona lead events. You may perform at most three web searches.

Profile key: {profile_key}
Known company names: {names}
Known locations: {locations}
Deterministic anchor event ID: {anchor_id}
Events: {events}

Return strict JSON only with keys canonical_name, domain, employee_count, and variants. variants must have keys a, b, c; each variant must contain text, confidence (high or low), and source_urls.

Each nonblank text must be exactly one specific sourced sentence of 25-45 words and contain no en dash or em dash.
A: explain why the anchor property event makes outreach timely.
B: explain why ongoing company operations fit facilities services.
C: blend the anchor event with operating context.
If a claim lacks a supporting URL, return a blank text and empty source_urls. Do not guess."""


REPAIR_PROMPT = """Rewrite each supplied sourced why line to exactly 25-45 words as one sentence, without en dashes or em dashes. Preserve its meaning and do not add claims. Return strict JSON only mapping every exact ID to one rewritten string; include every ID exactly once and invent no IDs.

Items: {items}"""


class BulkRunner:
    STAGES = ("discover", "qualify", "seed", "dedup", "score", "companies", "export")

    def __init__(
        self,
        options: BulkOptions,
        *,
        fetch: Callable[[str], FetchResponse] | None = None,
        model_call: ModelCall | None = None,
    ):
        self.options = options
        self.since = date.fromisoformat(options.since)
        self.until = date.fromisoformat(options.until)
        self.archive_until = date.fromisoformat(options.archive_until or options.until)
        if self.until < self.since:
            raise ValueError("--until must be on or after --since")
        if not self.since <= self.archive_until <= self.until:
            raise ValueError("--archive-until must be within --since and --until")
        if options.model != "grok-4.3":
            raise ValueError("bulk enrichment is pinned to grok-4.3")
        options.output_dir.mkdir(parents=True, exist_ok=True)
        self.state = StateStore(options.output_dir / "state.sqlite")
        self.state.migrate()
        self.fetch = fetch or HttpFetcher()
        self.model_call = model_call or _default_model_call
        self.sources = load_curated_sources(options.sources_csv)
        self.artifacts = ArtifactStore(
            options.output_dir, options.until, options.run_id, self.state
        )
        configuration = {
            "kind": "explicit_bulk_enrichment",
            "schema_version": SCHEMA_VERSION,
            "since": options.since,
            "until": options.until,
            "archive_until": self.archive_until.isoformat(),
            "workers": options.workers,
            "model": options.model,
            "search_fallback": options.search_fallback,
            "apollo": False,
            "email_delivery": False,
            "seed_db": str(options.seed_db or ""),
            "seed_run_id": options.seed_run_id,
        }
        self.state.create_run(
            options.run_id,
            options.until,
            options.since,
            configuration,
            str(self.artifacts.manifest_path),
        )
        if options.resume and self.artifacts.manifest_path.exists():
            self.manifest = self.artifacts.load_manifest()
            self.manifest.configuration = configuration
        else:
            self.manifest = new_manifest(
                options.run_id, options.until, options.since, configuration
            )
            self.artifacts.write_manifest(self.manifest)
        self.coverage: list[SourceCoverage] = []
        self.profiles: list[CompanyProfile] = []

    def run(self) -> dict:
        self.manifest.status = StageStatus.RUNNING
        self.state.set_run_status(self.options.run_id, StageStatus.RUNNING)
        self.artifacts.write_manifest(self.manifest)
        try:
            self._stage("discover", self._discover)
            self._stage("qualify", self._qualify)
            self._stage("seed", self._seed)
            self._stage("dedup", self._dedup)
            self._stage("score", self._score)
            self._stage("companies", self._companies)
            self._stage("export", self._export)
        except Exception as exc:
            self.manifest.status = StageStatus.FAILED
            self.manifest.errors.append(
                {"type": type(exc).__name__, "message": str(exc)}
            )
            self._refresh_manifest()
            raise
        self.manifest.status = StageStatus.COMPLETED
        self._refresh_manifest()
        return {
            "run_id": self.options.run_id,
            "manifest": str(self.artifacts.manifest_path),
            "leads": len(self.state.active_events_for_run(self.options.run_id)),
            "companies": len(self._load_profiles()),
            "output": str(self.options.output_dir),
        }

    def _stage(self, name: str, function: Callable[[], dict]) -> None:
        if self.options.resume and name in self.state.completed_stages(self.options.run_id):
            self._hydrate(name)
            return
        self.state.set_stage_status(self.options.run_id, name, StageStatus.RUNNING)
        self.manifest.stages[name] = {"status": StageStatus.RUNNING.value}
        self.artifacts.write_manifest(self.manifest)
        try:
            counters = function()
        except Exception as exc:
            error = {"type": type(exc).__name__, "message": str(exc)}
            self.state.set_stage_status(
                self.options.run_id, name, StageStatus.FAILED, error=error
            )
            self.manifest.stages[name] = {
                "status": StageStatus.FAILED.value,
                "error": error,
            }
            self._refresh_manifest()
            raise
        self.state.set_stage_status(
            self.options.run_id, name, StageStatus.COMPLETED, counters=counters
        )
        self.manifest.stages[name] = {
            "status": StageStatus.COMPLETED.value,
            "counters": counters,
        }
        self.manifest.counts.update(
            {f"{name}.{key}": int(value) for key, value in counters.items()}
        )
        self._refresh_manifest()

    def _discover(self) -> dict:
        for source in self.sources:
            self.state.upsert_source(
                source.source_id,
                source.name,
                source.url,
                source.domain,
                source.state,
                source.enabled,
            )
        current = CuratedSiteAdapter(
            self.sources,
            self.state,
            self.artifacts,
            fetch=self.fetch,
            workers=self.options.workers,
        ).discover(
            self.options.run_id,
            self.since,
            max_candidates=0,
            until=self.archive_until,
        )
        with ThreadPoolExecutor(max_workers=self.options.workers) as pool:
            archive_results = list(pool.map(self._discover_source_archive, self.sources))
        archive_candidates = [item for _, rows in archive_results for item in rows]
        self.coverage = [item for item, _ in archive_results]
        fallback_candidates: list[DiscoveryCandidate] = []
        if self.options.search_fallback:
            targets = [item for item in self.coverage if item.dated_candidates == 0 or item.incomplete]
            with ThreadPoolExecutor(max_workers=self.options.workers) as pool:
                fallback_results = list(pool.map(self._fallback_source, targets))
            coverage_by_id = {item.source_id: item for item in self.coverage}
            for coverage, rows in fallback_results:
                coverage_by_id[coverage.source_id] = coverage
                fallback_candidates.extend(rows)
            self.coverage = [coverage_by_id[source.source_id] for source in self.sources]
        all_candidates = [*current.candidates, *archive_candidates, *fallback_candidates]
        selected = dedupe_candidates_exact(all_candidates)
        for candidate in selected:
            self.state.save_candidate(
                candidate.model_copy(
                    update={
                        "metadata": {
                            **candidate.metadata,
                            "selected_for_qualification": True,
                        }
                    }
                )
            )
        for coverage in self.coverage:
            if not coverage.incomplete:
                continue
            self.state.add_review(
                ReviewItem(
                    review_id=stable_uuid(
                        "review", self.options.run_id, "archive-coverage", coverage.source_id
                    ),
                    run_id=self.options.run_id,
                    stage="discover",
                    record_type="source",
                    record_id=coverage.source_id,
                    reason_code="archive_coverage_incomplete",
                    validation_errors=coverage.errors or ["archive_coverage_incomplete"],
                )
            )
        self._write_coverage()
        return {
            "sources": len(self.sources),
            "current_candidates": len(current.candidates),
            "archive_candidates": len(archive_candidates),
            "fallback_candidates": len(fallback_candidates),
            "selected": len(selected),
            "incomplete_sources": sum(item.incomplete for item in self.coverage),
            "uncovered_sources": sum(item.dated_candidates == 0 for item in self.coverage),
        }

    def _discover_source_archive(
        self, source: CuratedSource
    ) -> tuple[SourceCoverage, list[DiscoveryCandidate]]:
        coverage = SourceCoverage(
            source_id=source.source_id,
            source_name=source.name,
            source_url=source.url,
        )
        roots = self._sitemap_roots(source, coverage)
        queue = [(url, 0) for url in roots]
        seen_docs: set[str] = set()
        entries: dict[str, tuple[str, datetime | None]] = {}
        while queue and len(seen_docs) < MAX_SITEMAP_DOCUMENTS:
            url, depth = queue.pop(0)
            if url in seen_docs or depth > MAX_SITEMAP_DEPTH:
                continue
            seen_docs.add(url)
            try:
                response = self.fetch(url)
                root = _parse_sitemap(response.content)
            except Exception as exc:
                coverage.errors.append(f"sitemap:{url}:{type(exc).__name__}")
                continue
            coverage.sitemap_documents += 1
            if _local(root.tag) == "sitemapindex":
                for node in root:
                    child_url = _child_text(node, "loc")
                    if not child_url:
                        continue
                    try:
                        child_url = canonicalize_url(child_url)
                    except ValueError:
                        continue
                    if same_registrable_domain(child_url, source.url):
                        queue.append((child_url, depth + 1))
                continue
            if _local(root.tag) != "urlset":
                coverage.errors.append(f"sitemap:{url}:unsupported_root")
                continue
            for node in root:
                if len(entries) >= MAX_URLS_PER_SOURCE:
                    coverage.incomplete = True
                    break
                raw_url = _child_text(node, "loc")
                if not raw_url:
                    continue
                try:
                    page_url = canonicalize_url(raw_url)
                except ValueError:
                    continue
                if not same_registrable_domain(page_url, source.url):
                    continue
                title = _descendant_text(node, "title")
                raw_date = _descendant_text(node, "publication_date") or _child_text(node, "lastmod")
                hint = parse_datetime(raw_date) if raw_date else None
                url_date = date_from_url(page_url)
                if hint and hint.date() < self.since:
                    continue
                if hint and hint.date() > self.archive_until and url_date != self.archive_until:
                    continue
                if url_date and not self.since <= url_date <= self.archive_until:
                    continue
                if article_score(page_url, title, source.url) < 2:
                    continue
                entries.setdefault(page_url, (title, hint))
        if queue:
            coverage.incomplete = True
            coverage.errors.append("sitemap_document_cap_reached")
        coverage.sitemap_urls = len(entries)
        candidates: list[DiscoveryCandidate] = []
        for page_url, (sitemap_title, _) in entries.items():
            try:
                page = self.fetch(page_url)
                coverage.pages_fetched += 1
                canonical = canonicalize_url(page.url)
                published = publication_date(page.text, canonical)
                if not published:
                    coverage.undated_pages += 1
                    continue
                if not self.since <= published.date() <= self.archive_until:
                    continue
                artifact = self.artifacts.write_raw_text(
                    "archive-discover",
                    f"article-{stable_hash(canonical)[:20]}.html",
                    page.text,
                )
                title = sitemap_title or _html_title(page.text)
                item = DiscoveryCandidate(
                    candidate_id=candidate_id("archive", "", canonical),
                    run_id=self.options.run_id,
                    provider="archive",
                    discovered_url=page_url,
                    resolved_url=canonical,
                    canonical_url=canonical,
                    title=title,
                    source_id=source.source_id,
                    source_name=source.name,
                    source_domain=source.domain,
                    published_at=published,
                    raw_artifact_path=artifact["path"],
                    raw_artifact_hash=artifact["sha256"],
                    metadata={"archive_method": "sitemap"},
                )
                self.state.save_candidate(item)
                candidates.append(item)
            except Exception as exc:
                coverage.errors.append(f"article:{page_url}:{type(exc).__name__}")
        coverage.dated_candidates = len(candidates)
        return coverage, candidates

    def _sitemap_roots(
        self, source: CuratedSource, coverage: SourceCoverage
    ) -> list[str]:
        base = f"{urlsplit(source.url).scheme}://{urlsplit(source.url).netloc}"
        roots: list[str] = []
        try:
            robots = self.fetch(f"{base}/robots.txt").text
            for line in robots.splitlines():
                if line.casefold().startswith("sitemap:"):
                    value = line.split(":", 1)[1].strip()
                    try:
                        url = canonicalize_url(value)
                    except ValueError:
                        continue
                    if same_registrable_domain(url, source.url):
                        roots.append(url)
        except Exception as exc:
            coverage.errors.append(f"robots:{type(exc).__name__}")
        for path in CONVENTIONAL_SITEMAPS:
            roots.append(canonicalize_url(urljoin(base, path)))
        return list(dict.fromkeys(roots))

    def _fallback_source(
        self, coverage: SourceCoverage
    ) -> tuple[SourceCoverage, list[DiscoveryCandidate]]:
        source = next(item for item in self.sources if item.source_id == coverage.source_id)
        prompt = ARCHIVE_FALLBACK_PROMPT.format(
            since=self.options.since,
            until=self.archive_until.isoformat(),
            source_id=source.source_id,
            source_name=source.name,
            source_url=source.url,
        )
        attempt_id = stable_uuid("attempt", self.options.run_id, "archive-fallback", source.source_id)
        request = self.artifacts.write_raw(
            "archive-fallback", f"{attempt_id}-request.json", {"model": self.options.model, "prompt": prompt}
        )
        started = datetime.now(timezone.utc).isoformat()
        rows: list[DiscoveryCandidate] = []
        try:
            text, usage = self.model_call(self.options.model, prompt, [{"type": "web_search"}])
            response = self.artifacts.write_raw_text(
                "archive-fallback", f"{attempt_id}-response.txt", text
            )
            payload = _parse_object(text)
            if str(payload.get("source_id") or "") != source.source_id:
                raise ValueError("fallback source_id mismatch")
            urls = payload.get("urls") or []
            if not isinstance(urls, list):
                raise ValueError("fallback urls must be a list")
            for raw_url in urls[:100]:
                try:
                    url = canonicalize_url(str(raw_url))
                    if not same_registrable_domain(url, source.url):
                        continue
                    page = self.fetch(url)
                    published = publication_date(page.text, page.url)
                    if not published or not self.since <= published.date() <= self.archive_until:
                        continue
                    canonical = canonicalize_url(page.url)
                    artifact = self.artifacts.write_raw_text(
                        "archive-fallback",
                        f"article-{stable_hash(canonical)[:20]}.html",
                        page.text,
                    )
                    candidate = DiscoveryCandidate(
                        candidate_id=candidate_id("archive-search", "", canonical),
                        run_id=self.options.run_id,
                        provider="archive-search",
                        discovered_url=url,
                        resolved_url=canonical,
                        canonical_url=canonical,
                        title=_html_title(page.text),
                        source_id=source.source_id,
                        source_name=source.name,
                        source_domain=source.domain,
                        published_at=published,
                        raw_artifact_path=artifact["path"],
                        raw_artifact_hash=artifact["sha256"],
                        metadata={"archive_method": "grok_search_fallback"},
                    )
                    self.state.save_candidate(candidate)
                    rows.append(candidate)
                except Exception:
                    continue
            self.state.record_provider_attempt(
                attempt_id=attempt_id,
                run_id=self.options.run_id,
                stage="archive-fallback",
                provider="model",
                target_type="source",
                target_id=source.source_id,
                status="completed",
                token_usage=usage,
                request_artifact_path=request["path"],
                response_artifact_path=response["path"],
                started_at=started,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
        except Exception as exc:
            coverage.errors.append(f"fallback:{type(exc).__name__}:{exc}")
            coverage.incomplete = True
            self.state.record_provider_attempt(
                attempt_id=attempt_id,
                run_id=self.options.run_id,
                stage="archive-fallback",
                provider="model",
                target_type="source",
                target_id=source.source_id,
                status="failed",
                request_artifact_path=request["path"],
                error={"type": type(exc).__name__, "message": str(exc)},
                started_at=started,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
        coverage.fallback_used = True
        coverage.fallback_candidates = len(rows)
        coverage.dated_candidates += len(rows)
        return coverage, rows

    def _qualify(self) -> dict:
        candidates = [
            item
            for item in self.state.candidates_for_run(self.options.run_id)
            if item.metadata.get("selected_for_qualification")
            and item.record_status == RecordStatus.VALID
        ]
        result = QualificationService(
            self.state,
            self.artifacts,
            self.options.model,
            call_model=self.model_call,
            workers=self.options.workers,
        ).qualify(candidates)
        return {
            "submitted": len(candidates),
            "qualified": len(result.events),
            "rejected": len(result.rejected_candidate_ids),
            "reviews": len(result.reviews),
        }

    def _seed(self) -> dict:
        if not self.options.seed_db:
            return {"events": 0, "organizations": 0, "candidates": 0}
        _verify_seed_manifest(self.options.seed_db, self.options.seed_run_id)
        seed = StateStore(self.options.seed_db)
        events = [
            item
            for item in seed.events_for_run(self.options.seed_run_id)
            if item.record_status == RecordStatus.VALID
        ]
        candidate_ids = {
            candidate_id_value
            for event in events
            for candidate_id_value in event.supporting_candidate_ids
        }
        candidates = seed.candidates_by_ids(candidate_ids)
        organization_ids = {item.organization_id for item in events}
        organizations = seed.organizations(organization_ids)
        source_ids = {item.source_id for item in candidates}
        with seed.connect() as conn:
            source_rows = list(
                conn.execute(
                    f"SELECT * FROM v2_sources WHERE source_id IN ({', '.join('?' for _ in source_ids)})",
                    sorted(source_ids),
                )
            ) if source_ids else []
        for row in source_rows:
            self.state.upsert_source(
                row["source_id"], row["name"], row["url"], row["domain"], row["state"], bool(row["enabled"])
            )
        for candidate in candidates:
            self.state.save_candidate(candidate.model_copy(update={"run_id": self.options.run_id}))
        for organization in organizations:
            self.state.save_organization(organization)
        for event in events:
            self.state.save_lead_event(event.model_copy(update={"run_id": self.options.run_id}))
        return {
            "events": len(events),
            "organizations": len(organizations),
            "candidates": len(candidates),
        }

    def _dedup(self) -> dict:
        events = self.state.active_events_for_run(self.options.run_id)
        kept, reviews = FuzzyEventDeduper(
            self.state,
            self.artifacts,
            self.options.model,
            call_model=self.model_call,
        ).dedupe(events)
        return {"submitted": len(events), "events": len(kept), "reviews": len(reviews)}

    def _score(self) -> dict:
        events = self.state.active_events_for_run(self.options.run_id)
        scores, reviews = ScoringService(
            self.state,
            self.artifacts,
            self.options.model,
            call_model=self.model_call,
        ).score(events, [])
        return {"submitted": len(events), "scored": len(scores), "reviews": len(reviews)}

    def _companies(self) -> dict:
        events = self.state.active_events_for_run(self.options.run_id)
        organizations = {
            item.organization_id: item
            for item in self.state.organizations({event.organization_id for event in events})
        }
        scores = {item.lead_event_id: item.score for item in self.state.scores_for_run(self.options.run_id)}
        groups: dict[str, list[LeadEvent]] = defaultdict(list)
        for event in events:
            org = organizations.get(event.organization_id)
            groups[normalize_text(org.canonical_name if org else event.organization_id)].append(event)
        raw_dir = self.options.output_dir / "company_raw"
        raw_dir.mkdir(parents=True, exist_ok=True)

        def enrich(group_item: tuple[str, list[LeadEvent]]) -> CompanyProfile:
            key, group_events = group_item
            profile_key = stable_uuid("bulk-company-profile", key)
            path = raw_dir / f"{profile_key}.json"
            if self.options.resume and path.exists():
                return CompanyProfile.model_validate_json(path.read_text(encoding="utf-8"))
            profile = self._enrich_company(profile_key, group_events, organizations, scores)
            path.write_text(profile.model_dump_json(indent=2), encoding="utf-8")
            return profile

        with ThreadPoolExecutor(max_workers=self.options.workers) as pool:
            profiles = list(pool.map(enrich, sorted(groups.items())))
        profiles = self._repair_profiles(profiles)
        profiles = _dedupe_profiles(profiles)
        for profile in profiles:
            for key, variant in profile.variants.items():
                if variant.status == "valid":
                    continue
                self.state.add_review(
                    ReviewItem(
                        review_id=stable_uuid(
                            "review", self.options.run_id, "company-why", profile.profile_key, key
                        ),
                        run_id=self.options.run_id,
                        stage="companies",
                        record_type="company_profile",
                        record_id=f"{profile.profile_key}:{key}",
                        reason_code="company_why_line_invalid",
                        validation_errors=variant.validation_errors or ["company_why_line_invalid"],
                        raw_artifact_path=profile.raw_artifact_path,
                    )
                )
        self.profiles = profiles
        self._write_profiles(profiles)
        return {
            "submitted": len(groups),
            "companies": len(profiles),
            "valid": sum(item.record_status == "valid" for item in profiles),
            "reviews": sum(item.record_status != "valid" for item in profiles),
        }

    def _enrich_company(
        self,
        profile_key: str,
        events: list[LeadEvent],
        organizations: dict[str, Organization],
        scores: dict[str, int],
    ) -> CompanyProfile:
        anchor = _anchor_event(events, scores)
        orgs = [organizations[item] for item in {event.organization_id for event in events} if item in organizations]
        names = list(dict.fromkeys(item.canonical_name for item in orgs))
        locations = list(dict.fromkeys([*(item.location for item in orgs), *(event.location for event in events)]))
        event_payload = [
            {
                "lead_event_id": event.lead_event_id,
                "event": event.event,
                "date": str(event.date_posted or ""),
                "location": event.location,
                "summary": event.summary,
                "priority": event.priority,
                "score": scores.get(event.lead_event_id),
                "sources": [item.url for item in event.evidence],
            }
            for event in events
        ]
        prompt = COMPANY_PROMPT.format(
            profile_key=profile_key,
            names=json.dumps(names),
            locations=json.dumps(locations),
            anchor_id=anchor.lead_event_id,
            events=json.dumps(event_payload, sort_keys=True),
        )
        attempt_id = stable_uuid("attempt", self.options.run_id, "companies", profile_key)
        request = self.artifacts.write_raw(
            "companies", f"{attempt_id}-request.json", {"model": self.options.model, "prompt": prompt}
        )
        started = datetime.now(timezone.utc).isoformat()
        try:
            text, usage = self.model_call(self.options.model, prompt, [{"type": "web_search"}])
            response = self.artifacts.write_raw_text(
                "companies", f"{attempt_id}-response.txt", text
            )
            payload = _parse_object(text)
            variants = {
                key: _validate_variant((payload.get("variants") or {}).get(key) or {})
                for key in WHY_KEYS
            }
            canonical_name = str(payload.get("canonical_name") or names[0]).strip()
            domain = _domain(str(payload.get("domain") or ""))
            profile = CompanyProfile(
                profile_key=profile_key,
                canonical_name=canonical_name,
                domain=domain,
                aliases=list(dict.fromkeys([*names, *(alias for org in orgs for alias in org.aliases)])),
                locations=[item for item in locations if item],
                employee_count=str(payload.get("employee_count") or ""),
                organization_ids=sorted({event.organization_id for event in events}),
                lead_event_ids=sorted(event.lead_event_id for event in events),
                anchor_lead_event_id=anchor.lead_event_id,
                variants=variants,
                evidence_urls=list(dict.fromkeys([item.url for event in events for item in event.evidence])),
                record_status="valid" if all(item.status == "valid" for item in variants.values()) else "review",
                raw_artifact_path=response["path"],
            )
            self.state.record_provider_attempt(
                attempt_id=attempt_id,
                run_id=self.options.run_id,
                stage="companies",
                provider="model",
                target_type="company_profile",
                target_id=profile_key,
                status="completed",
                token_usage=usage,
                request_artifact_path=request["path"],
                response_artifact_path=response["path"],
                started_at=started,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            return profile
        except Exception as exc:
            self.state.record_provider_attempt(
                attempt_id=attempt_id,
                run_id=self.options.run_id,
                stage="companies",
                provider="model",
                target_type="company_profile",
                target_id=profile_key,
                status="review",
                request_artifact_path=request["path"],
                error={"type": type(exc).__name__, "message": str(exc)},
                started_at=started,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            return CompanyProfile(
                profile_key=profile_key,
                canonical_name=names[0] if names else profile_key,
                aliases=names,
                locations=[item for item in locations if item],
                organization_ids=sorted({event.organization_id for event in events}),
                lead_event_ids=sorted(event.lead_event_id for event in events),
                anchor_lead_event_id=anchor.lead_event_id,
                variants={key: WhyVariant(validation_errors=[f"company_contract:{type(exc).__name__}"]) for key in WHY_KEYS},
                evidence_urls=list(dict.fromkeys([item.url for event in events for item in event.evidence])),
                record_status="review",
            )

    def _repair_profiles(self, profiles: list[CompanyProfile]) -> list[CompanyProfile]:
        repair_items = {}
        for profile in profiles:
            for key, variant in profile.variants.items():
                if variant.text and variant.source_urls and variant.status != "valid":
                    repair_items[f"{profile.profile_key}:{key}"] = variant.text
        if not repair_items:
            return profiles
        prompt = REPAIR_PROMPT.format(items=json.dumps(repair_items, sort_keys=True))
        attempt_id = stable_uuid("attempt", self.options.run_id, "company-why-repair")
        request = self.artifacts.write_raw(
            "companies", f"{attempt_id}-request.json", {"model": self.options.model, "prompt": prompt}
        )
        started = datetime.now(timezone.utc).isoformat()
        try:
            text, usage = self.model_call(self.options.model, prompt, [])
            response = self.artifacts.write_raw_text(
                "companies", f"{attempt_id}-response.txt", text
            )
            payload = _parse_object(text)
            if set(payload) != set(repair_items):
                raise ValueError("why repair IDs must match exactly")
            self.state.record_provider_attempt(
                attempt_id=attempt_id,
                run_id=self.options.run_id,
                stage="companies",
                provider="model",
                target_type="why_repair_batch",
                target_id=self.options.run_id,
                status="completed",
                token_usage=usage,
                request_artifact_path=request["path"],
                response_artifact_path=response["path"],
                started_at=started,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
        except Exception as exc:
            self.state.record_provider_attempt(
                attempt_id=attempt_id,
                run_id=self.options.run_id,
                stage="companies",
                provider="model",
                target_type="why_repair_batch",
                target_id=self.options.run_id,
                status="review",
                request_artifact_path=request["path"],
                error={"type": type(exc).__name__, "message": str(exc)},
                started_at=started,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            return profiles
        updated = []
        for profile in profiles:
            variants = dict(profile.variants)
            for key, variant in profile.variants.items():
                repair_id = f"{profile.profile_key}:{key}"
                if repair_id not in payload:
                    continue
                candidate = _validate_variant(
                    {
                        "text": str(payload[repair_id]),
                        "confidence": variant.confidence,
                        "source_urls": variant.source_urls,
                    }
                )
                variants[key] = candidate if candidate.status == "valid" else WhyVariant(
                    confidence=variant.confidence,
                    source_urls=variant.source_urls,
                    validation_errors=[*candidate.validation_errors, "repair_failed"],
                )
            updated.append(
                profile.model_copy(
                    update={
                        "variants": variants,
                        "record_status": "valid" if all(item.status == "valid" for item in variants.values()) else "review",
                    }
                )
            )
        return updated

    def _export(self) -> dict:
        events = self.state.active_events_for_run(self.options.run_id)
        organizations = {item.organization_id: item for item in self.state.organizations()}
        candidates = {
            item.candidate_id: item for item in self.state.candidates_for_run(self.options.run_id)
        }
        scores = {item.lead_event_id: item.score for item in self.state.scores_for_run(self.options.run_id)}
        profiles = self._load_profiles()
        profile_by_event = {
            event_id: profile for profile in profiles for event_id in profile.lead_event_ids
        }
        lead_fields = [
            "lead_event_id", "company_id", "business_name", "event", "date_posted",
            "location", "priority", "score", "property_type", "service_angle", "summary",
            "article_url", "why_line_a", "why_line_b", "why_line_c", "why_sources_a",
            "why_sources_b", "why_sources_c", "supporting_candidate_ids", "run_id",
            "record_status", "provenance_json",
        ]
        lead_rows = []
        for event in events:
            profile = profile_by_event.get(event.lead_event_id)
            org = organizations.get(event.organization_id)
            primary = candidates.get(event.primary_candidate_id)
            lead_rows.append(
                {
                    "lead_event_id": event.lead_event_id,
                    "company_id": profile.company_id if profile else "",
                    "business_name": profile.canonical_name if profile else (org.canonical_name if org else ""),
                    "event": event.event,
                    "date_posted": str(event.date_posted or ""),
                    "location": event.location,
                    "priority": event.priority,
                    "score": scores.get(event.lead_event_id, ""),
                    "property_type": event.property_type,
                    "service_angle": event.service_angle,
                    "summary": event.summary,
                    "article_url": primary.canonical_url if primary else (event.evidence[0].url if event.evidence else ""),
                    "why_line_a": _variant_text(profile, "a"),
                    "why_line_b": _variant_text(profile, "b"),
                    "why_line_c": _variant_text(profile, "c"),
                    "why_sources_a": _variant_sources(profile, "a"),
                    "why_sources_b": _variant_sources(profile, "b"),
                    "why_sources_c": _variant_sources(profile, "c"),
                    "supporting_candidate_ids": ",".join(event.supporting_candidate_ids),
                    "run_id": self.options.run_id,
                    "record_status": event.record_status.value,
                    "provenance_json": json.dumps([item.model_dump(mode="json") for item in event.evidence], default=str, sort_keys=True),
                }
            )
        lead_rows.sort(key=lambda item: (-int(item["score"] or -1), item["lead_event_id"]))
        company_fields = [
            "company_id", "business_name", "domain", "aliases", "locations", "employee_count",
            "lead_event_ids", "lead_event_count", "anchor_lead_event_id", "why_line_a",
            "why_confidence_a", "why_sources_a", "why_line_b", "why_confidence_b",
            "why_sources_b", "why_line_c", "why_confidence_c", "why_sources_c",
            "record_status", "run_id", "provenance_json",
        ]
        company_rows = [_company_row(item, self.options.run_id) for item in profiles]
        company_rows.sort(key=lambda item: (-int(item["lead_event_count"]), item["business_name"]))
        _write_csv(self.options.output_dir / "leads.csv", lead_fields, lead_rows)
        _write_csv(self.options.output_dir / "companies.csv", company_fields, company_rows)
        _write_jsonl(self.options.output_dir / "lead_events.jsonl", events)
        _write_jsonl(self.options.output_dir / "company_profiles.jsonl", profiles)
        _write_jsonl(
            self.options.output_dir / "reviews.jsonl",
            self.state.reviews_for_run(self.options.run_id),
        )
        for path, kind in (
            (self.options.output_dir / "leads.csv", "csv"),
            (self.options.output_dir / "companies.csv", "csv"),
            (self.options.output_dir / "lead_events.jsonl", "jsonl"),
            (self.options.output_dir / "company_profiles.jsonl", "jsonl"),
            (self.options.output_dir / "reviews.jsonl", "jsonl"),
            (self.options.output_dir / "coverage.csv", "csv"),
        ):
            self.artifacts.record_existing("export", kind, path)
        return {
            "leads": len(lead_rows),
            "companies": len(company_rows),
            "reviews": len(self.state.reviews_for_run(self.options.run_id)),
        }

    def _write_coverage(self) -> None:
        fields = list(SourceCoverage.model_fields)
        rows = []
        for item in self.coverage:
            value = item.model_dump(mode="json")
            value["errors"] = " | ".join(item.errors)
            rows.append(value)
        _write_csv(self.options.output_dir / "coverage.csv", fields, rows)
        (self.options.output_dir / "coverage.json").write_text(
            json.dumps([item.model_dump(mode="json") for item in self.coverage], indent=2),
            encoding="utf-8",
        )
        self.artifacts.record_existing("discover", "csv", self.options.output_dir / "coverage.csv")
        self.artifacts.record_existing("discover", "json", self.options.output_dir / "coverage.json")

    def _write_profiles(self, profiles: list[CompanyProfile]) -> None:
        _write_jsonl(self.options.output_dir / "company_profiles.jsonl", profiles)

    def _load_profiles(self) -> list[CompanyProfile]:
        path = self.options.output_dir / "company_profiles.jsonl"
        if not path.exists():
            return self.profiles
        return [
            CompanyProfile.model_validate_json(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def _hydrate(self, stage: str) -> None:
        if stage == "discover":
            path = self.options.output_dir / "coverage.json"
            if path.exists():
                self.coverage = [SourceCoverage.model_validate(item) for item in json.loads(path.read_text())]
        elif stage == "companies":
            self.profiles = self._load_profiles()

    def _refresh_manifest(self) -> None:
        self.manifest.usage = self.state.usage_summary(self.options.run_id)
        self.manifest.artifacts = self.state.artifacts_for_run(self.options.run_id)
        self.artifacts.write_manifest(self.manifest)


def _parse_sitemap(content: bytes) -> ET.Element:
    payload = gzip.decompress(content) if content[:2] == b"\x1f\x8b" else content
    return ET.fromstring(payload)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].casefold()


def _child_text(node: ET.Element, name: str) -> str:
    for child in node:
        if _local(child.tag) == name and child.text:
            return child.text.strip()
    return ""


def _descendant_text(node: ET.Element, name: str) -> str:
    for child in node.iter():
        if _local(child.tag) == name and child.text:
            return child.text.strip()
    return ""


def _html_title(html: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    return " ".join(re.sub(r"<[^>]+>", " ", match.group(1)).split()) if match else ""


def _parse_object(text: str) -> dict:
    cleaned = re.sub(r"<<ccr:[^>]+>>", "", str(text or ""))
    match = re.search(r"\{.*\}", cleaned, re.S)
    if not match:
        raise ValueError("model response did not contain a JSON object")
    payload = json.loads(match.group())
    if not isinstance(payload, dict):
        raise ValueError("model response must be an object")
    return payload


def _word_count(value: str) -> int:
    return len(re.findall(r"\b[\w’']+\b", value))


def _validate_variant(raw: dict) -> WhyVariant:
    text = " ".join(str(raw.get("text") or "").split())
    confidence = str(raw.get("confidence") or "low").casefold()
    if confidence not in {"high", "low"}:
        confidence = "low"
    sources = []
    for value in raw.get("source_urls") or []:
        try:
            sources.append(canonicalize_url(str(value)))
        except ValueError:
            continue
    sources = list(dict.fromkeys(sources))
    errors = []
    if not text:
        errors.append("why_line_missing")
    if not sources:
        errors.append("why_line_unsourced")
    if text and not 25 <= _word_count(text) <= 45:
        errors.append("why_line_word_count")
    if "—" in text or "–" in text:
        errors.append("why_line_dash")
    if errors:
        if "why_line_unsourced" in errors:
            text = ""
        return WhyVariant(
            text=text,
            confidence=confidence,
            source_urls=sources,
            status="review",
            validation_errors=errors,
        )
    return WhyVariant(
        text=text,
        confidence=confidence,
        source_urls=sources,
        status="valid",
    )


def _anchor_event(events: list[LeadEvent], scores: dict[str, int]) -> LeadEvent:
    priority = {"high": 2, "medium": 1, "low": 0}
    return sorted(
        events,
        key=lambda item: (
            -scores.get(item.lead_event_id, -1),
            -priority.get(item.priority, -1),
            -(item.date_posted.toordinal() if item.date_posted else 0),
            item.lead_event_id,
        ),
    )[0]


def _domain(value: str) -> str:
    raw = value.strip().casefold()
    if not raw:
        return ""
    try:
        return urlsplit(canonicalize_url(raw if "://" in raw else f"https://{raw}")).hostname or ""
    except ValueError:
        return ""


def _dedupe_profiles(profiles: list[CompanyProfile]) -> list[CompanyProfile]:
    groups: dict[str, list[CompanyProfile]] = defaultdict(list)
    for profile in profiles:
        groups[profile.domain or normalize_text(profile.canonical_name)].append(profile)
    output = []
    for identity, rows in groups.items():
        winner = sorted(
            rows,
            key=lambda item: (
                -sum(value.status == "valid" for value in item.variants.values()),
                -bool(item.employee_count),
                -sum(len(value.source_urls) for value in item.variants.values()),
                item.profile_key,
            ),
        )[0]
        merged = winner.model_copy(
            update={
                "company_id": stable_uuid("bulk-company", identity),
                "aliases": list(dict.fromkeys(alias for row in rows for alias in [row.canonical_name, *row.aliases])),
                "locations": list(dict.fromkeys(value for row in rows for value in row.locations)),
                "organization_ids": sorted({value for row in rows for value in row.organization_ids}),
                "lead_event_ids": sorted({value for row in rows for value in row.lead_event_ids}),
                "evidence_urls": list(dict.fromkeys(value for row in rows for value in row.evidence_urls)),
            }
        )
        output.append(merged)
    return sorted(output, key=lambda item: item.company_id)


def _verify_seed_manifest(db_path: Path, run_id: str) -> None:
    state = StateStore(db_path)
    with state.connect() as conn:
        row = conn.execute(
            "SELECT status, manifest_path FROM v2_runs WHERE run_id=?", (run_id,)
        ).fetchone()
    if not row or row["status"] != StageStatus.COMPLETED.value:
        raise ValueError("seed run is not completed")
    manifest_path = Path(row["manifest_path"])
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if payload.get("status") != StageStatus.COMPLETED.value:
        raise ValueError("seed manifest is not completed")
    for artifact in payload.get("artifacts") or []:
        path = Path(str(artifact.get("path") or ""))
        expected = str(artifact.get("sha256") or "")
        if path.resolve() == manifest_path.resolve():
            continue
        if not path.exists() or not expected:
            raise ValueError(f"seed artifact missing: {path}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f"seed artifact hash mismatch: {path}")


def _variant_text(profile: CompanyProfile | None, key: str) -> str:
    return profile.variants[key].text if profile and key in profile.variants else ""


def _variant_sources(profile: CompanyProfile | None, key: str) -> str:
    return " ".join(profile.variants[key].source_urls) if profile and key in profile.variants else ""


def _company_row(profile: CompanyProfile, run_id: str) -> dict:
    row = {
        "company_id": profile.company_id,
        "business_name": profile.canonical_name,
        "domain": profile.domain,
        "aliases": "; ".join(profile.aliases),
        "locations": "; ".join(profile.locations),
        "employee_count": profile.employee_count,
        "lead_event_ids": ",".join(profile.lead_event_ids),
        "lead_event_count": len(profile.lead_event_ids),
        "anchor_lead_event_id": profile.anchor_lead_event_id,
        "record_status": profile.record_status,
        "run_id": run_id,
        "provenance_json": json.dumps(
            {"organization_ids": profile.organization_ids, "evidence_urls": profile.evidence_urls},
            sort_keys=True,
        ),
    }
    for key in WHY_KEYS:
        variant = profile.variants[key]
        row[f"why_line_{key}"] = variant.text
        row[f"why_confidence_{key}"] = variant.confidence
        row[f"why_sources_{key}"] = " ".join(variant.source_urls)
    return row


def _write_csv(path: Path, fields: list[str], rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_jsonl(path: Path, rows: Iterable[BaseModel | dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            value = row.model_dump(mode="json") if isinstance(row, BaseModel) else row
            file.write(json.dumps(value, sort_keys=True, default=str) + "\n")


def _default_model_call(model: str, prompt: str, tools: list[dict]) -> tuple[str, dict]:
    import llm

    return llm.call(model, prompt, tools=tools, text_format="json_object", with_usage=True)
