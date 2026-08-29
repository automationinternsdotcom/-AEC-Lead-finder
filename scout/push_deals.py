"""Push daily article leads to Pipedrive Deals in Aether's Pipeline."""
from __future__ import annotations

import csv
import html
import os
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

import httpx

try:
    from . import config
except ImportError:  # pragma: no cover - production script execution path
    import config


class PipedriveError(RuntimeError):
    """Hard-fail Pipedrive auth, validation, or server errors."""


class PipedriveDealClient:
    def __init__(self, *, transport: httpx.BaseTransport | None = None):
        self._http = httpx.Client(
            base_url=f"https://{config.PIPEDRIVE_DOMAIN}.pipedrive.com/api/",
            timeout=30,
            headers={
                "x-api-token": config.PIPEDRIVE_API_TOKEN,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            transport=transport,
        )

    def __enter__(self) -> "PipedriveDealClient":
        return self

    def __exit__(self, *exc) -> None:
        self._http.close()

    def _req(self, method: str, path: str, **kwargs) -> dict:
        response = self._http.request(method, path, **kwargs)
        if response.status_code in (401, 403):
            raise PipedriveError(f"auth failed: {response.status_code} on {path}")
        response.raise_for_status()
        payload = response.json()
        if payload.get("success") is False:
            raise PipedriveError(
                f"{method} {path} success:false - "
                f"{payload.get('error') or payload.get('error_info') or payload}"
            )
        data = payload.get("data")
        return data if isinstance(data, dict) else {"items": data or []}

    def _post_id(self, path: str, payload: dict) -> int:
        return int(self._req("POST", path, json=payload)["id"])

    def search_id(self, path: str, **params) -> int | None:
        data = self._req("GET", f"{path}/search", params=params)
        items = data.get("items") if isinstance(data, dict) else None
        if not items:
            return None
        item = items[0].get("item") or items[0]
        return int(item["id"])

    def find_deal_by_article_url(self, article_url: str) -> int | None:
        term = article_url[:100]
        try:
            data = self._req(
                "GET",
                "v2/deals/search",
                params={"term": term, "fields": "custom_fields", "limit": 10},
            )
        except httpx.HTTPStatusError as err:
            if err.response.status_code == 400:
                return None
            raise

        field = config.PIPEDRIVE_FIELD_ARTICLE_URL
        needle = article_url[:255]
        for hit in data.get("items", []):
            item = hit.get("item") or hit
            custom_fields = item.get("custom_fields") or {}
            if isinstance(custom_fields, dict):
                value = custom_fields.get(field)
                if value == needle or (isinstance(value, dict) and value.get("value") == needle):
                    return int(item["id"])
                continue
            for value in custom_fields:
                if value == needle or (isinstance(value, dict) and value.get("value") == needle):
                    return int(item["id"])
        return None

    def create_org(self, row: dict) -> int:
        existing = self.search_id("v2/organizations", term=row["business_name"], fields="name", exact_match=True, limit=1)
        if existing is not None:
            return existing
        payload = {"name": row["business_name"]}
        if row.get("location"):
            payload["address"] = row["location"]
        return self._post_id("v2/organizations", payload)

    def create_person(self, contact: dict, org_id: int) -> int | None:
        name = clean(contact.get("person"))
        if not name:
            return None
        email = clean(contact.get("email"))
        phone = clean(contact.get("phone"))
        if email:
            existing = self.search_id("v2/persons", term=email, fields="email", exact_match=True, limit=1)
            if existing is not None:
                return existing
        payload = {"name": name, "org_id": org_id}
        if email:
            payload["emails"] = [{"value": email, "primary": True, "label": "work"}]
        if phone:
            payload["phones"] = [{"value": phone, "primary": True, "label": "work"}]
        return self._post_id("v2/persons", payload)

    def create_deal(self, row: dict, org_id: int, person_id: int | None) -> int:
        return self._post_id("v2/deals", article_deal_payload(row, org_id, person_id))

    def add_note(self, deal_id: int, row: dict, contact: dict | None) -> None:
        self._req("POST", "v1/notes", json={
            "deal_id": deal_id,
            "content": note_body(row, contact),
            "pinned_to_deal_flag": 1,
        })


def clean(value: str | None) -> str:
    return " ".join(str(value or "").split())


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as file:
        return list(csv.DictReader(file))


def contacts_by_business(path: Path) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in read_csv(path):
        grouped[row.get("business_name", "")].append(row)
    return grouped


def best_contact(row: dict, grouped_contacts: dict[str, list[dict]]) -> dict | None:
    contacts = grouped_contacts.get(row.get("business_name", ""), [])
    if not contacts and row.get("person"):
        return {"person": row.get("person", ""), "title": "", "email": "", "phone": ""}
    reachable = [c for c in contacts if clean(c.get("email")) or clean(c.get("phone"))]
    named = [c for c in contacts if clean(c.get("person"))]
    return (reachable or named or contacts or [None])[0]


def deal_title(row: dict) -> str:
    title = clean(f"Article Lead: {row.get('business_name') or row.get('event') or row.get('link')}")
    return title[:255]


def article_deal_payload(row: dict, org_id: int, person_id: int | None) -> dict:
    payload = {
        "title": deal_title(row),
        "org_id": org_id,
        "pipeline_id": config.PIPEDRIVE_ARTICLE_DEAL_PIPELINE_ID,
        "stage_id": config.PIPEDRIVE_ARTICLE_DEAL_STAGE_ID,
        "custom_fields": {
            config.PIPEDRIVE_FIELD_ARTICLE_URL: clean(row.get("link"))[:255],
        },
    }
    if person_id is not None:
        payload["person_id"] = person_id
    return payload


def note_line(label: str, value: str | None) -> str:
    return f"<b>{html.escape(label)}:</b> {html.escape(clean(value) or 'Not found')}"


def note_body(row: dict, contact: dict | None) -> str:
    lines = [
        "<b>Aether article lead intake (automated)</b>",
        note_line("Article", row.get("link")),
        note_line("Business", row.get("business_name")),
        note_line("Event", row.get("event")),
        note_line("Date posted", row.get("date_posted")),
        note_line("Location", row.get("location")),
        note_line("Priority", row.get("priority")),
        note_line("Score", row.get("score")),
        note_line("Property type", row.get("property_type")),
        note_line("Summary", row.get("summary")),
        note_line("Filter reason", row.get("filter_reason")),
        note_line("Aether angle", row.get("service_angle")),
    ]
    if contact:
        lines.extend([
            note_line("Contact", contact.get("person")),
            note_line("Contact title", contact.get("title")),
            note_line("Contact email", contact.get("email")),
            note_line("Contact phone", contact.get("phone")),
        ])
    return "<br>".join(lines)


def validate_config() -> None:
    missing = [
        name for name, value in {
            "PIPEDRIVE_API_TOKEN": config.PIPEDRIVE_API_TOKEN,
            "PIPEDRIVE_DOMAIN": config.PIPEDRIVE_DOMAIN,
            "PIPEDRIVE_FIELD_ARTICLE_URL": config.PIPEDRIVE_FIELD_ARTICLE_URL,
        }.items()
        if not value
    ]
    if missing:
        raise RuntimeError("Missing required Pipedrive config: " + ", ".join(missing))


def push_daily_deals(stamp: str) -> tuple[int, int, int]:
    validate_config()
    day_dir = Path(config.RESULTS_DIR) / stamp
    leads = read_csv(day_dir / "raw_leads.csv")
    grouped_contacts = contacts_by_business(day_dir / "contacts.csv")
    created = skipped = failed = 0

    if not leads:
        print(f"no article leads to push for {stamp}", file=sys.stderr)
        return created, skipped, failed

    with PipedriveDealClient() as pd:
        for row in leads:
            url = clean(row.get("link"))
            if not url:
                failed += 1
                print("article deal push failed: missing article URL", file=sys.stderr)
                continue
            try:
                existing = pd.find_deal_by_article_url(url)
                if existing is not None:
                    skipped += 1
                    print(f"article deal already exists for {url}: {existing}", file=sys.stderr)
                    continue
                contact = best_contact(row, grouped_contacts)
                org_id = pd.create_org(row)
                person_id = pd.create_person(contact, org_id) if contact else None
                deal_id = pd.create_deal(row, org_id, person_id)
                pd.add_note(deal_id, row, contact)
                created += 1
                print(f"created article deal {deal_id}: {row.get('business_name')}", file=sys.stderr)
            except Exception as err:
                failed += 1
                print(f"article deal push failed for {url}: {err}", file=sys.stderr)

    print(
        f"article deal push complete: {created} created, {skipped} skipped, {failed} failed",
        file=sys.stderr,
    )
    if failed:
        raise SystemExit(1)
    return created, skipped, failed


def main(argv: list[str]) -> int:
    stamp = argv[1] if len(argv) > 1 else date.today().isoformat()
    push_daily_deals(stamp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
