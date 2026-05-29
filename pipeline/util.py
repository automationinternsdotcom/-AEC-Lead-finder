from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx
import trafilatura

from pipeline.config import Settings


TRACKING_PREFIXES = ("utm_", "mc_")
TRACKING_KEYS = {"fbclid", "gclid", "yclid", "msclkid", "ref"}
USER_AGENT = "Mozilla/5.0 (compatible; AetherLeadBot/1.0)"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def json_log(event: str, **fields: object) -> None:
    print(json.dumps({"event": event, **fields}, default=str))


def make_http_client(settings: Settings) -> httpx.Client:
    return httpx.Client(
        timeout=settings.http_timeout_sec,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    )


def canonicalize_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"unsupported url scheme: {parsed.scheme}")

    kept_query = sorted(
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not (key.startswith(TRACKING_PREFIXES) or key in TRACKING_KEYS)
    )
    host = (parsed.hostname or "").lower()
    netloc = host + (f":{parsed.port}" if parsed.port else "")
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme, netloc, path, urlencode(kept_query), ""))


def stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_key(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(value.casefold().strip().split())


def same_domain(url: str, allowed_root: str) -> bool:
    return (urlsplit(url).hostname or "").lower() == (urlsplit(allowed_root).hostname or "").lower()


def allowed_by_path(url: str, allowed_paths: list[str]) -> bool:
    if not allowed_paths:
        return True
    path = urlsplit(url).path or "/"
    return any(path.startswith(allowed_path.rstrip("/") or "/") for allowed_path in allowed_paths)


def clean_html(html: str, url: str) -> str:
    text = trafilatura.extract(
        html,
        url=url,
        include_comments=False,
        include_tables=False,
        with_metadata=False,
    )
    return text.strip() if text else ""


class LinkParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.links: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self.links.add(urljoin(self.base_url, href))


def extract_links(html: str, base_url: str) -> list[str]:
    parser = LinkParser(base_url)
    parser.feed(html)
    out: list[str] = []
    for link in parser.links:
        try:
            out.append(canonicalize_url(link))
        except ValueError:
            continue
    return sorted(set(out))

