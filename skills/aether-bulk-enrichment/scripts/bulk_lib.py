"""Implementation for the explicit-only Aether bulk enrichment skill."""
from __future__ import annotations

import csv
import gzip
import hashlib
import html
import json
import re
import threading
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
    LeadScore,
    Organization,
    RecordStatus,
    ReviewItem,
    StageStatus,
)
from v2.dedup import FUZZY_PROMPT, dedupe_candidates_exact, validate_fuzzy_groups
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
from v2.ids import (
    candidate_id,
    canonicalize_url,
    event_id,
    normalize_text,
    organization_id,
    stable_hash,
    stable_uuid,
)
from v2.qualification import JudgmentPayload
from v2.scoring import parse_scores
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
ARIZONA_TERMS = (
    "arizona", "phoenix", "tucson", "mesa", "scottsdale", "tempe",
    "chandler", "gilbert", "glendale", "peoria", "surprise", "goodyear",
    "avondale", "flagstaff", "prescott", "yuma", "buckeye", "queen creek",
    "maricopa", "pinal county", "maricopa county", "casa grande",
    "lake havasu city", "bullhead city", "apache junction", "oro valley",
    "sierra vista", "prescott valley", "fountain hills", "cottonwood",
    "sedona", "nogales", "kingman", "eloy", "coolidge", "marana",
    "sahuarita", "san tan valley",
)
NON_ARIZONA_STATE_TERMS = (
    "california", "colorado", "florida", "georgia", "illinois", "indiana",
    "maryland", "massachusetts", "michigan", "minnesota", "missouri",
    "nevada", "new jersey", "new mexico", "new york", "north carolina",
    "ohio", "oregon", "pennsylvania", "south carolina", "tennessee",
    "texas", "utah", "virginia", "washington", "wisconsin",
)
AEC_EVENT_TERMS = (
    "open", "lease", "occup", "construct", "develop", "redevelop",
    "acqui", "sold", "sale", "tenant", "facility", "warehouse",
    "industrial", "retail", "multifamily", "hotel", "restaurant", "office",
    "medical", "campus", "data center", "distribution", "manufactur",
    "groundbreak", "expan", "renovat", "property", "real estate",
)


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
    reuse_discovery_corpus: bool = False
    batch_size: int = 20


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


BULK_QUALIFICATION_PROMPT = """Qualify this bounded batch using only the supplied saved article evidence. Do not search the web and do not identify people. For every exact candidate_id, decide whether the article reports a specific Arizona commercial-property event that creates a facilities-services opportunity.

Return strict JSON only as one object mapping every exact candidate_id to an object with keys: qualified, business_name, event, date_posted, location, summary, state, priority, property_type, service_angle, filter_reason, confidence. Include every submitted ID exactly once and invent no IDs. A rejection requires a specific filter_reason. A qualification requires state Arizona, priority high or medium, and nonempty business_name, event, and location.

Candidates:
{candidates}"""


BULK_SCORE_PROMPT = """Score each Arizona commercial-property lead event from 0 to 100 for Aether Facility Services outreach priority. Consider event fit, commercial property fit, timing, geography, and facilities-service need. Do not consider contact availability.

Return strict JSON only as one object mapping every exact lead_event_id to one integer 0-100. Include every submitted ID exactly once and invent no IDs.

Events:
{events}"""


class BulkRunner:
    STAGES = (
        "discover", "screen", "qualify", "seed", "dedup", "score",
        "companies", "export",
    )

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
        self.sources_sha256 = hashlib.sha256(options.sources_csv.read_bytes()).hexdigest()
        self.artifacts = ArtifactStore(
            options.output_dir, options.until, options.run_id, self.state
        )
        configuration = {
            "kind": "explicit_bulk_enrichment",
            "workflow_version": 2,
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
            "sources_sha256": self.sources_sha256,
            "reuse_discovery_corpus": options.reuse_discovery_corpus,
            "batch_size": options.batch_size,
        }
        self._validate_resume(configuration)
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
        # Source discovery is parallel too, so keep total article traffic bounded
        # while still allowing a large final sitemap to use more than one worker.
        self._archive_fetch_slots = threading.BoundedSemaphore(
            max(1, options.workers * 2)
        )
        self._persisted_archive_by_source: dict[str, list[DiscoveryCandidate]] = {}
        self._persisted_archive_by_url: dict[str, DiscoveryCandidate] = {}

    def _validate_resume(self, configuration: dict) -> None:
        with self.state.connect() as conn:
            row = conn.execute(
                "SELECT stamp, since_date, configuration_json FROM v2_runs WHERE run_id=?",
                (self.options.run_id,),
            ).fetchone()
        if not row:
            if self.options.resume:
                raise ValueError("--resume run ID does not exist in this output database")
            return
        if not self.options.resume:
            raise ValueError("run ID already exists; use --resume")
        if row["stamp"] != self.options.until or row["since_date"] != self.options.since:
            raise ValueError("resume date range does not match the existing run")
        prior = json.loads(row["configuration_json"] or "{}")
        immutable = (
            "kind", "model", "archive_until", "seed_db", "seed_run_id",
            "apollo", "email_delivery", "search_fallback", "workflow_version",
            "reuse_discovery_corpus", "batch_size",
        )
        mismatched = [
            key for key in immutable
            if key in prior and prior.get(key) != configuration.get(key)
        ]
        if prior.get("sources_sha256") and prior["sources_sha256"] != self.sources_sha256:
            mismatched.append("sources_sha256")
        if mismatched:
            raise ValueError(
                "resume configuration mismatch: " + ", ".join(sorted(set(mismatched)))
            )

    def run(self) -> dict:
        self.manifest.status = StageStatus.RUNNING
        self.state.set_run_status(self.options.run_id, StageStatus.RUNNING)
        self.artifacts.write_manifest(self.manifest)
        try:
            self._stage("discover", self._discover)
            self._stage("screen", self._screen)
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
            "output": str(self.artifacts.final_dir),
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
        if self.options.reuse_discovery_corpus:
            persisted = [
                item
                for item in self.state.candidates_for_run(self.options.run_id)
                if item.record_status == RecordStatus.VALID
            ]
            if not persisted:
                raise ValueError("saved-corpus reuse requested but this run has no valid candidates")
            counts: dict[str, int] = defaultdict(int)
            for candidate in persisted:
                counts[candidate.source_id] += 1
            self.coverage = [
                SourceCoverage(
                    source_id=source.source_id,
                    source_name=source.name,
                    source_url=source.url,
                    dated_candidates=counts.get(source.source_id, 0),
                    incomplete=True,
                    errors=["reconstructed_from_interrupted_discovery_corpus"],
                )
                for source in self.sources
            ]
            self._write_coverage()
            return {
                "sources": len(self.sources),
                "corpus_reused": len(persisted),
                "distinct_urls": len({item.canonical_url for item in persisted}),
                "incomplete_sources": len(self.sources),
            }
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
        persisted_archive = [
            item
            for item in self.state.candidates_for_run(self.options.run_id)
            if item.provider in {"archive", "archive-search"}
            and item.record_status == RecordStatus.VALID
        ]
        persisted_by_source: dict[str, list[DiscoveryCandidate]] = defaultdict(list)
        for candidate in persisted_archive:
            persisted_by_source[candidate.source_id].append(candidate)
        self._persisted_archive_by_source = dict(persisted_by_source)
        self._persisted_archive_by_url = {
            item.canonical_url: item for item in persisted_archive
        }
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
                if not _archive_url_in_scope(source, page_url):
                    coverage.incomplete = True
                    if "regional_scope_requires_search_fallback" not in coverage.errors:
                        coverage.errors.append("regional_scope_requires_search_fallback")
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
        candidates = list(self._persisted_archive_by_source.get(source.source_id, []))
        existing_urls = set(self._persisted_archive_by_url)
        reused = [
            self._persisted_archive_by_url[url]
            for url in entries
            if url in self._persisted_archive_by_url
            and self._persisted_archive_by_url[url] not in candidates
        ]
        candidates.extend(reused)
        pending = [
            (page_url, sitemap_title)
            for page_url, (sitemap_title, _) in entries.items()
            if page_url not in existing_urls
        ]

        def fetch_page(item: tuple[str, str]) -> tuple[DiscoveryCandidate | None, str, bool]:
            page_url, sitemap_title = item
            try:
                with self._archive_fetch_slots:
                    page = self.fetch(page_url)
                canonical = canonicalize_url(page.url)
                published = publication_date(page.text, canonical)
                if not published:
                    return None, "", True
                if not self.since <= published.date() <= self.archive_until:
                    return None, "", False
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
                return item, "", False
            except Exception as exc:
                return None, f"article:{page_url}:{type(exc).__name__}", False

        # A source-level pool eliminates the serial long tail when one large
        # sitemap remains after the outer source pool has drained.
        with ThreadPoolExecutor(max_workers=max(1, self.options.workers)) as pool:
            page_results = list(pool.map(fetch_page, pending))
        coverage.pages_fetched += len(pending)
        for candidate, error, undated in page_results:
            if error:
                coverage.errors.append(error)
            if undated:
                coverage.undated_pages += 1
            if candidate:
                self.state.save_candidate(candidate)
                candidates.append(candidate)
        coverage.dated_candidates = len(candidates)
        return coverage, candidates

    def _screen(self) -> dict:
        candidates = [
            item
            for item in self.state.candidates_for_run(self.options.run_id)
            if item.record_status == RecordStatus.VALID
            and item.provider not in {"seed"}
        ]
        selected = dedupe_candidates_exact(candidates)
        kept = rejected = ambiguous = 0
        for candidate in selected:
            decision = _offline_screen(candidate)
            metadata = {
                **candidate.metadata,
                "bulk_screen": decision,
                "selected_for_qualification": decision != "reject",
            }
            status = candidate.record_status
            if decision == "reject":
                status = RecordStatus.REJECTED
                rejected += 1
            elif decision == "ambiguous":
                ambiguous += 1
                kept += 1
            else:
                kept += 1
            self.state.save_candidate(
                candidate.model_copy(update={"metadata": metadata, "record_status": status})
            )
        return {
            "submitted": len(candidates),
            "distinct_urls": len(selected),
            "selected": kept,
            "ambiguous": ambiguous,
            "rejected": rejected,
        }

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
            and not item.metadata.get("bulk_qualified")
        ]
        batches = list(_chunks(candidates, self.options.batch_size))
        with ThreadPoolExecutor(max_workers=self.options.workers) as pool:
            outcomes = list(pool.map(self._qualify_batch, batches))
        return {
            "submitted": len(candidates),
            "batches": len(batches),
            "qualified": sum(item["qualified"] for item in outcomes),
            "rejected": sum(item["rejected"] for item in outcomes),
            "reviews": sum(item["reviews"] for item in outcomes),
        }

    def _qualify_batch(self, candidates: list[DiscoveryCandidate]) -> dict[str, int]:
        batch_id = stable_hash(*(item.candidate_id for item in candidates))[:20]
        payload = [
            {
                "candidate_id": item.candidate_id,
                "url": item.canonical_url,
                "title": item.title,
                "published_at": str(item.published_at or ""),
                "saved_article_excerpt": _candidate_excerpt(item, 2_500),
            }
            for item in candidates
        ]
        prompt = BULK_QUALIFICATION_PROMPT.format(
            candidates=json.dumps(payload, sort_keys=True, ensure_ascii=False)
        )
        attempt_id = stable_uuid(
            "attempt", self.options.run_id, "bulk-qualify", batch_id
        )
        request = self.artifacts.write_raw(
            "qualify", f"{attempt_id}-request.json",
            {"model": self.options.model, "prompt": prompt},
        )
        started = datetime.now(timezone.utc).isoformat()
        response_path = ""
        try:
            text, usage = self.model_call(self.options.model, prompt, [])
            response = self.artifacts.write_raw_text(
                "qualify", f"{attempt_id}-response.txt", text
            )
            response_path = response["path"]
            raw = _parse_object(text)
            expected = {item.candidate_id for item in candidates}
            if set(raw) != expected:
                raise ValueError(
                    "qualification IDs must match exactly; "
                    f"missing={sorted(expected - set(raw))}, "
                    f"unknown={sorted(set(raw) - expected)}"
                )
            judgments = {
                key: JudgmentPayload.model_validate(value) for key, value in raw.items()
            }
        except Exception as exc:
            for candidate in candidates:
                self._quarantine_bulk_candidate(candidate, response_path, exc)
            self.state.record_provider_attempt(
                attempt_id=attempt_id,
                run_id=self.options.run_id,
                stage="qualify",
                provider="model",
                target_type="discovery_candidate_batch",
                target_id=batch_id,
                status="review",
                request_artifact_path=request["path"],
                response_artifact_path=response_path,
                error={"type": type(exc).__name__, "message": str(exc)},
                started_at=started,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            return {"qualified": 0, "rejected": 0, "reviews": len(candidates)}
        qualified = rejected = 0
        for candidate in candidates:
            judgment = judgments[candidate.candidate_id]
            metadata = {**candidate.metadata, "bulk_qualified": True}
            if not judgment.qualified:
                self.state.save_candidate(
                    candidate.model_copy(
                        update={"metadata": metadata, "record_status": RecordStatus.REJECTED}
                    )
                )
                rejected += 1
                continue
            self._save_bulk_event(candidate, judgment)
            self.state.save_candidate(candidate.model_copy(update={"metadata": metadata}))
            qualified += 1
        self.state.record_provider_attempt(
            attempt_id=attempt_id,
            run_id=self.options.run_id,
            stage="qualify",
            provider="model",
            target_type="discovery_candidate_batch",
            target_id=batch_id,
            status="completed",
            token_usage=usage,
            request_artifact_path=request["path"],
            response_artifact_path=response_path,
            started_at=started,
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
        return {"qualified": qualified, "rejected": rejected, "reviews": 0}

    def _save_bulk_event(
        self, candidate: DiscoveryCandidate, payload: JudgmentPayload
    ) -> None:
        org_id = organization_id(payload.business_name, "", payload.location)
        support_ids = candidate.metadata.get("exact_duplicate_candidate_ids") or [
            candidate.candidate_id
        ]
        support_candidates = {
            item.candidate_id: item for item in self.state.candidates_by_ids(support_ids)
        }
        evidence = [
            Evidence(
                url=support_candidates[item].canonical_url,
                supports="Saved source article for the qualified property event.",
                provider=support_candidates[item].provider,
            )
            for item in support_ids if item in support_candidates
        ] or [
            Evidence(
                url=candidate.canonical_url,
                supports="Saved source article for the qualified property event.",
                provider=candidate.provider,
            )
        ]
        self.state.save_organization(
            Organization(
                organization_id=org_id,
                canonical_name=payload.business_name.strip(),
                location=payload.location.strip(),
                evidence=evidence,
            )
        )
        lead_id = event_id(
            org_id, payload.event, payload.location,
            payload.date_posted or (
                candidate.published_at.date() if candidate.published_at else ""
            ),
        )
        self.state.save_lead_event(
            LeadEvent(
                lead_event_id=lead_id,
                run_id=self.options.run_id,
                organization_id=org_id,
                primary_candidate_id=candidate.candidate_id,
                supporting_candidate_ids=list(support_ids),
                event=payload.event.strip(),
                location=payload.location.strip(),
                date_posted=payload.date_posted or (
                    candidate.published_at.date() if candidate.published_at else None
                ),
                summary=payload.summary.strip(),
                priority=payload.priority,
                property_type=payload.property_type.strip() or "other",
                service_angle=payload.service_angle.strip(),
                filter_reason=payload.filter_reason.strip(),
                confidence=payload.confidence,
                evidence=evidence,
            )
        )

    def _quarantine_bulk_candidate(
        self, candidate: DiscoveryCandidate, response_path: str, exc: Exception
    ) -> None:
        error = f"{type(exc).__name__}:{exc}"
        self.state.save_candidate(
            candidate.model_copy(
                update={
                    "record_status": RecordStatus.REVIEW,
                    "validation_errors": [*candidate.validation_errors, error],
                }
            )
        )
        self.state.add_review(
            ReviewItem(
                review_id=stable_uuid(
                    "review", self.options.run_id, "bulk-qualify", candidate.candidate_id
                ),
                run_id=self.options.run_id,
                stage="qualify",
                record_type="discovery_candidate",
                record_id=candidate.candidate_id,
                reason_code="bulk_model_contract_invalid",
                validation_errors=[error],
                raw_artifact_path=response_path,
            )
        )

    def _seed(self) -> dict:
        if not self.options.seed_db:
            return {"events": 0, "organizations": 0, "candidates": 0}
        _verify_seed_manifest(
            self.options.seed_db,
            self.options.seed_run_id,
            overall_since=self.since,
            overall_until=self.until,
            archive_until=self.archive_until,
        )
        seed = StateStore(self.options.seed_db)
        events = seed.active_events_for_run(self.options.seed_run_id)
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
        organizations = {
            item.organization_id: item for item in self.state.organizations()
        }
        buckets: dict[str, list[LeadEvent]] = defaultdict(list)
        for event in events:
            city = normalize_text(event.location.split(",", 1)[0]) or "unknown"
            buckets[city].append(event)
        batches: list[list[LeadEvent]] = []
        for rows in buckets.values():
            ordered = sorted(
                rows,
                key=lambda item: (
                    normalize_text(
                        organizations.get(item.organization_id).canonical_name
                        if organizations.get(item.organization_id)
                        else item.organization_id
                    ),
                    normalize_text(item.event),
                    item.lead_event_id,
                ),
            )
            batches.extend(_chunks(ordered, 40))
        outcomes = [
            self._dedup_batch(batch, organizations)
            for batch in batches if len(batch) > 1
        ]
        return {
            "submitted": len(events),
            "batches": len(outcomes),
            "events": len(self.state.active_events_for_run(self.options.run_id)),
            "reviews": sum(item["reviews"] for item in outcomes),
        }

    def _dedup_batch(
        self,
        events: list[LeadEvent],
        organizations: dict[str, Organization],
    ) -> dict[str, int]:
        batch_id = stable_hash(*(item.lead_event_id for item in events))[:20]
        inputs = [
            {
                "lead_event_id": event.lead_event_id,
                "organization": (
                    organizations[event.organization_id].canonical_name
                    if event.organization_id in organizations else event.organization_id
                ),
                "event": event.event,
                "location": event.location,
                "date_posted": str(event.date_posted or ""),
            }
            for event in events
        ]
        prompt = FUZZY_PROMPT.format(events=json.dumps(inputs, sort_keys=True))
        attempt_id = stable_uuid("attempt", self.options.run_id, "bulk-dedup", batch_id)
        request = self.artifacts.write_raw(
            "dedup", f"{attempt_id}-request.json",
            {"model": self.options.model, "prompt": prompt},
        )
        started = datetime.now(timezone.utc).isoformat()
        response_path = ""
        try:
            text, usage = self.model_call(self.options.model, prompt, [])
            response = self.artifacts.write_raw_text(
                "dedup", f"{attempt_id}-response.txt", text
            )
            response_path = response["path"]
            match = re.search(r"\[.*\]", re.sub(r"<<ccr:[^>]+>>", "", text), re.DOTALL)
            if not match:
                raise ValueError("dedup response did not contain a JSON list")
            groups = validate_fuzzy_groups(
                [item.lead_event_id for item in events], json.loads(match.group())
            )
        except Exception as exc:
            for event in events:
                self.state.add_review(
                    ReviewItem(
                        review_id=stable_uuid(
                            "review", self.options.run_id, "bulk-dedup", event.lead_event_id
                        ),
                        run_id=self.options.run_id,
                        stage="dedup",
                        record_type="lead_event",
                        record_id=event.lead_event_id,
                        reason_code="bulk_fuzzy_dedup_contract_invalid",
                        validation_errors=[f"{type(exc).__name__}:{exc}"],
                    )
                )
            self.state.record_provider_attempt(
                attempt_id=attempt_id, run_id=self.options.run_id, stage="dedup",
                provider="model", target_type="lead_event_batch", target_id=batch_id,
                status="review", request_artifact_path=request["path"],
                response_artifact_path=response_path,
                error={"type": type(exc).__name__, "message": str(exc)},
                started_at=started,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            return {"events": len(events), "reviews": len(events)}
        by_id = {item.lead_event_id: item for item in events}
        for group in groups:
            kept = by_id[group.kept_id]
            members = [by_id[item] for item in group.member_ids]
            merged = kept.model_copy(
                update={
                    "supporting_candidate_ids": list(dict.fromkeys(
                        candidate_id_value
                        for member in members
                        for candidate_id_value in member.supporting_candidate_ids
                    )),
                    "evidence": list({
                        (evidence.url, evidence.supports, evidence.provider): evidence
                        for member in members for evidence in member.evidence
                    }.values()),
                }
            )
            self.state.save_lead_event(merged)
            for member in members:
                self.state.save_event_merge(
                    self.options.run_id, member.lead_event_id, kept.lead_event_id
                )
        self.state.record_provider_attempt(
            attempt_id=attempt_id, run_id=self.options.run_id, stage="dedup",
            provider="model", target_type="lead_event_batch", target_id=batch_id,
            status="completed", token_usage=usage,
            request_artifact_path=request["path"], response_artifact_path=response_path,
            started_at=started, completed_at=datetime.now(timezone.utc).isoformat(),
        )
        return {"events": len(groups), "reviews": 0}

    def _score(self) -> dict:
        events = self.state.active_events_for_run(self.options.run_id)
        existing = {
            item.lead_event_id for item in self.state.scores_for_run(self.options.run_id)
        }
        pending = [item for item in events if item.lead_event_id not in existing]
        batches = list(_chunks(pending, 40))
        outcomes = [self._score_batch(batch) for batch in batches]
        return {
            "submitted": len(events),
            "pending": len(pending),
            "batches": len(batches),
            "scored": len(existing) + sum(item["scored"] for item in outcomes),
            "reviews": sum(item["reviews"] for item in outcomes),
        }

    def _score_batch(self, events: list[LeadEvent]) -> dict[str, int]:
        batch_id = stable_hash(*(item.lead_event_id for item in events))[:20]
        inputs = [
            {
                "lead_event_id": item.lead_event_id,
                "event": item.event,
                "location": item.location,
                "date_posted": str(item.date_posted or ""),
                "summary": item.summary,
                "priority": item.priority,
                "property_type": item.property_type,
                "service_angle": item.service_angle,
            }
            for item in events
        ]
        prompt = BULK_SCORE_PROMPT.format(events=json.dumps(inputs, sort_keys=True))
        attempt_id = stable_uuid("attempt", self.options.run_id, "bulk-score", batch_id)
        request = self.artifacts.write_raw(
            "score", f"{attempt_id}-request.json",
            {"model": self.options.model, "prompt": prompt},
        )
        started = datetime.now(timezone.utc).isoformat()
        response_path = ""
        try:
            text, usage = self.model_call(self.options.model, prompt, [])
            response = self.artifacts.write_raw_text(
                "score", f"{attempt_id}-response.txt", text
            )
            response_path = response["path"]
            parsed = parse_scores(text, {item.lead_event_id for item in events})
        except Exception as exc:
            for event in events:
                self.state.add_review(
                    ReviewItem(
                        review_id=stable_uuid(
                            "review", self.options.run_id, "bulk-score", event.lead_event_id
                        ),
                        run_id=self.options.run_id,
                        stage="score",
                        record_type="lead_event",
                        record_id=event.lead_event_id,
                        reason_code="bulk_score_contract_invalid",
                        validation_errors=[f"{type(exc).__name__}:{exc}"],
                    )
                )
            self.state.record_provider_attempt(
                attempt_id=attempt_id, run_id=self.options.run_id, stage="score",
                provider="model", target_type="lead_event_batch", target_id=batch_id,
                status="review", request_artifact_path=request["path"],
                response_artifact_path=response_path,
                error={"type": type(exc).__name__, "message": str(exc)},
                started_at=started,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            return {"scored": 0, "reviews": len(events)}
        for event in events:
            self.state.save_score(
                LeadScore(
                    run_id=self.options.run_id,
                    lead_event_id=event.lead_event_id,
                    score=parsed[event.lead_event_id],
                    model=self.options.model,
                    attempt_id=attempt_id,
                )
            )
        self.state.record_provider_attempt(
            attempt_id=attempt_id, run_id=self.options.run_id, stage="score",
            provider="model", target_type="lead_event_batch", target_id=batch_id,
            status="completed", token_usage=usage,
            request_artifact_path=request["path"], response_artifact_path=response_path,
            started_at=started, completed_at=datetime.now(timezone.utc).isoformat(),
        )
        return {"scored": len(events), "reviews": 0}

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
        raw_dir = self.artifacts.raw_dir / "company-profiles"
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
            event_evidence_urls = list(dict.fromkeys(
                item.url for event in events for item in event.evidence
            ))
            anchor_evidence_urls = [item.url for item in anchor.evidence]
            variants = {
                key: _validate_variant((payload.get("variants") or {}).get(key) or {})
                for key in WHY_KEYS
            }
            for key in ("a", "c"):
                variants[key] = _require_event_evidence(
                    variants[key], anchor_evidence_urls
                )
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
                evidence_urls=event_evidence_urls,
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
        events_by_id = {
            item.lead_event_id: item
            for item in self.state.active_events_for_run(self.options.run_id)
        }
        repair_items = {}
        for profile in profiles:
            for key, variant in profile.variants.items():
                if variant.text and variant.source_urls and variant.status != "valid":
                    if key in {"a", "c"}:
                        anchor = events_by_id.get(profile.anchor_lead_event_id)
                        anchor_urls = [item.url for item in anchor.evidence] if anchor else []
                        checked = _require_event_evidence(variant, anchor_urls)
                        if "why_line_missing_event_evidence" in checked.validation_errors:
                            continue
                    repair_items[f"{profile.profile_key}:{key}"] = variant.text
        if not repair_items:
            return profiles
        payload: dict[str, str] = {}
        for batch in _chunks(list(repair_items.items()), 25):
            batch_items = dict(batch)
            batch_id = stable_hash(*batch_items)[:20]
            prompt = REPAIR_PROMPT.format(items=json.dumps(batch_items, sort_keys=True))
            attempt_id = stable_uuid(
                "attempt", self.options.run_id, "company-why-repair", batch_id
            )
            request = self.artifacts.write_raw(
                "companies", f"{attempt_id}-request.json",
                {"model": self.options.model, "prompt": prompt},
            )
            started = datetime.now(timezone.utc).isoformat()
            response_path = ""
            try:
                text, usage = self.model_call(self.options.model, prompt, [])
                response = self.artifacts.write_raw_text(
                    "companies", f"{attempt_id}-response.txt", text
                )
                response_path = response["path"]
                batch_payload = _parse_object(text)
                if set(batch_payload) != set(batch_items):
                    raise ValueError("why repair IDs must match exactly")
                payload.update({key: str(value) for key, value in batch_payload.items()})
                status = "completed"
                error = {}
            except Exception as exc:
                usage = {}
                status = "review"
                error = {"type": type(exc).__name__, "message": str(exc)}
            self.state.record_provider_attempt(
                attempt_id=attempt_id,
                run_id=self.options.run_id,
                stage="companies",
                provider="model",
                target_type="why_repair_batch",
                target_id=batch_id,
                status=status,
                token_usage=usage,
                request_artifact_path=request["path"],
                response_artifact_path=response_path,
                error=error,
                started_at=started,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
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
                if key in {"a", "c"}:
                    anchor = events_by_id.get(profile.anchor_lead_event_id)
                    candidate = _require_event_evidence(
                        candidate,
                        [item.url for item in anchor.evidence] if anchor else [],
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
        final_dir = self.artifacts.final_dir
        _write_csv(final_dir / "leads.csv", lead_fields, lead_rows)
        _write_csv(final_dir / "companies.csv", company_fields, company_rows)
        _write_jsonl(final_dir / "lead_events.jsonl", events)
        _write_jsonl(final_dir / "company_profiles.jsonl", profiles)
        _write_jsonl(
            final_dir / "reviews.jsonl",
            self.state.reviews_for_run(self.options.run_id),
        )
        for path, kind in (
            (final_dir / "leads.csv", "csv"),
            (final_dir / "companies.csv", "csv"),
            (final_dir / "lead_events.jsonl", "jsonl"),
            (final_dir / "company_profiles.jsonl", "jsonl"),
            (final_dir / "reviews.jsonl", "jsonl"),
            (final_dir / "coverage.csv", "csv"),
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
        _write_csv(self.artifacts.final_dir / "coverage.csv", fields, rows)
        (self.artifacts.final_dir / "coverage.json").write_text(
            json.dumps([item.model_dump(mode="json") for item in self.coverage], indent=2),
            encoding="utf-8",
        )
        self.artifacts.record_existing("discover", "csv", self.artifacts.final_dir / "coverage.csv")
        self.artifacts.record_existing("discover", "json", self.artifacts.final_dir / "coverage.json")

    def _write_profiles(self, profiles: list[CompanyProfile]) -> None:
        _write_jsonl(self.artifacts.final_dir / "company_profiles.jsonl", profiles)

    def _load_profiles(self) -> list[CompanyProfile]:
        path = self.artifacts.final_dir / "company_profiles.jsonl"
        if not path.exists():
            return self.profiles
        return [
            CompanyProfile.model_validate_json(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def _hydrate(self, stage: str) -> None:
        if stage == "discover":
            path = self.artifacts.final_dir / "coverage.json"
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


def _offline_screen(candidate: DiscoveryCandidate) -> str:
    """Conservatively remove obvious non-Arizona/non-AEC pages without model spend."""
    headline = normalize_text(f"{candidate.title} {candidate.canonical_url}")
    body = ""
    if candidate.raw_artifact_path:
        try:
            raw = Path(candidate.raw_artifact_path).read_text(
                encoding="utf-8", errors="replace"
            )[:200_000]
            raw = re.sub(r"(?is)<(script|style|noscript).*?>.*?</\1>", " ", raw)
            body = normalize_text(html.unescape(re.sub(r"(?s)<[^>]+>", " ", raw)))
        except OSError:
            return "ambiguous"
    combined = f"{headline} {body[:40_000]}"
    headline_places = {term for term in ARIZONA_TERMS if _place_present(headline, term)}
    body_places = {term for term in ARIZONA_TERMS if _place_present(combined, term)}
    event_hits = {term for term in AEC_EVENT_TERMS if _prefix_present(combined, term)}
    if not event_hits:
        return "ambiguous"
    if not body_places:
        non_arizona = {
            term for term in NON_ARIZONA_STATE_TERMS if _term_present(combined, term)
        }
        return "reject" if non_arizona else "ambiguous"
    if headline_places or len(body_places) >= 2:
        return "keep"
    return "ambiguous"


def _term_present(value: str, term: str) -> bool:
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", value))


def _place_present(value: str, term: str) -> bool:
    if term not in {"mesa", "surprise", "gilbert"}:
        return _term_present(value, term)
    place_context = (
        rf"(?:\b(?:in|near|at|city of)\s+{re.escape(term)}\b)"
        rf"|(?:\b{re.escape(term)}(?:\s*,?\s*(?:arizona|az)\b"
        rf"|\s+(?:property|site|development|warehouse|industrial|retail|office|hotel)\b))"
    )
    return bool(re.search(place_context, value))


def _prefix_present(value: str, term: str) -> bool:
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(term)}", value))


def _archive_url_in_scope(source: CuratedSource, page_url: str) -> bool:
    """Prevent a regional curated entry from expanding into a national domain crawl."""
    source_parts = urlsplit(source.url)
    page_parts = urlsplit(page_url)
    host = source_parts.netloc.casefold()
    local_host_hints = (
        "arizona", "phoenix", "tucson", "azbigmedia", "azbex", "azcentral",
        "arizcc", "roselawgroupreporter",
    )
    if any(term in host for term in local_host_hints):
        return True
    scope_terms = {
        term
        for term in ("arizona", "phoenix", "tucson", "southwest")
        if term in normalize_text(source_parts.path)
        or term in normalize_text(source.name)
    }
    if not scope_terms:
        return source_parts.path in {"", "/"}
    page_scope = normalize_text(page_parts.path)
    return bool(scope_terms & {term for term in scope_terms if term in page_scope})


def _candidate_excerpt(candidate: DiscoveryCandidate, limit: int) -> str:
    if not candidate.raw_artifact_path:
        return ""
    try:
        raw = Path(candidate.raw_artifact_path).read_text(
            encoding="utf-8", errors="replace"
        )[:200_000]
    except OSError:
        return ""
    raw = re.sub(r"(?is)<(script|style|noscript).*?>.*?</\1>", " ", raw)
    value = html.unescape(re.sub(r"(?s)<[^>]+>", " ", raw))
    return re.sub(r"\s+", " ", value).strip()[:limit]


def _chunks(items: list, size: int) -> Iterable[list]:
    for start in range(0, len(items), max(1, size)):
        yield items[start : start + max(1, size)]


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


def _require_event_evidence(
    variant: WhyVariant, event_evidence_urls: Iterable[str]
) -> WhyVariant:
    allowed = set()
    for value in event_evidence_urls:
        try:
            allowed.add(canonicalize_url(value))
        except ValueError:
            continue
    if not (set(variant.source_urls) & allowed):
        return WhyVariant(
            text="",
            confidence=variant.confidence,
            source_urls=variant.source_urls,
            status="review",
            validation_errors=list(dict.fromkeys([
                *variant.validation_errors,
                "why_line_missing_event_evidence",
            ])),
        )
    return variant


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


def _verify_seed_manifest(
    db_path: Path,
    run_id: str,
    *,
    overall_since: date | None = None,
    overall_until: date | None = None,
    archive_until: date | None = None,
) -> None:
    state = StateStore(db_path)
    with state.connect() as conn:
        row = conn.execute(
            "SELECT status, manifest_path, stamp, since_date FROM v2_runs WHERE run_id=?",
            (run_id,),
        ).fetchone()
    if not row or row["status"] != StageStatus.COMPLETED.value:
        raise ValueError("seed run is not completed")
    seed_since = date.fromisoformat(row["since_date"])
    seed_until = date.fromisoformat(row["stamp"])
    if overall_since and seed_since < overall_since:
        raise ValueError("seed run begins before the bulk range")
    if overall_until and seed_until > overall_until:
        raise ValueError("seed run ends after the bulk range")
    if archive_until and seed_since <= archive_until:
        raise ValueError("seed run overlaps the archive discovery range")
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
