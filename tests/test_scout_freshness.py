"""Daily freshness filtering for curated website discovery."""
from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scout"))

import stages  # noqa: E402


def test_filter_since_excludes_archives_and_undated_links():
    entries = [
        {"link": "https://example.test/new", "published_iso": "2026-08-28"},
        {"link": "https://example.test/cutoff", "published_iso": "2026-08-27"},
        {"link": "https://example.test/old", "published_iso": "2026-08-26"},
        {"link": "https://example.test/unknown", "published_iso": ""},
    ]

    assert stages.filter_since(entries, "2026-08-27") == entries[:2]
