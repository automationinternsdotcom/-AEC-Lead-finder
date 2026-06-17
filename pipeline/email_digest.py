"""Email digest of recently-created Pipedrive Leads.

Jordan wants the day's new article-sourced Leads delivered to his inbox so he
can triage without opening Pipedrive. This module reads Leads back OUT of
Pipedrive (the inverse of push.py), enriches each with its Org name + the
deterministic metadata push.py wrote into the Lead's Note, renders a summary
table (one row per Lead, listing every contact), and sends it over SMTP.

Reuses: httpx (same base_url + api_token wiring as feedback.py), config.Settings,
        stdlib smtplib/email for the send.
Two windows:
  - backfill: every Lead with add_time >= a given date (the one-time May-29 email)
  - daily:    every Lead created since the last successful digest run (a watermark
              persisted in SQLite by the CLI). The routine appends this at the end
              of its run, so it captures the Leads the run just created without
              re-emailing yesterday's. Falls back to start-of-today (UTC) the very
              first time, before any watermark exists.
Failure: SMTP/auth errors raise (CLI surfaces them). Zero new Leads → no send.

Note-parsing: push._note_body writes a fixed line
  "Signal: <s> | Property: <p> | City: <c> | Priority: <pri>"
as the 3rd line of every Lead's Note. We parse THAT (a format we control), so
City/Signal/Priority — which aren't structured custom fields — still reach the
digest. The 1st Note line is the 2-sentence summary.
"""
from __future__ import annotations

import dataclasses
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formatdate
from html import escape

import httpx

from pipeline import config
from pipeline.config import Settings

# Pipedrive v1 paginates Leads via start/limit + a next_start cursor; 500 is the
# v1 max page size (mirrors feedback.list_flagged_leads).
_LEADS_PAGE_SIZE = 500


@dataclasses.dataclass(slots=True)
class DigestLead:
    """A Lead, flattened for rendering. Mirrors what push.py wrote out."""
    lead_id: str
    title: str
    add_time: str                 # Pipedrive add_time, "YYYY-MM-DD HH:MM:SS" (UTC)
    org_name: str | None
    city: str | None
    signal: str | None
    priority: str | None
    summary: str | None
    value: int | None
    currency: str | None
    article_url: str | None
    contacts: list[str]           # raw "Name | Title | Email | Phone" strings
    lead_url: str                 # deep link to the Lead in Pipedrive


class SmtpNotConfigured(RuntimeError):
    """Raised when a send is attempted without the SMTP_* / LEAD_DIGEST_* env."""


def make_pipedrive_client(settings: Settings) -> httpx.Client:
    """httpx.Client wired for Pipedrive v1 (same shape as feedback's CLI)."""
    return httpx.Client(
        base_url=f"https://{settings.pipedrive_domain}.pipedrive.com/api/v1/",
        params={"api_token": settings.pipedrive_api_token},
        timeout=config.HTTP_TIMEOUT_SEC,
    )


def _lead_add_dt(lead: dict) -> datetime | None:
    """Parse a Lead's add_time into an aware UTC datetime. None if unparseable.

    Pipedrive v1 returns add_time as "YYYY-MM-DD HH:MM:SS" (UTC, space-separated);
    older/edge rows may carry date-only — both are handled.
    """
    raw = lead.get("add_time")
    if not raw or len(raw) < 10:
        return None
    try:
        return datetime.strptime(raw[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            return datetime.strptime(raw[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return None


def _org_id(lead: dict):
    """Lead.organization_id may be a bare int or a nested {value:...} dict."""
    org = lead.get("organization_id")
    if isinstance(org, dict):
        return org.get("value")
    return org


def list_leads_since(
    http: httpx.Client, settings: Settings, since: datetime,
) -> list[DigestLead]:
    """Every Lead with add_time (UTC) >= `since`, newest first.

    Paginates /leads (the endpoint ignores date/label filters server-side, same
    as feedback.py), post-filters by add_time, then enriches each match with its
    Org name + city/signal/priority/summary parsed from its Note.
    """
    matches: list[dict] = []
    start = 0
    while True:
        resp = http.get("leads", params={"limit": _LEADS_PAGE_SIZE, "start": start})
        resp.raise_for_status()
        body = resp.json()
        data = body.get("data") or []
        for lead in data:
            dt = _lead_add_dt(lead)
            if dt is not None and dt >= since:
                matches.append(lead)
        pagination = (body.get("additional_data") or {}).get("pagination") or {}
        if not pagination.get("more_items_in_collection"):
            break
        start = pagination.get("next_start")
        if start is None:
            break

    matches.sort(key=lambda l: l.get("add_time") or "", reverse=True)

    org_names: dict[int, str | None] = {}  # cache — many Leads share an Org
    out: list[DigestLead] = []
    for lead in matches:
        out.append(_to_digest_lead(http, settings, lead, org_names))
    return out


def _to_digest_lead(
    http: httpx.Client, settings: Settings, lead: dict,
    org_names: dict[int, str | None],
) -> DigestLead:
    oid = _org_id(lead)
    if oid is not None and oid not in org_names:
        org_names[oid] = _fetch_org_name(http, oid)
    org_name = org_names.get(oid)

    note_meta = _fetch_note_meta(http, lead.get("id"))

    value = lead.get("value") or {}
    contacts = _contacts(lead, settings)
    lead_id = str(lead.get("id") or "")
    return DigestLead(
        lead_id=lead_id,
        title=str(lead.get("title") or ""),
        add_time=str(lead.get("add_time") or ""),
        org_name=org_name,
        city=note_meta.get("city"),
        signal=note_meta.get("signal"),
        priority=note_meta.get("priority"),
        summary=note_meta.get("summary"),
        value=value.get("amount") if isinstance(value, dict) else None,
        currency=value.get("currency") if isinstance(value, dict) else None,
        article_url=lead.get(settings.pipedrive_field_article_url),
        contacts=contacts,
        lead_url=f"https://{settings.pipedrive_domain}.pipedrive.com/leads/inbox/{lead_id}",
    )


def _fetch_org_name(http: httpx.Client, org_id) -> str | None:
    """GET organizations/{id} → name. Returns None on any error (best-effort)."""
    try:
        resp = http.get(f"organizations/{org_id}")
        resp.raise_for_status()
        return (resp.json().get("data") or {}).get("name")
    except httpx.HTTPError:
        return None


def _fetch_note_meta(http: httpx.Client, lead_id) -> dict:
    """First Note on the Lead → {summary, signal, city, priority}.

    push._note_body writes a deterministic Note at Lead creation; we parse the
    bits that aren't available as structured custom fields. Best-effort: any
    failure or missing Note yields an empty dict.
    """
    if not lead_id:
        return {}
    try:
        resp = http.get("notes", params={"lead_id": lead_id, "limit": 1, "sort": "add_time ASC"})
        resp.raise_for_status()
        notes = resp.json().get("data") or []
    except httpx.HTTPError:
        return {}
    if not notes:
        return {}
    return _parse_note(notes[0].get("content") or "")


def _parse_note(content: str) -> dict:
    """Extract summary + the 'Signal: ... | City: ... | Priority: ...' line.

    The Note is plain text (push.py joins fields with '\\n'); Pipedrive may
    return it as HTML, so strip a couple of common tags defensively.
    """
    text = content.replace("<br>", "\n").replace("<br/>", "\n").replace("</p>", "\n")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    meta: dict = {}
    if lines:
        # First non-empty line is the 2-sentence summary (unless it's the
        # 'Source:' line, which only happens when summary was empty).
        if not lines[0].lower().startswith("source:"):
            meta["summary"] = lines[0]
    for ln in lines:
        if ln.startswith("Signal:") and "|" in ln:
            for part in ln.split("|"):
                if ":" not in part:
                    continue
                key, _, val = part.partition(":")
                key, val = key.strip().lower(), val.strip()
                if key in ("signal", "city", "priority"):
                    meta[key] = val
            break
    return meta


def _contacts(lead: dict, settings: Settings) -> list[str]:
    """The non-empty Lead 1/2/3 custom-field strings, in order."""
    out: list[str] = []
    for field in (
        settings.pipedrive_field_lead_1,
        settings.pipedrive_field_lead_2,
        settings.pipedrive_field_lead_3,
    ):
        if not field:
            continue
        val = lead.get(field)
        if isinstance(val, dict):  # Pipedrive sometimes nests as {value: ...}
            val = val.get("value")
        if val:
            out.append(str(val))
    return out


# ---- Rendering -------------------------------------------------------------

def _fmt_value(lead: DigestLead) -> str:
    if not lead.value:
        return "—"
    return f"${lead.value:,.0f} {lead.currency or ''}".strip()


def render_text(leads: list[DigestLead], window_label: str) -> str:
    """Plaintext fallback body."""
    lines = [
        f"Aether — {len(leads)} new lead(s) {window_label}.",
        "",
    ]
    for i, ld in enumerate(leads, 1):
        lines.append(f"{i}. {ld.title}")
        meta = " | ".join(
            p for p in (
                ld.org_name,
                ld.city,
                (ld.priority.upper() if ld.priority else None),
                ld.signal,
                _fmt_value(ld) if ld.value else None,
            ) if p
        )
        if meta:
            lines.append(f"   {meta}")
        if ld.summary:
            lines.append(f"   {ld.summary}")
        if ld.article_url:
            lines.append(f"   Article: {ld.article_url}")
        lines.append(f"   Pipedrive: {ld.lead_url}")
        if ld.contacts:
            lines.append("   Contacts:")
            for c in ld.contacts:
                lines.append(f"     - {c}")
        else:
            lines.append("   Contacts: (none found)")
        lines.append("")
    return "\n".join(lines)


def render_html(leads: list[DigestLead], window_label: str) -> str:
    """HTML summary table — one row per Lead, every contact listed."""
    rows = []
    for ld in leads:
        contacts_html = (
            "<br>".join(escape(c) for c in ld.contacts)
            if ld.contacts else "<em>none found</em>"
        )
        title_html = escape(ld.title)
        if ld.article_url:
            title_html = f'<a href="{escape(ld.article_url)}">{title_html}</a>'
        priority = (ld.priority or "").lower()
        pri_color = {"high": "#c0392b", "medium": "#d68910"}.get(priority, "#555")
        pri_html = (
            f'<span style="color:{pri_color};font-weight:bold">{escape(ld.priority.upper())}</span>'
            if ld.priority else "—"
        )
        rows.append(
            "<tr>"
            f'<td style="{_TD}">{title_html}'
            + (f'<div style="color:#666;font-size:12px;margin-top:4px">{escape(ld.summary)}</div>' if ld.summary else "")
            + "</td>"
            f'<td style="{_TD}">{escape(ld.org_name or "—")}</td>'
            f'<td style="{_TD}">{escape(ld.city or "—")}</td>'
            f'<td style="{_TD}">{pri_html}<br><span style="color:#666;font-size:12px">{escape(ld.signal or "")}</span></td>'
            f'<td style="{_TD};white-space:nowrap">{escape(_fmt_value(ld))}</td>'
            f'<td style="{_TD};font-size:13px">{contacts_html}</td>'
            f'<td style="{_TD};white-space:nowrap"><a href="{escape(ld.lead_url)}">Open</a></td>'
            "</tr>"
        )
    header = (
        "<tr>"
        + "".join(
            f'<th style="{_TH}">{h}</th>'
            for h in ("Lead", "Org", "City", "Priority / Signal", "Est. value", "Contacts", "Pipedrive")
        )
        + "</tr>"
    )
    return (
        '<div style="font-family:Arial,Helvetica,sans-serif;color:#222">'
        f"<h2 style='margin:0 0 4px'>Aether — new leads {escape(window_label)}</h2>"
        f"<p style='color:#666;margin:0 0 12px'>{len(leads)} lead(s).</p>"
        '<table style="border-collapse:collapse;width:100%;font-size:14px">'
        f"<thead>{header}</thead><tbody>{''.join(rows)}</tbody></table></div>"
    )


_TH = "text-align:left;padding:8px;border-bottom:2px solid #ccc;background:#f4f4f4;font-size:13px"
_TD = "padding:8px;border-bottom:1px solid #eee;vertical-align:top"


# ---- Sending ---------------------------------------------------------------

def build_message(
    leads: list[DigestLead], window_label: str, settings: Settings,
) -> EmailMessage:
    """Compose the multipart (plaintext + HTML) digest email."""
    msg = EmailMessage()
    msg["Subject"] = f"Aether: {len(leads)} new lead(s) {window_label}"
    msg["From"] = settings.lead_digest_from
    msg["To"] = settings.lead_digest_to
    msg["Date"] = formatdate(localtime=True)
    msg.set_content(render_text(leads, window_label))
    msg.add_alternative(render_html(leads, window_label), subtype="html")
    return msg


def send_digest(msg: EmailMessage, settings: Settings) -> None:
    """Send via SMTP. Port 465 → implicit SSL; anything else → STARTTLS."""
    if not (settings.smtp_host and settings.lead_digest_to and settings.lead_digest_from):
        raise SmtpNotConfigured(
            "Set SMTP_HOST, LEAD_DIGEST_TO and LEAD_DIGEST_FROM to send the digest."
        )
    host, port = settings.smtp_host, settings.smtp_port
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=config.HTTP_TIMEOUT_SEC) as s:
            _login_and_send(s, msg, settings)
    else:
        with smtplib.SMTP(host, port, timeout=config.HTTP_TIMEOUT_SEC) as s:
            s.starttls()
            _login_and_send(s, msg, settings)


def _login_and_send(s: smtplib.SMTP, msg: EmailMessage, settings: Settings) -> None:
    if settings.smtp_user and settings.smtp_password:
        s.login(settings.smtp_user, settings.smtp_password)
    s.send_message(msg)


def parse_watermark(iso: str) -> datetime:
    """Parse a stored watermark (util.utc_now_iso → 'YYYY-MM-DDTHH:MM:SSZ') to UTC."""
    return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def start_of_today_utc() -> datetime:
    """Midnight UTC today — the `--daily` window the very first run, before any
    watermark exists. The routine runs the digest at the end of its run, so this
    still captures every Lead the run just created."""
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)
