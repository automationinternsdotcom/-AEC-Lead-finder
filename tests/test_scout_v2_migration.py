"""Local legacy migration is backed up, idempotent, and non-destructive."""
from __future__ import annotations

import csv
import hashlib
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scout"))

from v2.migration import LegacyMigrator  # noqa: E402
from v2.state import StateStore  # noqa: E402


def write_csv(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_migration_imports_history_and_dated_csvs_idempotently(tmp_path):
    db_path = tmp_path / "scout.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """CREATE TABLE articles(
                link TEXT PRIMARY KEY, business_name TEXT, event TEXT,
                date_posted TEXT, location TEXT, source_site TEXT
            )"""
        )
        conn.execute("CREATE TABLE rejected(link TEXT PRIMARY KEY)")
        conn.execute(
            "INSERT INTO articles VALUES (?, ?, ?, ?, ?, ?)",
            (
                "https://example.com/history",
                "Historical Acme",
                "Historical event",
                "2026-08-20",
                "Phoenix, Arizona",
                "https://example.com",
            ),
        )
        conn.execute("INSERT INTO rejected VALUES (?)", ("https://example.com/rejected",))
    day = tmp_path / "results" / "2026-08-28"
    raw_path = day / "raw_leads.csv"
    contacts_path = day / "contacts.csv"
    uncertain_path = day / "uncertain_leads.csv"
    write_csv(
        raw_path,
        [
            {
                "link": "https://example.com/lead",
                "business_name": "Acme Marketplace",
                "person": "",
                "event": "Opened a marketplace.",
                "date_posted": "2026-08-28",
                "location": "Phoenix, Arizona",
                "summary": "Acme opened.",
                "state": "Arizona",
                "source_site": "https://example.com",
                "aka": "Acme Market",
                "priority": "high",
                "property_type": "retail",
                "service_angle": "Strategic partner.",
                "filter_reason": "Opening.",
                "Decision_Makers": "Jane Manager — General Manager",
                "Employee_Count": "100 (company, 2026)",
                "Decision_Maker_Sources": "https://acme.example/team",
                "score": "0",
            }
        ],
    )
    write_csv(
        contacts_path,
        [
            {
                "business_name": "Acme Marketplace",
                "person": "Jane Manager",
                "title": "General Manager",
                "email": "jane@acme.example",
                "phone": "",
                "linkedin": "",
                "sources": "https://acme.example/team",
            }
        ],
    )
    write_csv(
        uncertain_path,
        [
            {
                "link": "https://example.com/uncertain",
                "business_name": "Uncertain lead",
                "event": "Potential opening",
                "date_posted": "2026-08-28",
                "source_site": "https://example.com",
            }
        ],
    )
    original_hashes = {path: digest(path) for path in (raw_path, contacts_path, uncertain_path)}
    migrator = LegacyMigrator(db_path, tmp_path / "results")
    preview = migrator.inventory()
    assert not preview.applied and not list(tmp_path.glob("*.backup-*"))

    report = migrator.apply()
    assert report.applied and Path(report.backup_path).exists()
    store = StateStore(db_path)
    legacy_run = next(run for run in report.synthetic_runs if run != report.synthetic_runs[0])
    assert len(store.events_for_run(legacy_run)) == 1
    assert store.scores_for_run(legacy_run)[0].score == 0
    assert len(store.contacts_for_run(legacy_run)) == 1
    assert len(store.reviews_for_run(legacy_run)) == 1
    assert all(digest(path) == original_hashes[path] for path in original_hashes)

    with store.connect() as conn:
        before = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "v2_discovery_candidates",
                "v2_lead_events",
                "v2_contact_candidates",
                "v2_review_items",
            )
        }
    migrator.apply()
    with StateStore(db_path).connect() as conn:
        after = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in before
        }
    assert after == before
