"""SQLite state for the GPS-style AEC scout pipeline."""
from __future__ import annotations

import sqlite3

import config

ARTICLE_COLUMNS = [
    "link",
    "business_name",
    "person",
    "event",
    "date_posted",
    "location",
    "summary",
    "state",
    "source_site",
    "aka",
    "priority",
    "property_type",
    "service_angle",
    "filter_reason",
]


def connect():
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = connect()
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS websites (
        url TEXT PRIMARY KEY, state TEXT, rss_url TEXT, last_visited TEXT)""")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS articles (
        link TEXT PRIMARY KEY, business_name TEXT, person TEXT, event TEXT,
        date_posted TEXT, location TEXT, summary TEXT, state TEXT, source_site TEXT,
        aka TEXT, priority TEXT, property_type TEXT, service_angle TEXT,
        filter_reason TEXT)"""
    )
    conn.execute("CREATE TABLE IF NOT EXISTS rejected (link TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()


def seed_from_sources(sources: list[dict]) -> None:
    for source in sources:
        add_site(source["url"], "Arizona")


def get_sites(state):
    conn = connect()
    rows = conn.execute(
        "SELECT url, rss_url FROM websites WHERE state = ?", (state,)
    ).fetchall()
    conn.close()
    return rows


def _write(sql, *params):
    conn = connect()
    conn.execute(sql, params)
    conn.commit()
    conn.close()


def add_site(url, state):
    _write("INSERT OR IGNORE INTO websites (url, state) VALUES (?, ?)", url, state)


def set_rss(url, rss_url):
    _write("UPDATE websites SET rss_url = ? WHERE url = ?", rss_url, url)


def mark_visited(url, ts):
    _write("UPDATE websites SET last_visited = ? WHERE url = ?", ts, url)


def mark_rejected(link):
    _write("INSERT OR IGNORE INTO rejected (link) VALUES (?)", link)


def insert_article(row):
    row = {k: v for k, v in row.items() if k in ARTICLE_COLUMNS}
    if not row.get("link"):
        return
    cols = ", ".join(row.keys())
    placeholders = ", ".join("?" for _ in row)
    _write(
        f"INSERT OR IGNORE INTO articles ({cols}) VALUES ({placeholders})",
        *row.values(),
    )


def all_links():
    """Every link ever judged: kept leads and rejects alike."""
    conn = connect()
    rows = conn.execute(
        "SELECT link FROM articles UNION SELECT link FROM rejected"
    ).fetchall()
    conn.close()
    return {row["link"] for row in rows}


if __name__ == "__main__":
    import tempfile
    from datetime import datetime, timezone

    config.DB_PATH = tempfile.mktemp(suffix=".db")
    init_db()
    seed_from_sources([{"url": "https://example.com"}])
    assert len(get_sites("Arizona")) == 1
    add_site("https://example.org", "Arizona")
    set_rss("https://example.com", "https://example.com/rss")
    mark_visited("https://example.com", datetime.now(timezone.utc).isoformat())
    insert_article({"link": "https://example.com/a", "business_name": "Acme", "bogus": "x"})
    mark_rejected("https://example.com/r")
    assert {"https://example.com/a", "https://example.com/r"} <= all_links()
    conn = connect()
    cols = [r[1] for r in conn.execute("PRAGMA table_info(articles)")]
    assert "bogus" not in cols
    conn.close()
    print("db.py self-check OK")
