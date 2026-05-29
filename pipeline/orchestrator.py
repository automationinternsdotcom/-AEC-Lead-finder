from __future__ import annotations

import sqlite3
from uuid import uuid4

from schema import EnrichmentResult, ExtractionResult, FetchedPage, LeadRecord, SourceRecord
from pipeline import crawl, db, dedupe, fetch, qualify, score, util
from pipeline.config import Settings
from pipeline.crm import CrmClient
from pipeline.enrich import Enricher
from pipeline.extract import LeadExtractor, looks_like_candidate
from pipeline.sheets import CsvSheetGateway


class LeadPipeline:
    def __init__(
        self,
        settings: Settings,
        sheets: CsvSheetGateway,
        extractor: LeadExtractor,
        enricher: Enricher,
        crm: CrmClient,
    ) -> None:
        self.settings = settings
        self.sheets = sheets
        self.extractor = extractor
        self.enricher = enricher
        self.crm = crm

    def run(self) -> int:
        run_id = str(uuid4())
        conn = db.connect(self.settings.db_path)
        db.start_run(conn, run_id)
        conn.commit()

        try:
            review_leads = self._discover_and_prepare_leads(conn)
            self.sheets.upsert_review_leads(review_leads)
            self._sync_approved_review_leads(conn)
            db.finish_run(conn, run_id, "ok")
            conn.commit()
            util.json_log("run_finished", run_id=run_id, review_leads=len(review_leads))
            return 0
        except Exception as exc:
            db.finish_run(conn, run_id, "failed", repr(exc))
            conn.commit()
            util.json_log("run_failed", run_id=run_id, error=repr(exc))
            return 1
        finally:
            conn.close()

    def _discover_and_prepare_leads(self, conn: sqlite3.Connection) -> list[LeadRecord]:
        sources = [source for source in self.sheets.load_sources() if source.is_active]
        review_leads: list[LeadRecord] = []

        with util.make_http_client(self.settings) as http:
            for source in sources[: self.settings.max_sources_per_run]:
                try:
                    pages = self._discover_source_pages(source, http)
                    for page in pages[: self.settings.max_articles_per_run]:
                        lead = self._process_page(conn, page, http)
                        if lead and lead.qualification_status != "rejected":
                            review_leads.append(lead)
                    self.sheets.update_source_status(
                        source.source_id,
                        last_checked_at=util.utc_now_iso(),
                        last_success_at=util.utc_now_iso(),
                    )
                except Exception as exc:
                    self.sheets.update_source_status(
                        source.source_id,
                        last_checked_at=util.utc_now_iso(),
                        last_error=repr(exc),
                    )
                    util.json_log("source_failed", source_id=source.source_id, error=repr(exc))

        conn.commit()
        return review_leads

    def _discover_source_pages(self, source: SourceRecord, http) -> list[FetchedPage]:
        pages: list[FetchedPage] = []
        if source.source_type in {"rss", "hybrid"}:
            pages.extend(fetch.discover_rss_pages(source, http))
        if source.source_type in {"website", "hybrid"}:
            pages.extend(crawl.discover_website_pages(source, http, self.settings.max_pages_per_source))
        return pages

    def _process_page(self, conn: sqlite3.Connection, page: FetchedPage, http) -> LeadRecord | None:
        url_hash = util.stable_hash(page.url)
        if db.has_seen_url(conn, url_hash):
            return None

        page = fetch.fetch_page_content(page, http)
        db.save_fetched_page(conn, url_hash, page)

        is_candidate, reason = looks_like_candidate(page.cleaned_text or "")
        if not is_candidate:
            util.json_log("page_rejected", url=page.url, reason=reason)
            return None

        extraction = self.extractor.extract(page)
        status, qualification_reason, rejection_reason = qualify.qualify(extraction)
        enrichment = self._maybe_enrich(extraction, status)
        lead = _build_lead(page, extraction, status, qualification_reason, rejection_reason, enrichment)

        lead.dedupe_key = dedupe.build_dedupe_key(lead)
        dedupe_result = dedupe.check_local_duplicates(conn, lead)
        possible_duplicate = dedupe_result.outcome != "new"
        if dedupe_result.outcome == "exact_duplicate":
            lead.pipedrive_status = "duplicate"

        score_breakdown = score.score_lead(extraction, enrichment, possible_duplicate=possible_duplicate)
        lead.score_breakdown = score_breakdown
        lead.confidence_score = score_breakdown.score

        # Scores below the PRD threshold should stay out of the review queue.
        if score_breakdown.route == "reject":
            lead.qualification_status = "rejected"
            lead.rejection_reason = lead.rejection_reason or "score_below_40"

        db.save_lead(conn, lead)
        util.json_log(
            "lead_prepared",
            lead_id=lead.lead_id,
            status=lead.qualification_status,
            score=lead.confidence_score,
            dedupe=dedupe_result.outcome,
        )
        return lead

    def _maybe_enrich(
        self,
        extraction: ExtractionResult,
        status: str,
    ) -> EnrichmentResult | None:
        if status == "rejected" or extraction.has_article_contact:
            return None
        try:
            return self.enricher.enrich(extraction)
        except Exception as exc:
            util.json_log("enrichment_failed", source_url=extraction.source_url, error=repr(exc))
            return EnrichmentResult(needs_human_review=True, enrichment_confidence=0)

    def _sync_approved_review_leads(self, conn: sqlite3.Connection) -> None:
        for decision in self.sheets.load_review_decisions():
            if decision.review_status != "approved":
                continue
            lead = db.load_lead(conn, decision.lead_id)
            if not lead or lead.pipedrive_status in {"created", "updated", "duplicate"}:
                continue
            result = self.crm.sync_approved_lead(lead)
            lead.pipedrive_status = result.status
            lead.pipedrive_org_id = result.org_id
            lead.pipedrive_person_id = result.person_id
            lead.pipedrive_lead_id = result.lead_id
            db.save_lead(conn, lead)
            util.json_log("lead_synced", lead_id=lead.lead_id, status=result.status)


def _build_lead(
    page: FetchedPage,
    extraction: ExtractionResult,
    status: str,
    qualification_reason: str,
    rejection_reason: str | None,
    enrichment: EnrichmentResult | None,
) -> LeadRecord:
    if extraction.has_article_contact:
        enrichment_status = "not_needed"
    elif enrichment and enrichment.enrichment_confidence > 0:
        enrichment_status = "review" if enrichment.needs_human_review else "complete"
    else:
        enrichment_status = "pending"

    return LeadRecord(
        source_id=page.source_id,
        source_site=page.source_site,
        source_url=page.url,
        article_title=page.title,
        published_at=page.published_at,
        property_name=extraction.property_name,
        address=extraction.address,
        property_type=extraction.property_type,
        opening_date_or_status=extraction.opening_date_or_status,
        contact_name=extraction.contact_name,
        contact_title=extraction.contact_title,
        contact_email=extraction.contact_email,
        contact_phone=extraction.contact_phone,
        description=extraction.description,
        qualification_status=status,
        qualification_reason=qualification_reason,
        enrichment_status=enrichment_status,
        pipedrive_status="not_ready",
        rejection_reason=rejection_reason,
        extraction=extraction,
        enrichment=enrichment,
    )

