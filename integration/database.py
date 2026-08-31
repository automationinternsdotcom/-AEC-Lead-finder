"""Durable Mac-local SQLite queue, mappings, and suppression state."""

from __future__ import annotations

import hashlib
import json
import random
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .models import MappingRecord, SuppressionReason, WorkItem

SCHEMA = """
CREATE TABLE IF NOT EXISTS webhook_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  provider TEXT NOT NULL CHECK (provider IN ('warmy', 'pipedrive')),
  provider_event_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  payload_hash TEXT NOT NULL,
  payload TEXT NOT NULL DEFAULT '{}',
  signature_valid INTEGER NOT NULL DEFAULT 0,
  received_at TEXT NOT NULL,
  UNIQUE (provider, provider_event_id)
);

CREATE TABLE IF NOT EXISTS work_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  dedupe_key TEXT NOT NULL UNIQUE,
  payload TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'running', 'completed', 'dead_letter')),
  attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
  run_after TEXT NOT NULL,
  lease_owner TEXT,
  lease_until TEXT,
  last_error TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  completed_at TEXT
);

CREATE INDEX IF NOT EXISTS work_items_ready_idx
  ON work_items (status, run_after, id);

CREATE TABLE IF NOT EXISTS lead_mappings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  outreach_id TEXT NOT NULL UNIQUE,
  source_contact_candidate_id TEXT NOT NULL DEFAULT '',
  source_verification_status TEXT NOT NULL DEFAULT 'unknown',
  source_verification_reason TEXT NOT NULL DEFAULT '',
  source_provider TEXT NOT NULL DEFAULT '',
  email TEXT NOT NULL,
  lead_event_id TEXT NOT NULL DEFAULT '',
  organization_id TEXT NOT NULL DEFAULT '',
  person_id TEXT NOT NULL DEFAULT '',
  pipedrive_organization_id INTEGER,
  pipedrive_person_id INTEGER,
  pipedrive_lead_id TEXT UNIQUE,
  pipedrive_deal_id INTEGER UNIQUE,
  warmy_prospect_id TEXT,
  warmy_campaign_id TEXT,
  warmy_mailbox_id TEXT,
  gmail_thread_id TEXT,
  verification_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (verification_status IN ('pending', 'valid', 'invalid', 'catch_all', 'unknown')),
  reply_disposition TEXT
    CHECK (reply_disposition IS NULL OR reply_disposition IN
      ('pending_review', 'positive', 'negative', 'out_of_office', 'unsubscribe', 'other')),
  reply_received_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS lead_mappings_email_idx ON lead_mappings (email);
CREATE INDEX IF NOT EXISTS lead_mappings_warmy_prospect_idx
  ON lead_mappings (warmy_prospect_id);

CREATE TABLE IF NOT EXISTS suppressions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT NOT NULL UNIQUE,
  reason TEXT NOT NULL CHECK (reason IN
    ('bounce', 'unsubscribe', 'negative_reply', 'invalid', 'manual', 'complaint')),
  source TEXT NOT NULL,
  external_event_id TEXT NOT NULL DEFAULT '',
  suppressed_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS unsubscribe_tokens (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  token_hash TEXT NOT NULL UNIQUE,
  email TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS provider_operations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  provider TEXT NOT NULL,
  operation_key TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'started'
    CHECK (status IN ('started', 'completed', 'failed')),
  request_payload TEXT NOT NULL DEFAULT '{}',
  response_payload TEXT NOT NULL DEFAULT '{}',
  external_id TEXT NOT NULL DEFAULT '',
  last_error TEXT NOT NULL DEFAULT '',
  lease_owner TEXT,
  lease_until TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  completed_at TEXT,
  UNIQUE (provider, operation_key)
);

CREATE TABLE IF NOT EXISTS app_state (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL DEFAULT '{}',
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS integration_runs (
  run_id TEXT PRIMARY KEY,
  source_file TEXT NOT NULL DEFAULT '',
  discovered_count INTEGER NOT NULL DEFAULT 0,
  enqueued_count INTEGER NOT NULL DEFAULT 0,
  started_at TEXT NOT NULL,
  completed_at TEXT,
  metadata TEXT NOT NULL DEFAULT '{}'
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _timestamp(value: datetime | None) -> str:
    if value is None:
        return _now()
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), default=str)


class Database:
    """Small transactional state store intended for one persistent Mac.

    Every operation opens a short-lived connection. WAL mode plus an immediate
    transaction for work claims allows the webhook server and worker process to
    safely share the same file.
    """

    def __init__(self, path: str | Path):
        if not str(path).strip():
            raise ValueError("AETHER_SALES_DB_PATH is required")
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _open(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        self._migrate(conn)
        try:
            self.path.chmod(0o600)
        except OSError:
            pass
        return conn

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """Apply additive migrations for databases created by earlier previews."""
        additions = {
            "lead_mappings": {
                "source_verification_status": "TEXT NOT NULL DEFAULT 'unknown'",
                "source_verification_reason": "TEXT NOT NULL DEFAULT ''",
                "source_provider": "TEXT NOT NULL DEFAULT ''",
            },
            "provider_operations": {
                "lease_owner": "TEXT",
                "lease_until": "TEXT",
            },
        }
        for table, columns in additions.items():
            existing = {
                str(row["name"])
                for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            for name, definition in columns.items():
                if name not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    @contextmanager
    def connection(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        conn = self._open()
        try:
            conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def healthcheck(self) -> bool:
        with self.connection() as conn:
            return conn.execute("SELECT 1 AS ok").fetchone()["ok"] == 1

    def enqueue_work(
        self,
        kind: str,
        dedupe_key: str,
        payload: dict[str, Any],
        *,
        run_after: datetime | None = None,
    ) -> bool:
        now = _now()
        with self.connection() as conn:
            cursor = conn.execute(
                """INSERT OR IGNORE INTO work_items(
                       kind, dedupe_key, payload, run_after, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?)""",
                (kind, dedupe_key, _json(payload), _timestamp(run_after), now, now),
            )
        return cursor.rowcount > 0

    def accept_webhook(
        self,
        provider: str,
        event_id: str,
        event_type: str,
        payload: dict[str, Any],
        payload_hash: str,
        *,
        signature_valid: bool,
    ) -> bool:
        now = _now()
        with self.connection(immediate=True) as conn:
            cursor = conn.execute(
                """INSERT OR IGNORE INTO webhook_events(
                       provider, provider_event_id, event_type, payload_hash,
                       payload, signature_valid, received_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    provider,
                    event_id,
                    event_type,
                    payload_hash,
                    _json(payload),
                    int(signature_valid),
                    now,
                ),
            )
            if cursor.rowcount == 0:
                return False
            conn.execute(
                """INSERT OR IGNORE INTO work_items(
                       kind, dedupe_key, payload, run_after, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    f"{provider}.event",
                    f"{provider}:event:{event_id}",
                    _json({"event_id": event_id, **payload, "event_type": event_type}),
                    now,
                    now,
                    now,
                ),
            )
        return True

    def claim_work(self, owner: str, limit: int, lease_seconds: int) -> list[WorkItem]:
        now = datetime.now(UTC)
        now_text = now.isoformat()
        lease_until = (now + timedelta(seconds=max(30, lease_seconds))).isoformat()
        with self.connection(immediate=True) as conn:
            rows = conn.execute(
                """SELECT id FROM work_items
                   WHERE (status='pending' AND run_after <= ?)
                      OR (status='running' AND lease_until < ?)
                   ORDER BY run_after, id
                   LIMIT ?""",
                (now_text, now_text, max(0, min(limit, 100))),
            ).fetchall()
            ids = [row["id"] for row in rows]
            if not ids:
                return []
            placeholders = ",".join("?" for _ in ids)
            conn.execute(
                f"""UPDATE work_items
                    SET status='running', attempt_count=attempt_count + 1,
                        lease_owner=?, lease_until=?, updated_at=?
                    WHERE id IN ({placeholders})""",
                (owner, lease_until, now_text, *ids),
            )
            claimed = conn.execute(
                f"""SELECT id, kind, dedupe_key, payload, attempt_count
                    FROM work_items WHERE id IN ({placeholders})
                    ORDER BY run_after, id""",
                ids,
            ).fetchall()
        return [
            WorkItem(
                id=row["id"],
                kind=row["kind"],
                dedupe_key=row["dedupe_key"],
                payload=json.loads(row["payload"]),
                attempt_count=row["attempt_count"],
            )
            for row in claimed
        ]

    def complete_work(self, item_id: int, owner: str) -> None:
        now = _now()
        with self.connection() as conn:
            conn.execute(
                """UPDATE work_items
                   SET status='completed', completed_at=?, lease_owner=NULL,
                       lease_until=NULL, updated_at=?
                   WHERE id=? AND lease_owner=? AND status='running'""",
                (now, now, item_id, owner),
            )

    def retry_work(
        self,
        item: WorkItem,
        owner: str,
        error: Exception,
        *,
        max_attempts: int,
    ) -> bool:
        terminal = item.attempt_count >= max_attempts
        delay = min(3600, 2 ** min(item.attempt_count, 10)) + random.randint(0, 15)
        now = datetime.now(UTC)
        with self.connection() as conn:
            conn.execute(
                """UPDATE work_items
                   SET status=?, run_after=?, lease_owner=NULL, lease_until=NULL,
                       last_error=?, updated_at=?
                   WHERE id=? AND lease_owner=?""",
                (
                    "dead_letter" if terminal else "pending",
                    (now + timedelta(seconds=delay)).isoformat(),
                    str(error)[:1000],
                    now.isoformat(),
                    item.id,
                    owner,
                ),
            )
        return terminal

    def defer_work(
        self,
        item: WorkItem,
        owner: str,
        error: Exception,
        *,
        delay_seconds: int = 300,
    ) -> None:
        """Release an activation-blocked item without spending its retry budget."""
        now = datetime.now(UTC)
        with self.connection() as conn:
            conn.execute(
                """UPDATE work_items
                   SET status='pending', attempt_count=MAX(0, attempt_count - 1),
                       run_after=?, lease_owner=NULL, lease_until=NULL,
                       last_error=?, updated_at=?
                   WHERE id=? AND lease_owner=?""",
                (
                    (now + timedelta(seconds=max(30, delay_seconds))).isoformat(),
                    str(error)[:1000],
                    now.isoformat(),
                    item.id,
                    owner,
                ),
            )

    def replay_dead_letters(self) -> int:
        now = _now()
        with self.connection(immediate=True) as conn:
            cursor = conn.execute(
                """UPDATE work_items
                   SET status='pending', attempt_count=0, run_after=?,
                       lease_owner=NULL, lease_until=NULL, last_error='',
                       completed_at=NULL, updated_at=?
                   WHERE status='dead_letter'""",
                (now, now),
            )
        return cursor.rowcount

    def upsert_mapping(self, mapping: MappingRecord) -> None:
        data = mapping.model_dump(mode="json")
        now = _now()
        with self.connection() as conn:
            conn.execute(
                """INSERT INTO lead_mappings(
                       outreach_id, source_contact_candidate_id,
                       source_verification_status, source_verification_reason,
                       source_provider, email, lead_event_id,
                       organization_id, person_id, pipedrive_organization_id,
                       pipedrive_person_id,
                       pipedrive_lead_id, pipedrive_deal_id, warmy_prospect_id,
                       warmy_campaign_id, warmy_mailbox_id, gmail_thread_id,
                       verification_status, reply_disposition, reply_received_at,
                       created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(outreach_id) DO UPDATE SET
                       source_contact_candidate_id=excluded.source_contact_candidate_id,
                       source_verification_status=excluded.source_verification_status,
                       source_verification_reason=excluded.source_verification_reason,
                       source_provider=excluded.source_provider,
                       email=excluded.email,
                       lead_event_id=excluded.lead_event_id,
                       organization_id=excluded.organization_id,
                       person_id=excluded.person_id,
                       pipedrive_organization_id=COALESCE(
                           excluded.pipedrive_organization_id,
                           lead_mappings.pipedrive_organization_id),
                       pipedrive_person_id=COALESCE(
                           excluded.pipedrive_person_id,
                           lead_mappings.pipedrive_person_id),
                       pipedrive_lead_id=COALESCE(
                           excluded.pipedrive_lead_id,
                           lead_mappings.pipedrive_lead_id),
                       pipedrive_deal_id=COALESCE(
                           excluded.pipedrive_deal_id,
                           lead_mappings.pipedrive_deal_id),
                       warmy_prospect_id=COALESCE(
                           excluded.warmy_prospect_id,
                           lead_mappings.warmy_prospect_id),
                       warmy_campaign_id=COALESCE(
                           excluded.warmy_campaign_id,
                           lead_mappings.warmy_campaign_id),
                       warmy_mailbox_id=COALESCE(
                           excluded.warmy_mailbox_id,
                           lead_mappings.warmy_mailbox_id),
                       gmail_thread_id=COALESCE(
                           excluded.gmail_thread_id,
                           lead_mappings.gmail_thread_id),
                       verification_status=excluded.verification_status,
                       reply_disposition=COALESCE(
                           excluded.reply_disposition,
                           lead_mappings.reply_disposition),
                       reply_received_at=COALESCE(
                           excluded.reply_received_at,
                           lead_mappings.reply_received_at),
                       updated_at=excluded.updated_at""",
                (
                    data["outreach_id"],
                    data["source_contact_candidate_id"],
                    data["source_verification_status"],
                    data["source_verification_reason"],
                    data["source_provider"],
                    data["email"],
                    data["lead_event_id"],
                    data["organization_id"],
                    data["person_id"],
                    data["pipedrive_organization_id"],
                    data["pipedrive_person_id"],
                    data["pipedrive_lead_id"],
                    data["pipedrive_deal_id"],
                    data["warmy_prospect_id"],
                    data["warmy_campaign_id"],
                    data["warmy_mailbox_id"],
                    data["gmail_thread_id"],
                    data["verification_status"],
                    data["reply_disposition"],
                    data["reply_received_at"],
                    data["created_at"],
                    now,
                ),
            )

    def get_mapping(self, **lookup: Any) -> MappingRecord | None:
        allowed = {
            "outreach_id",
            "source_contact_candidate_id",
            "email",
            "warmy_prospect_id",
            "pipedrive_lead_id",
            "pipedrive_deal_id",
        }
        if len(lookup) != 1 or next(iter(lookup)) not in allowed:
            raise ValueError("exactly one supported mapping lookup is required")
        column, value = next(iter(lookup.items()))
        with self.connection() as conn:
            row = conn.execute(
                f"SELECT * FROM lead_mappings WHERE {column}=? "
                "ORDER BY updated_at DESC LIMIT 1",
                (value,),
            ).fetchone()
        return MappingRecord.model_validate(dict(row)) if row else None

    def get_mappings_by_email(self, email: str) -> list[MappingRecord]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM lead_mappings WHERE email=? ORDER BY updated_at DESC",
                (email.strip().casefold(),),
            ).fetchall()
        return [MappingRecord.model_validate(dict(row)) for row in rows]

    def update_mapping(self, outreach_id: str, **fields: Any) -> None:
        allowed = {
            "source_contact_candidate_id",
            "source_verification_status",
            "source_verification_reason",
            "source_provider",
            "email",
            "pipedrive_organization_id",
            "pipedrive_person_id",
            "pipedrive_lead_id",
            "pipedrive_deal_id",
            "warmy_prospect_id",
            "warmy_campaign_id",
            "warmy_mailbox_id",
            "gmail_thread_id",
            "verification_status",
            "reply_disposition",
            "reply_received_at",
        }
        bad = set(fields) - allowed
        if bad or not fields:
            raise ValueError(f"unsupported mapping fields: {sorted(bad)}")
        assignments = ", ".join(f"{key}=?" for key in fields)
        values = [
            value.value if hasattr(value, "value") else value
            for value in fields.values()
        ]
        with self.connection() as conn:
            conn.execute(
                f"UPDATE lead_mappings SET {assignments}, updated_at=? "
                "WHERE outreach_id=?",
                (*values, _now(), outreach_id),
            )

    def suppress(
        self,
        email: str,
        reason: SuppressionReason | str,
        source: str,
        *,
        external_event_id: str = "",
    ) -> bool:
        normalized = email.strip().casefold()
        now = _now()
        reason_value = (
            reason.value if isinstance(reason, SuppressionReason) else str(reason)
        )
        with self.connection(immediate=True) as conn:
            exists = conn.execute(
                "SELECT 1 FROM suppressions WHERE email=?", (normalized,)
            ).fetchone()
            conn.execute(
                """INSERT INTO suppressions(
                       email, reason, source, external_event_id, suppressed_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(email) DO UPDATE SET
                       reason=excluded.reason,
                       source=excluded.source,
                       external_event_id=CASE
                         WHEN excluded.external_event_id <> ''
                         THEN excluded.external_event_id
                         ELSE suppressions.external_event_id END,
                       updated_at=excluded.updated_at""",
                (normalized, reason_value, source, external_event_id, now, now),
            )
        return exists is None

    def is_suppressed(self, email: str) -> bool:
        with self.connection() as conn:
            return (
                conn.execute(
                    "SELECT 1 FROM suppressions WHERE email=?",
                    (email.strip().casefold(),),
                ).fetchone()
                is not None
            )

    def store_unsubscribe_token(self, token_id: str, email: str) -> None:
        token_hash = hashlib.sha256(token_id.encode()).hexdigest()
        now = _now()
        with self.connection() as conn:
            conn.execute(
                """INSERT INTO unsubscribe_tokens(token_hash, email, created_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(email) DO UPDATE SET token_hash=excluded.token_hash""",
                (token_hash, email.strip().casefold(), now),
            )

    def resolve_unsubscribe_token(self, token_id: str) -> str | None:
        token_hash = hashlib.sha256(token_id.encode()).hexdigest()
        with self.connection() as conn:
            row = conn.execute(
                "SELECT email FROM unsubscribe_tokens WHERE token_hash=?",
                (token_hash,),
            ).fetchone()
        return row["email"] if row else None

    def claim_operation(
        self,
        provider: str,
        operation_key: str,
        payload: dict,
        *,
        owner: str = "",
        lease_seconds: int = 300,
    ) -> bool:
        now_value = datetime.now(UTC)
        now = now_value.isoformat()
        lease_until = (
            now_value + timedelta(seconds=max(30, lease_seconds))
        ).isoformat()
        with self.connection(immediate=True) as conn:
            row = conn.execute(
                """SELECT status, lease_until FROM provider_operations
                   WHERE provider=? AND operation_key=?""",
                (provider, operation_key),
            ).fetchone()
            active = (
                row
                and row["status"] == "started"
                and row["lease_until"]
                and row["lease_until"] >= now
            )
            if row and (row["status"] == "completed" or active):
                return False
            if row:
                conn.execute(
                    """UPDATE provider_operations
                       SET status='started', request_payload=?, last_error='',
                           lease_owner=?, lease_until=?, updated_at=?, completed_at=NULL
                       WHERE provider=? AND operation_key=?""",
                    (
                        _json(payload),
                        owner,
                        lease_until,
                        now,
                        provider,
                        operation_key,
                    ),
                )
            else:
                conn.execute(
                    """INSERT INTO provider_operations(
                           provider, operation_key, request_payload,
                           lease_owner, lease_until, created_at, updated_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        provider,
                        operation_key,
                        _json(payload),
                        owner,
                        lease_until,
                        now,
                        now,
                    ),
                )
        return True

    def get_operation(self, provider: str, operation_key: str) -> dict | None:
        with self.connection() as conn:
            row = conn.execute(
                """SELECT status, external_id, response_payload, last_error
                   FROM provider_operations WHERE provider=? AND operation_key=?""",
                (provider, operation_key),
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["response_payload"] = json.loads(result["response_payload"])
        return result

    def complete_operation(
        self,
        provider: str,
        operation_key: str,
        response: dict,
        *,
        external_id: str = "",
    ) -> None:
        now = _now()
        with self.connection() as conn:
            conn.execute(
                """UPDATE provider_operations
                   SET status='completed', response_payload=?, external_id=?,
                       lease_owner=NULL, lease_until=NULL, completed_at=?, updated_at=?
                   WHERE provider=? AND operation_key=?""",
                (_json(response), external_id, now, now, provider, operation_key),
            )

    def fail_operation(
        self, provider: str, operation_key: str, error: Exception
    ) -> None:
        with self.connection() as conn:
            conn.execute(
                """UPDATE provider_operations
                   SET status='failed', last_error=?, updated_at=?
                       , lease_owner=NULL, lease_until=NULL
                   WHERE provider=? AND operation_key=?""",
                (str(error)[:1000], _now(), provider, operation_key),
            )

    def get_state(self, key: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT value FROM app_state WHERE key=?", (key,)
            ).fetchone()
        return json.loads(row["value"]) if row else None

    def set_state(self, key: str, value: dict[str, Any]) -> None:
        now = _now()
        with self.connection() as conn:
            conn.execute(
                """INSERT INTO app_state(key, value, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET
                       value=excluded.value, updated_at=excluded.updated_at""",
                (key, _json(value), now),
            )

    def record_run(
        self, run_id: str, source_file: str, discovered_count: int, enqueued_count: int
    ) -> None:
        now = _now()
        with self.connection() as conn:
            conn.execute(
                """INSERT INTO integration_runs(
                       run_id, source_file, discovered_count, enqueued_count,
                       started_at, completed_at
                   ) VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(run_id) DO UPDATE SET
                       source_file=excluded.source_file,
                       discovered_count=excluded.discovered_count,
                       enqueued_count=excluded.enqueued_count,
                       completed_at=excluded.completed_at""",
                (run_id, source_file, discovered_count, enqueued_count, now, now),
            )
