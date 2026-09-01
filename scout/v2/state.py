"""Transactional normalized SQLite state for V2."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from pydantic import BaseModel

from .contracts import (
    CompanyProfile,
    ContactCandidate,
    DiscoveryCandidate,
    LeadEvent,
    LeadScore,
    Organization,
    OutreachRecipient,
    Person,
    RecordStatus,
    ReviewItem,
    StageStatus,
)
from .ids import normalize_text


SCHEMA_VERSION = 5


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class _ClosingConnection(sqlite3.Connection):
    """Make `with store.connect()` close, not merely commit, the connection."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


MIGRATION_1 = """
CREATE TABLE IF NOT EXISTS v2_schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS v2_runs (
    run_id TEXT PRIMARY KEY,
    stamp TEXT NOT NULL,
    since_date TEXT NOT NULL,
    status TEXT NOT NULL,
    configuration_json TEXT NOT NULL DEFAULT '{}',
    manifest_path TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS v2_stage_runs (
    run_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    counters_json TEXT NOT NULL DEFAULT '{}',
    error_json TEXT NOT NULL DEFAULT '{}',
    started_at TEXT,
    completed_at TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (run_id, stage),
    FOREIGN KEY (run_id) REFERENCES v2_runs(run_id)
);
CREATE TABLE IF NOT EXISTS v2_sources (
    source_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    domain TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'Arizona',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS v2_discovered_feeds (
    feed_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    discovery_method TEXT NOT NULL,
    redirect_chain_json TEXT NOT NULL DEFAULT '[]',
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_valid_item_at TEXT,
    last_checked_at TEXT,
    validation_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (source_id) REFERENCES v2_sources(source_id)
);
CREATE TABLE IF NOT EXISTS v2_discovery_candidates (
    candidate_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    provider_id TEXT NOT NULL DEFAULT '',
    discovered_url TEXT NOT NULL,
    resolved_url TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    source_id TEXT NOT NULL,
    published_at TEXT,
    record_status TEXT NOT NULL,
    raw_artifact_path TEXT NOT NULL DEFAULT '',
    raw_artifact_hash TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES v2_runs(run_id),
    FOREIGN KEY (source_id) REFERENCES v2_sources(source_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS v2_candidates_provider_identity
    ON v2_discovery_candidates(provider, provider_id) WHERE provider_id <> '';
CREATE INDEX IF NOT EXISTS v2_candidates_canonical_url
    ON v2_discovery_candidates(canonical_url);
CREATE TABLE IF NOT EXISTS v2_run_candidates (
    run_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    record_status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (run_id, candidate_id),
    FOREIGN KEY (run_id) REFERENCES v2_runs(run_id),
    FOREIGN KEY (candidate_id) REFERENCES v2_discovery_candidates(candidate_id)
);
CREATE TABLE IF NOT EXISTS v2_organizations (
    organization_id TEXT PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    domain TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT '',
    inferred_identity INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS v2_organization_aliases (
    organization_id TEXT NOT NULL,
    alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    PRIMARY KEY (organization_id, normalized_alias),
    FOREIGN KEY (organization_id) REFERENCES v2_organizations(organization_id)
);
CREATE TABLE IF NOT EXISTS v2_people (
    person_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    inferred_identity INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (organization_id) REFERENCES v2_organizations(organization_id)
);
CREATE TABLE IF NOT EXISTS v2_lead_events (
    lead_event_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    organization_id TEXT NOT NULL,
    primary_candidate_id TEXT NOT NULL,
    record_status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES v2_runs(run_id),
    FOREIGN KEY (organization_id) REFERENCES v2_organizations(organization_id),
    FOREIGN KEY (primary_candidate_id) REFERENCES v2_discovery_candidates(candidate_id)
);
CREATE TABLE IF NOT EXISTS v2_lead_event_sources (
    lead_event_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    PRIMARY KEY (lead_event_id, candidate_id),
    FOREIGN KEY (lead_event_id) REFERENCES v2_lead_events(lead_event_id),
    FOREIGN KEY (candidate_id) REFERENCES v2_discovery_candidates(candidate_id)
);
CREATE TABLE IF NOT EXISTS v2_contact_candidates (
    contact_candidate_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    lead_event_id TEXT NOT NULL,
    organization_id TEXT NOT NULL,
    person_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    verification_status TEXT NOT NULL,
    selected INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES v2_runs(run_id),
    FOREIGN KEY (lead_event_id) REFERENCES v2_lead_events(lead_event_id),
    FOREIGN KEY (organization_id) REFERENCES v2_organizations(organization_id),
    FOREIGN KEY (person_id) REFERENCES v2_people(person_id)
);
CREATE TABLE IF NOT EXISTS v2_provider_attempts (
    attempt_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    provider TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    status TEXT NOT NULL,
    billable INTEGER NOT NULL DEFAULT 0,
    token_usage_json TEXT NOT NULL DEFAULT '{}',
    request_artifact_path TEXT NOT NULL DEFAULT '',
    response_artifact_path TEXT NOT NULL DEFAULT '',
    error_json TEXT NOT NULL DEFAULT '{}',
    started_at TEXT NOT NULL,
    completed_at TEXT,
    FOREIGN KEY (run_id) REFERENCES v2_runs(run_id)
);
CREATE INDEX IF NOT EXISTS v2_provider_target
    ON v2_provider_attempts(provider, target_type, target_id);
CREATE TABLE IF NOT EXISTS v2_scores (
    run_id TEXT NOT NULL,
    lead_event_id TEXT NOT NULL,
    score INTEGER NOT NULL CHECK(score BETWEEN 0 AND 100),
    model TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (run_id, lead_event_id),
    FOREIGN KEY (run_id) REFERENCES v2_runs(run_id),
    FOREIGN KEY (lead_event_id) REFERENCES v2_lead_events(lead_event_id)
);
CREATE TABLE IF NOT EXISTS v2_review_items (
    review_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    record_type TEXT NOT NULL,
    record_id TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    state TEXT NOT NULL,
    retry_count INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES v2_runs(run_id)
);
CREATE INDEX IF NOT EXISTS v2_review_retry
    ON v2_review_items(state, stage, retry_count);
CREATE TABLE IF NOT EXISTS v2_apollo_cache (
    cache_key TEXT PRIMARY KEY,
    normalized_person TEXT NOT NULL,
    normalized_organization TEXT NOT NULL,
    status TEXT NOT NULL,
    billable INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL DEFAULT '{}',
    error_json TEXT NOT NULL DEFAULT '{}',
    attempted_at TEXT NOT NULL,
    expires_at TEXT
);
CREATE TABLE IF NOT EXISTS v2_artifacts (
    run_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    kind TEXT NOT NULL,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    byte_count INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (run_id, path),
    FOREIGN KEY (run_id) REFERENCES v2_runs(run_id)
);
"""

MIGRATION_2 = """
CREATE TABLE IF NOT EXISTS v2_verification_cache (
    cache_key TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    value TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    checked_at TEXT NOT NULL,
    expires_at TEXT
);
CREATE INDEX IF NOT EXISTS v2_verification_expiry
    ON v2_verification_cache(kind, expires_at);
"""

MIGRATION_3 = """
CREATE TABLE IF NOT EXISTS v2_event_merges (
    run_id TEXT NOT NULL,
    merged_event_id TEXT NOT NULL,
    kept_event_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (run_id, merged_event_id),
    FOREIGN KEY (run_id) REFERENCES v2_runs(run_id),
    FOREIGN KEY (merged_event_id) REFERENCES v2_lead_events(lead_event_id),
    FOREIGN KEY (kept_event_id) REFERENCES v2_lead_events(lead_event_id)
);
"""

MIGRATION_4 = """
CREATE TABLE IF NOT EXISTS v2_company_profiles (
    run_id TEXT NOT NULL,
    company_id TEXT NOT NULL,
    anchor_lead_event_id TEXT NOT NULL,
    record_status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (run_id, company_id),
    FOREIGN KEY (run_id) REFERENCES v2_runs(run_id),
    FOREIGN KEY (anchor_lead_event_id) REFERENCES v2_lead_events(lead_event_id)
);
CREATE TABLE IF NOT EXISTS v2_outreach_recipients (
    run_id TEXT NOT NULL,
    recipient_id TEXT NOT NULL,
    company_id TEXT NOT NULL,
    person_id TEXT NOT NULL,
    rank INTEGER NOT NULL,
    primary_recipient INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (run_id, recipient_id),
    FOREIGN KEY (run_id) REFERENCES v2_runs(run_id)
);
CREATE INDEX IF NOT EXISTS v2_outreach_recipients_company
    ON v2_outreach_recipients(run_id, company_id, rank);
"""

MIGRATION_5 = """
CREATE TABLE IF NOT EXISTS v2_qualification_completions (
    candidate_id TEXT NOT NULL,
    since_date TEXT NOT NULL,
    stamp TEXT NOT NULL,
    run_id TEXT NOT NULL,
    outcome TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (candidate_id, since_date, stamp),
    FOREIGN KEY (candidate_id) REFERENCES v2_discovery_candidates(candidate_id),
    FOREIGN KEY (run_id) REFERENCES v2_runs(run_id)
);
CREATE INDEX IF NOT EXISTS v2_qualification_window
    ON v2_qualification_completions(since_date, stamp, candidate_id);
"""


class StateStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=30, factory=_ClosingConnection)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def migrate(self) -> int:
        with self.transaction() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS v2_schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            applied = {row[0] for row in conn.execute("SELECT version FROM v2_schema_migrations")}
            if 1 not in applied:
                conn.executescript(MIGRATION_1)
                conn.execute(
                    "INSERT INTO v2_schema_migrations(version, applied_at) VALUES (?, ?)",
                    (1, _now()),
                )
            if 2 not in applied:
                conn.executescript(MIGRATION_2)
                conn.execute(
                    "INSERT INTO v2_schema_migrations(version, applied_at) VALUES (?, ?)",
                    (2, _now()),
                )
            if 3 not in applied:
                conn.executescript(MIGRATION_3)
                conn.execute(
                    "INSERT INTO v2_schema_migrations(version, applied_at) VALUES (?, ?)",
                    (3, _now()),
                )
            if 4 not in applied:
                conn.executescript(MIGRATION_4)
                conn.execute(
                    "INSERT INTO v2_schema_migrations(version, applied_at) VALUES (?, ?)",
                    (4, _now()),
                )
            if 5 not in applied:
                conn.executescript(MIGRATION_5)
                conn.execute(
                    "INSERT INTO v2_schema_migrations(version, applied_at) VALUES (?, ?)",
                    (5, _now()),
                )
        return SCHEMA_VERSION

    def create_run(
        self,
        run_id: str,
        stamp: str,
        since: str,
        configuration: dict | None = None,
        manifest_path: str = "",
    ) -> None:
        now = _now()
        with self.transaction() as conn:
            conn.execute(
                """INSERT INTO v2_runs(
                    run_id, stamp, since_date, status, configuration_json,
                    manifest_path, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    manifest_path=CASE WHEN excluded.manifest_path <> '' THEN excluded.manifest_path ELSE v2_runs.manifest_path END,
                    updated_at=excluded.updated_at""",
                (
                    run_id,
                    stamp,
                    since,
                    StageStatus.PENDING.value,
                    _json(configuration or {}),
                    manifest_path,
                    now,
                    now,
                ),
            )

    def get_run(self, run_id: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM v2_runs WHERE run_id=?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["configuration"] = json.loads(result.pop("configuration_json"))
        return result

    def set_run_status(self, run_id: str, status: StageStatus, manifest_path: str = "") -> None:
        with self.transaction() as conn:
            conn.execute(
                """UPDATE v2_runs SET status=?, manifest_path=CASE WHEN ? <> '' THEN ? ELSE manifest_path END,
                    updated_at=? WHERE run_id=?""",
                (status.value, manifest_path, manifest_path, _now(), run_id),
            )

    def set_stage_status(
        self,
        run_id: str,
        stage: str,
        status: StageStatus,
        *,
        counters: dict | None = None,
        error: dict | None = None,
    ) -> None:
        now = _now()
        with self.transaction() as conn:
            prior = conn.execute(
                "SELECT attempt_count, started_at FROM v2_stage_runs WHERE run_id=? AND stage=?",
                (run_id, stage),
            ).fetchone()
            attempts = int(prior["attempt_count"]) if prior else 0
            started_at = prior["started_at"] if prior else None
            if status == StageStatus.RUNNING:
                attempts += 1
                started_at = now
            completed_at = now if status in {StageStatus.COMPLETED, StageStatus.FAILED, StageStatus.REVIEW} else None
            conn.execute(
                """INSERT INTO v2_stage_runs(
                    run_id, stage, status, attempt_count, counters_json, error_json,
                    started_at, completed_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, stage) DO UPDATE SET
                    status=excluded.status,
                    attempt_count=excluded.attempt_count,
                    counters_json=excluded.counters_json,
                    error_json=excluded.error_json,
                    started_at=excluded.started_at,
                    completed_at=excluded.completed_at,
                    updated_at=excluded.updated_at""",
                (
                    run_id,
                    stage,
                    status.value,
                    attempts,
                    _json(counters or {}),
                    _json(error or {}),
                    started_at,
                    completed_at,
                    now,
                ),
            )

    def completed_stages(self, run_id: str) -> set[str]:
        with self.connect() as conn:
            return {
                row["stage"]
                for row in conn.execute(
                    "SELECT stage FROM v2_stage_runs WHERE run_id=? AND status=?",
                    (run_id, StageStatus.COMPLETED.value),
                )
            }

    def upsert_source(
        self,
        source_id: str,
        name: str,
        url: str,
        domain: str,
        state: str = "Arizona",
        enabled: bool = True,
    ) -> None:
        now = _now()
        with self.transaction() as conn:
            conn.execute(
                """INSERT INTO v2_sources(
                    source_id, name, url, domain, state, enabled, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    name=excluded.name, url=excluded.url, domain=excluded.domain,
                    state=excluded.state, enabled=excluded.enabled, updated_at=excluded.updated_at""",
                (source_id, name, url, domain, state, int(enabled), now, now),
            )

    def upsert_feed(
        self,
        feed_id: str,
        source_id: str,
        url: str,
        status: str,
        discovery_method: str,
        *,
        redirect_chain: list[str] | None = None,
        consecutive_failures: int = 0,
        last_valid_item_at: str | None = None,
        last_checked_at: str | None = None,
        validation_error: str = "",
    ) -> None:
        now = _now()
        with self.transaction() as conn:
            conn.execute(
                """INSERT INTO v2_discovered_feeds(
                    feed_id, source_id, url, status, discovery_method, redirect_chain_json,
                    consecutive_failures, last_valid_item_at, last_checked_at,
                    validation_error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    status=excluded.status,
                    discovery_method=excluded.discovery_method,
                    redirect_chain_json=excluded.redirect_chain_json,
                    consecutive_failures=excluded.consecutive_failures,
                    last_valid_item_at=excluded.last_valid_item_at,
                    last_checked_at=excluded.last_checked_at,
                    validation_error=excluded.validation_error,
                    updated_at=excluded.updated_at""",
                (
                    feed_id,
                    source_id,
                    url,
                    status,
                    discovery_method,
                    _json(redirect_chain or []),
                    consecutive_failures,
                    last_valid_item_at,
                    last_checked_at,
                    validation_error,
                    now,
                    now,
                ),
            )

    def feeds(self, statuses: tuple[str, ...] | None = None) -> list[sqlite3.Row]:
        sql = "SELECT * FROM v2_discovered_feeds"
        params: list[str] = []
        if statuses:
            sql += f" WHERE status IN ({', '.join('?' for _ in statuses)})"
            params.extend(statuses)
        sql += " ORDER BY source_id, url"
        with self.connect() as conn:
            return list(conn.execute(sql, params))

    def save_candidate(self, candidate: DiscoveryCandidate) -> None:
        now = _now()
        payload = _json(candidate.model_dump(mode="json"))
        with self.transaction() as conn:
            conn.execute(
                """INSERT INTO v2_discovery_candidates(
                    candidate_id, run_id, provider, provider_id, discovered_url,
                    resolved_url, canonical_url, source_id, published_at, record_status,
                    raw_artifact_path, raw_artifact_hash, payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(candidate_id) DO UPDATE SET
                    resolved_url=excluded.resolved_url,
                    canonical_url=excluded.canonical_url,
                    published_at=COALESCE(excluded.published_at, v2_discovery_candidates.published_at),
                    record_status=excluded.record_status,
                    raw_artifact_path=excluded.raw_artifact_path,
                    raw_artifact_hash=excluded.raw_artifact_hash,
                    payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at""",
                (
                    candidate.candidate_id,
                    candidate.run_id,
                    candidate.provider,
                    candidate.provider_id,
                    candidate.discovered_url,
                    candidate.resolved_url,
                    candidate.canonical_url,
                    candidate.source_id,
                    candidate.published_at.isoformat() if candidate.published_at else None,
                    candidate.record_status.value,
                    candidate.raw_artifact_path,
                    candidate.raw_artifact_hash,
                    payload,
                    now,
                    now,
                ),
            )
            conn.execute(
                """INSERT INTO v2_run_candidates(
                    run_id, candidate_id, record_status, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(run_id, candidate_id) DO UPDATE SET
                    record_status=excluded.record_status, payload_json=excluded.payload_json""",
                (candidate.run_id, candidate.candidate_id, candidate.record_status.value, payload, now),
            )

    def candidate_urls(self) -> set[str]:
        with self.connect() as conn:
            return {
                row["canonical_url"]
                for row in conn.execute("SELECT canonical_url FROM v2_discovery_candidates")
            }

    def candidate_ids(self) -> set[str]:
        with self.connect() as conn:
            return {
                row["candidate_id"]
                for row in conn.execute("SELECT candidate_id FROM v2_discovery_candidates")
            }

    def completed_qualification_candidate_ids(
        self, *, since_date: str | None = None, stamp: str | None = None
    ) -> set[str]:
        """Candidates with a safely reusable prior qualification rejection.

        Qualification depends on the requested publication window, so a
        rejection is reusable only for the same window. Qualified outcomes
        are deliberately excluded because their LeadEvent rows are scoped to
        the originating run; skipping them in a later run would silently drop
        valid events unless those rows were also hydrated. Deferred, review,
        and legacy provider-attempt-only outcomes remain eligible.
        """
        if bool(since_date) != bool(stamp):
            raise ValueError("since_date and stamp must be supplied together")
        if not since_date or not stamp:
            return set()
        with self.connect() as conn:
            return {
                row["candidate_id"]
                for row in conn.execute(
                    """SELECT candidate_id
                       FROM v2_qualification_completions
                       WHERE since_date=? AND stamp=? AND outcome='rejected'""",
                    (since_date, stamp),
                )
            }

    def record_qualification_completion(
        self,
        *,
        candidate_id: str,
        since_date: str,
        stamp: str,
        run_id: str,
        outcome: str,
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                """INSERT INTO v2_qualification_completions(
                    candidate_id, since_date, stamp, run_id, outcome, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(candidate_id, since_date, stamp) DO UPDATE SET
                    run_id=excluded.run_id,
                    outcome=excluded.outcome,
                    created_at=excluded.created_at""",
                (candidate_id, since_date, stamp, run_id, outcome, _now()),
            )

    def completed_provider_target_ids(
        self, run_id: str, stage: str, target_type: str
    ) -> set[str]:
        with self.connect() as conn:
            return {
                row["target_id"]
                for row in conn.execute(
                    """SELECT target_id FROM v2_provider_attempts
                       WHERE run_id=? AND stage=? AND target_type=?
                         AND status='completed'""",
                    (run_id, stage, target_type),
                )
            }

    def candidates_for_run(self, run_id: str) -> list[DiscoveryCandidate]:
        with self.connect() as conn:
            return [
                DiscoveryCandidate.model_validate_json(row["payload_json"])
                for row in conn.execute(
                    "SELECT payload_json FROM v2_run_candidates WHERE run_id=? ORDER BY candidate_id",
                    (run_id,),
                )
            ]

    def candidates_by_ids(self, candidate_ids: set[str]) -> list[DiscoveryCandidate]:
        if not candidate_ids:
            return []
        placeholders = ", ".join("?" for _ in candidate_ids)
        with self.connect() as conn:
            return [
                DiscoveryCandidate.model_validate_json(row["payload_json"])
                for row in conn.execute(
                    f"SELECT payload_json FROM v2_discovery_candidates WHERE candidate_id IN ({placeholders})",
                    sorted(candidate_ids),
                )
            ]

    def save_organization(self, organization: Organization) -> None:
        self.upsert_model(
            "v2_organizations",
            "organization_id",
            organization.organization_id,
            organization,
            canonical_name=organization.canonical_name,
            domain=organization.domain,
            location=organization.location,
            inferred_identity=int(organization.inferred_identity),
        )
        with self.transaction() as conn:
            for alias in organization.aliases:
                conn.execute(
                    """INSERT OR IGNORE INTO v2_organization_aliases(
                        organization_id, alias, normalized_alias
                    ) VALUES (?, ?, ?)""",
                    (organization.organization_id, alias, normalize_text(alias)),
                )

    def save_person(self, person: Person) -> None:
        self.upsert_model(
            "v2_people",
            "person_id",
            person.person_id,
            person,
            organization_id=person.organization_id,
            name=person.name,
            normalized_name=normalize_text(person.name),
            inferred_identity=int(person.inferred_identity),
        )

    def save_lead_event(self, event: LeadEvent) -> None:
        with self.connect() as conn:
            prior = conn.execute(
                "SELECT payload_json FROM v2_lead_events WHERE lead_event_id=?",
                (event.lead_event_id,),
            ).fetchone()
        if prior:
            previous = LeadEvent.model_validate_json(prior["payload_json"])
            sources = list(dict.fromkeys([*previous.supporting_candidate_ids, *event.supporting_candidate_ids]))
            evidence = list(
                {
                    (item.url, item.supports, item.provider): item
                    for item in [*previous.evidence, *event.evidence]
                }.values()
            )
            event = event.model_copy(
                update={"supporting_candidate_ids": sources, "evidence": evidence}
            )
        self.upsert_model(
            "v2_lead_events",
            "lead_event_id",
            event.lead_event_id,
            event,
            run_id=event.run_id,
            organization_id=event.organization_id,
            primary_candidate_id=event.primary_candidate_id,
            record_status=event.record_status.value,
        )
        with self.transaction() as conn:
            for candidate in event.supporting_candidate_ids:
                conn.execute(
                    "INSERT OR IGNORE INTO v2_lead_event_sources(lead_event_id, candidate_id) VALUES (?, ?)",
                    (event.lead_event_id, candidate),
                )

    def retire_events_for_candidate(
        self, run_id: str, candidate_id: str, reason: str
    ) -> int:
        with self.connect() as conn:
            rows = list(
                conn.execute(
                    """SELECT payload_json FROM v2_lead_events
                       WHERE run_id=? AND primary_candidate_id=? AND record_status='valid'""",
                    (run_id, candidate_id),
                )
            )
        for row in rows:
            event = LeadEvent.model_validate_json(row["payload_json"])
            errors = list(event.validation_errors)
            if reason not in errors:
                errors.append(reason)
            self.save_lead_event(
                event.model_copy(
                    update={
                        "record_status": RecordStatus.REJECTED,
                        "validation_errors": errors,
                    }
                )
            )
        return len(rows)

    def save_contact(self, contact: ContactCandidate) -> None:
        self.upsert_model(
            "v2_contact_candidates",
            "contact_candidate_id",
            contact.contact_candidate_id,
            contact,
            run_id=contact.run_id,
            lead_event_id=contact.lead_event_id,
            organization_id=contact.organization_id,
            person_id=contact.person_id,
            provider=contact.provider,
            verification_status=contact.verification_status.value,
            selected=int(contact.selected),
        )

    def save_score(self, score: LeadScore) -> None:
        with self.transaction() as conn:
            conn.execute(
                """INSERT INTO v2_scores(
                    run_id, lead_event_id, score, model, attempt_id, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, lead_event_id) DO UPDATE SET
                    score=excluded.score, model=excluded.model, attempt_id=excluded.attempt_id,
                    payload_json=excluded.payload_json, created_at=excluded.created_at""",
                (
                    score.run_id,
                    score.lead_event_id,
                    score.score,
                    score.model,
                    score.attempt_id,
                    _json(score.model_dump(mode="json")),
                    _now(),
                ),
            )

    def events_for_run(self, run_id: str) -> list[LeadEvent]:
        with self.connect() as conn:
            return [
                LeadEvent.model_validate_json(row["payload_json"])
                for row in conn.execute(
                    "SELECT payload_json FROM v2_lead_events WHERE run_id=? ORDER BY lead_event_id",
                    (run_id,),
                )
            ]

    def active_events_for_run(self, run_id: str) -> list[LeadEvent]:
        with self.connect() as conn:
            return [
                LeadEvent.model_validate_json(row["payload_json"])
                for row in conn.execute(
                    """SELECT e.payload_json
                    FROM v2_lead_events e
                    LEFT JOIN v2_event_merges m
                      ON m.run_id=e.run_id AND m.merged_event_id=e.lead_event_id
                    WHERE e.run_id=? AND e.record_status='valid'
                      AND m.merged_event_id IS NULL
                    ORDER BY e.lead_event_id""",
                    (run_id,),
                )
            ]

    def save_event_merge(self, run_id: str, merged_event_id: str, kept_event_id: str) -> None:
        if merged_event_id == kept_event_id:
            return
        with self.transaction() as conn:
            conn.execute(
                """INSERT INTO v2_event_merges(run_id, merged_event_id, kept_event_id, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(run_id, merged_event_id) DO UPDATE SET
                    kept_event_id=excluded.kept_event_id, created_at=excluded.created_at""",
                (run_id, merged_event_id, kept_event_id, _now()),
            )

    def organizations(self, organization_ids: set[str] | None = None) -> list[Organization]:
        with self.connect() as conn:
            rows = list(conn.execute("SELECT organization_id, payload_json FROM v2_organizations"))
        return [
            Organization.model_validate_json(row["payload_json"])
            for row in rows
            if organization_ids is None or row["organization_id"] in organization_ids
        ]

    def people(self, organization_id: str | None = None) -> list[Person]:
        sql = "SELECT payload_json FROM v2_people"
        params: tuple[object, ...] = ()
        if organization_id:
            sql += " WHERE organization_id=?"
            params = (organization_id,)
        sql += " ORDER BY person_id"
        with self.connect() as conn:
            return [Person.model_validate_json(row["payload_json"]) for row in conn.execute(sql, params)]

    def contacts_for_run(self, run_id: str) -> list[ContactCandidate]:
        with self.connect() as conn:
            return [
                ContactCandidate.model_validate_json(row["payload_json"])
                for row in conn.execute(
                    "SELECT payload_json FROM v2_contact_candidates WHERE run_id=? ORDER BY contact_candidate_id",
                    (run_id,),
                )
            ]

    def scores_for_run(self, run_id: str) -> list[LeadScore]:
        with self.connect() as conn:
            return [
                LeadScore.model_validate_json(row["payload_json"])
                for row in conn.execute(
                    "SELECT payload_json FROM v2_scores WHERE run_id=? ORDER BY lead_event_id",
                    (run_id,),
                )
            ]

    def save_company_profile(self, profile: CompanyProfile) -> None:
        now = _now()
        with self.transaction() as conn:
            conn.execute(
                """INSERT INTO v2_company_profiles(
                    run_id, company_id, anchor_lead_event_id, record_status,
                    payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, company_id) DO UPDATE SET
                    anchor_lead_event_id=excluded.anchor_lead_event_id,
                    record_status=excluded.record_status,
                    payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at""",
                (
                    profile.run_id,
                    profile.company_id,
                    profile.anchor_lead_event_id,
                    profile.record_status.value,
                    _json(profile.model_dump(mode="json")),
                    now,
                    now,
                ),
            )

    def company_profiles_for_run(self, run_id: str) -> list[CompanyProfile]:
        with self.connect() as conn:
            return [
                CompanyProfile.model_validate_json(row["payload_json"])
                for row in conn.execute(
                    "SELECT payload_json FROM v2_company_profiles WHERE run_id=? ORDER BY company_id",
                    (run_id,),
                )
            ]

    def resolve_company_profile_identity(
        self, domain: str, names: list[str]
    ) -> str | None:
        """Resolve a durable company ID from any prior domain/name alias."""
        normalized_domain = domain.strip().casefold()
        normalized_names = {normalize_text(value) for value in names if value.strip()}
        matches: set[str] = set()
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT company_id, payload_json FROM v2_company_profiles"
            ).fetchall()
        for row in rows:
            profile = CompanyProfile.model_validate_json(row["payload_json"])
            profile_names = {
                normalize_text(value)
                for value in [profile.canonical_name, *profile.aliases]
                if value.strip()
            }
            if (
                normalized_domain
                and profile.domain.strip().casefold() == normalized_domain
            ) or (normalized_names and profile_names & normalized_names):
                matches.add(str(row["company_id"]))
        if len(matches) > 1:
            raise ValueError(
                f"ambiguous prior company identity for domain={domain!r}, names={names!r}"
            )
        return next(iter(matches), None)

    def save_outreach_recipient(self, recipient: OutreachRecipient) -> None:
        now = _now()
        with self.transaction() as conn:
            conn.execute(
                """INSERT INTO v2_outreach_recipients(
                    run_id, recipient_id, company_id, person_id, rank,
                    primary_recipient, payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, recipient_id) DO UPDATE SET
                    company_id=excluded.company_id,
                    person_id=excluded.person_id,
                    rank=excluded.rank,
                    primary_recipient=excluded.primary_recipient,
                    payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at""",
                (
                    recipient.run_id,
                    recipient.recipient_id,
                    recipient.company_id,
                    recipient.person_id,
                    recipient.rank,
                    int(recipient.primary),
                    _json(recipient.model_dump(mode="json")),
                    now,
                    now,
                ),
            )

    def outreach_recipients_for_run(self, run_id: str) -> list[OutreachRecipient]:
        with self.connect() as conn:
            return [
                OutreachRecipient.model_validate_json(row["payload_json"])
                for row in conn.execute(
                    """SELECT payload_json FROM v2_outreach_recipients
                       WHERE run_id=? ORDER BY company_id, rank, recipient_id""",
                    (run_id,),
                )
            ]

    def prune_company_outreach(
        self,
        run_id: str,
        company_ids: set[str],
        recipient_ids: set[str],
    ) -> None:
        with self.transaction() as conn:
            if company_ids:
                placeholders = ", ".join("?" for _ in company_ids)
                conn.execute(
                    f"DELETE FROM v2_company_profiles WHERE run_id=? AND company_id NOT IN ({placeholders})",
                    (run_id, *sorted(company_ids)),
                )
            else:
                conn.execute(
                    "DELETE FROM v2_company_profiles WHERE run_id=?", (run_id,)
                )
            if recipient_ids:
                placeholders = ", ".join("?" for _ in recipient_ids)
                conn.execute(
                    f"DELETE FROM v2_outreach_recipients WHERE run_id=? AND recipient_id NOT IN ({placeholders})",
                    (run_id, *sorted(recipient_ids)),
                )
            else:
                conn.execute(
                    "DELETE FROM v2_outreach_recipients WHERE run_id=?", (run_id,)
                )

    def record_provider_attempt(
        self,
        *,
        attempt_id: str,
        run_id: str,
        stage: str,
        provider: str,
        target_type: str,
        target_id: str,
        status: str,
        billable: bool = False,
        token_usage: dict | None = None,
        request_artifact_path: str = "",
        response_artifact_path: str = "",
        error: dict | None = None,
        started_at: str | None = None,
        completed_at: str | None = None,
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                """INSERT INTO v2_provider_attempts(
                    attempt_id, run_id, stage, provider, target_type, target_id,
                    status, billable, token_usage_json, request_artifact_path,
                    response_artifact_path, error_json, started_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(attempt_id) DO UPDATE SET
                    status=excluded.status, billable=excluded.billable,
                    token_usage_json=excluded.token_usage_json,
                    response_artifact_path=excluded.response_artifact_path,
                    error_json=excluded.error_json, completed_at=excluded.completed_at""",
                (
                    attempt_id,
                    run_id,
                    stage,
                    provider,
                    target_type,
                    target_id,
                    status,
                    int(billable),
                    _json(token_usage or {}),
                    request_artifact_path,
                    response_artifact_path,
                    _json(error or {}),
                    started_at or _now(),
                    completed_at,
                ),
            )

    def next_provider_attempt_number(
        self,
        run_id: str,
        stage: str,
        target_type: str,
        target_id: str,
    ) -> int:
        with self.connect() as conn:
            row = conn.execute(
                """SELECT COUNT(*) AS count FROM v2_provider_attempts
                   WHERE run_id=? AND stage=? AND target_type=? AND target_id=?""",
                (run_id, stage, target_type, target_id),
            ).fetchone()
        return int(row["count"]) + 1

    def get_verification_cache(self, cache_key: str) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                """SELECT * FROM v2_verification_cache
                WHERE cache_key=? AND (expires_at IS NULL OR expires_at > ?)""",
                (cache_key, _now()),
            ).fetchone()

    def set_verification_cache(
        self,
        cache_key: str,
        kind: str,
        value: str,
        status: str,
        payload: dict,
        expires_at: str | None,
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                """INSERT INTO v2_verification_cache(
                    cache_key, kind, value, status, payload_json, checked_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    status=excluded.status, payload_json=excluded.payload_json,
                    checked_at=excluded.checked_at, expires_at=excluded.expires_at""",
                (cache_key, kind, value, status, _json(payload), _now(), expires_at),
            )

    def get_apollo_cache(self, cache_key: str) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                """SELECT * FROM v2_apollo_cache
                WHERE cache_key=? AND (expires_at IS NULL OR expires_at > ?)""",
                (cache_key, _now()),
            ).fetchone()

    def set_apollo_cache(
        self,
        cache_key: str,
        normalized_person: str,
        normalized_organization: str,
        status: str,
        *,
        billable: bool,
        payload: dict | None = None,
        error: dict | None = None,
        expires_at: str | None = None,
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                """INSERT INTO v2_apollo_cache(
                    cache_key, normalized_person, normalized_organization, status,
                    billable, payload_json, error_json, attempted_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    status=excluded.status, billable=excluded.billable,
                    payload_json=excluded.payload_json, error_json=excluded.error_json,
                    attempted_at=excluded.attempted_at, expires_at=excluded.expires_at""",
                (
                    cache_key,
                    normalized_person,
                    normalized_organization,
                    status,
                    int(billable),
                    _json(payload or {}),
                    _json(error or {}),
                    _now(),
                    expires_at,
                ),
            )

    def usage_summary(self, run_id: str) -> dict[str, int]:
        totals: dict[str, int] = {}
        with self.connect() as conn:
            rows = list(
                conn.execute(
                    "SELECT token_usage_json, billable FROM v2_provider_attempts WHERE run_id=?",
                    (run_id,),
                )
            )
        totals["provider_attempts"] = len(rows)
        totals["billable_attempts"] = sum(int(row["billable"]) for row in rows)
        for row in rows:
            usage = json.loads(row["token_usage_json"])
            for key, value in usage.items():
                if isinstance(value, (int, float)):
                    totals[key] = totals.get(key, 0) + int(value)
        return totals

    def artifacts_for_run(self, run_id: str) -> list[dict]:
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(
                """SELECT stage, kind, path, sha256, byte_count, created_at
                FROM v2_artifacts WHERE run_id=? ORDER BY created_at, path""",
                (run_id,),
            )]

    def upsert_model(self, table: str, key_column: str, key: str, model: BaseModel, **columns: object) -> None:
        allowed = {
            "v2_discovery_candidates",
            "v2_organizations",
            "v2_people",
            "v2_lead_events",
            "v2_contact_candidates",
        }
        if table not in allowed:
            raise ValueError(f"unsupported model table: {table}")
        payload = model.model_dump(mode="json")
        now = _now()
        values = {key_column: key, **columns, "payload_json": _json(payload)}
        table_columns = self._table_columns(table)
        if "created_at" in table_columns:
            values["created_at"] = now
        if "updated_at" in table_columns:
            values["updated_at"] = now
        names = list(values)
        updates = [name for name in names if name not in {key_column, "created_at"}]
        sql = (
            f"INSERT INTO {table} ({', '.join(names)}) VALUES ({', '.join('?' for _ in names)}) "
            f"ON CONFLICT({key_column}) DO UPDATE SET "
            + ", ".join(f"{name}=excluded.{name}" for name in updates)
        )
        with self.transaction() as conn:
            conn.execute(sql, [values[name] for name in names])

    def add_review(self, item: ReviewItem) -> None:
        with self.transaction() as conn:
            prior = conn.execute(
                "SELECT retry_count FROM v2_review_items WHERE review_id=?",
                (item.review_id,),
            ).fetchone()
            if prior and int(prior["retry_count"]) > item.retry_count:
                item = item.model_copy(
                    update={"retry_count": int(prior["retry_count"])}
                )
            payload = item.model_dump(mode="json")
            conn.execute(
                """INSERT INTO v2_review_items(
                    review_id, run_id, stage, record_type, record_id, reason_code,
                    state, retry_count, payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(review_id) DO UPDATE SET
                    state=excluded.state,
                    retry_count=excluded.retry_count,
                    payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at""",
                (
                    item.review_id,
                    item.run_id,
                    item.stage,
                    item.record_type,
                    item.record_id,
                    item.reason_code,
                    item.state,
                    item.retry_count,
                    _json(payload),
                    item.created_at.isoformat(),
                    item.updated_at.isoformat(),
                ),
            )

    def mark_review_retried(self, review_id: str) -> None:
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT payload_json, retry_count FROM v2_review_items WHERE review_id=?",
                (review_id,),
            ).fetchone()
            if not row:
                return
            item = ReviewItem.model_validate_json(row["payload_json"])
            retry_count = int(row["retry_count"]) + 1
            item = item.model_copy(
                update={"retry_count": retry_count, "updated_at": datetime.now(timezone.utc)}
            )
            conn.execute(
                """UPDATE v2_review_items
                   SET retry_count=?, payload_json=?, updated_at=? WHERE review_id=?""",
                (retry_count, _json(item.model_dump(mode="json")), _now(), review_id),
            )

    def resolve_review(self, review_id: str) -> None:
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT payload_json FROM v2_review_items WHERE review_id=?",
                (review_id,),
            ).fetchone()
            if not row:
                return
            item = ReviewItem.model_validate_json(row["payload_json"])
            item = item.model_copy(
                update={"state": "resolved", "updated_at": datetime.now(timezone.utc)}
            )
            conn.execute(
                """UPDATE v2_review_items SET state='resolved', payload_json=?, updated_at=?
                   WHERE review_id=?""",
                (_json(item.model_dump(mode="json")), _now(), review_id),
            )

    def eligible_reviews(
        self,
        stage: str | None = None,
        max_retries: int = 2,
        *,
        run_id: str | None = None,
    ) -> list[ReviewItem]:
        sql = "SELECT payload_json FROM v2_review_items WHERE state='open' AND retry_count < ?"
        params: list[object] = [max_retries]
        if stage:
            sql += " AND stage=?"
            params.append(stage)
        if run_id:
            sql += " AND run_id=?"
            params.append(run_id)
        sql += " ORDER BY created_at, review_id"
        with self.connect() as conn:
            return [ReviewItem.model_validate_json(row["payload_json"]) for row in conn.execute(sql, params)]

    def reviews_for_run(
        self, run_id: str, *, state: str | None = None
    ) -> list[ReviewItem]:
        sql = "SELECT payload_json FROM v2_review_items WHERE run_id=?"
        params: list[object] = [run_id]
        if state is not None:
            sql += " AND state=?"
            params.append(state)
        sql += " ORDER BY created_at, review_id"
        with self.connect() as conn:
            return [
                ReviewItem.model_validate_json(row["payload_json"])
                for row in conn.execute(sql, params)
            ]

    def record_artifact(
        self,
        run_id: str,
        stage: str,
        kind: str,
        path: str,
        sha256: str,
        byte_count: int,
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                """INSERT INTO v2_artifacts(run_id, stage, kind, path, sha256, byte_count, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, path) DO UPDATE SET
                    stage=excluded.stage, kind=excluded.kind, sha256=excluded.sha256,
                    byte_count=excluded.byte_count, created_at=excluded.created_at""",
                (run_id, stage, kind, path, sha256, byte_count, _now()),
            )

    def _table_columns(self, table: str) -> set[str]:
        with self.connect() as conn:
            return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
