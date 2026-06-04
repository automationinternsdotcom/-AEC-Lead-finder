"""Safe follow-up automation payloads for Pipedrive.

This module builds Pipedrive activity payloads. push.py only writes them when
`PIPEDRIVE_ENABLE_AUTOMATIONS=1`; otherwise operators can use the CLI preview
to demo the call/email reminders without touching production.
"""
from __future__ import annotations

from datetime import date, timedelta

from pipeline.enrich import Lead
from schema import ExtractedArticle


def next_business_day(today: date | None = None) -> date:
    d = (today or date.today()) + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def build_followup_activities(
    *,
    lead_id: str,
    org_id: int | None,
    person_id: int | None,
    article: ExtractedArticle,
    primary: Lead | None,
    url: str,
    owner_id: int | None = None,
    today: date | None = None,
) -> list[dict]:
    due = next_business_day(today).isoformat()
    common = {
        "lead_id": lead_id,
        "done": False,
    }
    if org_id is not None:
        common["org_id"] = org_id
    if person_id is not None:
        common["person_id"] = person_id
    if owner_id is not None:
        common["owner_id"] = owner_id

    title = article.title[:120]
    return [
        {
            **common,
            "type": "call",
            "subject": f"Call: {title}",
            "due_date": due,
            "due_time": "09:00",
            "duration": "00:30",
            "note": _call_note(article, primary, url),
        },
        {
            **common,
            "type": "email",
            "subject": f"Email draft: {title}",
            "due_date": due,
            "due_time": "10:00",
            "duration": "00:15",
            "note": _email_note(article, primary, url),
        },
    ]


def _contact_lines(primary: Lead | None) -> list[str]:
    if primary is None:
        return ["Contact: no decision-maker found yet"]
    lines = [f"Contact: {primary.name} / {primary.title}"]
    if primary.linkedin_url:
        lines.append(f"LinkedIn: {primary.linkedin_url}")
    if primary.email:
        prefix = "" if primary.email_verified else "Likely "
        lines.append(f"Email: {prefix}{primary.email}")
    if primary.phone:
        lines.append(f"Phone: {primary.phone}")
    return lines


def _call_note(article: ExtractedArticle, primary: Lead | None, url: str) -> str:
    lines = [
        f"Source: {url}",
        f"Reason: {article.filter_reason}",
        f"Aether angle: {article.service_angle or 'Confirm facility-services fit.'}",
        *_contact_lines(primary),
    ]
    return "\n".join(lines)


def _email_note(article: ExtractedArticle, primary: Lead | None, url: str) -> str:
    contact = primary.name if primary else "there"
    body = [
        "Draft email:",
        f"Subject: Quick facilities follow-up re: {article.company_name}",
        "",
        f"Hi {contact},",
        "",
        f"Saw the recent update: {article.title}.",
        article.service_angle or "Wanted to see if there is a useful facilities/asset-preservation conversation here.",
        "",
        "Would it be worth a quick call?",
        "",
        "Source article:",
        url,
        "",
        *_contact_lines(primary),
    ]
    return "\n".join(body)
