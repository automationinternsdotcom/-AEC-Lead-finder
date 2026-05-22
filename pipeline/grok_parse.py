"""Parse SuperGrok Fast-mode responses into pipeline.enrich.Lead objects.

The Grok response shape is predictable:

  1. <Name> [or "<Name> (<AltName>)"]
  Current Title: <Title>, <Company>. [verification clauses]
  LinkedIn: <URL>
  Professional Email: [Likely] <email> [or <email2>] [format notes] [source tag]

  2. ...

Trailing meta (sources counts, suggested follow-ups) is ignored. We return
the first valid entry as the primary Lead — Grok orders by relevance.
"""
from __future__ import annotations

import re

from pipeline.enrich import Lead

# One entry runs from "N. " through the next "N+1. " or end-of-text.
_ENTRY_HEAD = re.compile(r"^(\d+)\.\s+(.+?)$", re.MULTILINE)
_TITLE_LINE = re.compile(r"Current Title:\s*(.+?)\s*$", re.MULTILINE)
_LINKEDIN = re.compile(r"LinkedIn:\s*(https?://\S+)")
_EMAIL = re.compile(r"Professional Email:[^\n]*?([\w.+-]+@[\w.-]+\.\w+)")
# Strip "(AltName)" or "(Christopher Madison)" parentheticals from names.
_PAREN_TAIL = re.compile(r"\s*\([^)]+\)\s*$")


def parse_grok_response(text: str) -> Lead | None:
    """Return the first qualifying decision-maker, or None if none found."""
    if not text or not text.strip():
        return None

    matches = list(_ENTRY_HEAD.finditer(text))
    if not matches:
        return None

    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        block = text[start:end]
        lead = _parse_block(block)
        if lead is not None:
            return lead
    return None


def _parse_block(block: str) -> Lead | None:
    head = _ENTRY_HEAD.match(block)
    if not head:
        return None
    raw_name = head.group(2).strip()
    name = _PAREN_TAIL.sub("", raw_name).strip()

    title_m = _TITLE_LINE.search(block)
    if not title_m:
        return None
    title = title_m.group(1).strip().rstrip(".")

    linkedin_m = _LINKEDIN.search(block)
    email_m = _EMAIL.search(block)

    return Lead(
        name=name,
        title=title,
        email=email_m.group(1).strip() if email_m else None,
        phone=None,
        linkedin_url=linkedin_m.group(1).strip() if linkedin_m else None,
        seniority=_derive_seniority(title),
        apollo_id="grok",
    )


# Check order matters: 'owner' (which catches "Founder") before 'c_suite' loses CEOs;
# 'c_suite' before 'vp' (CEO is not VP); 'director' before 'manager'.
_SENIORITY_RULES = (
    (("owner", "founder", "principal"), "owner"),
    (("chief ", " coo", " ceo", " cfo", " cmo", " cto",
      "coo,", "ceo,", "cfo,", "cmo,", "cto,",
      " coo)", " ceo)", " cfo)", " cmo)", " cto)"), "c_suite"),
    (("vp ", "vice president", "vp,"), "vp"),
    (("director",), "director"),
    (("manager",), "manager"),
)


def _derive_seniority(title: str) -> str:
    # Pad with spaces so word-boundary needles like " coo" can match leading/trailing positions.
    t = f" {title.lower()} "
    for needles, label in _SENIORITY_RULES:
        if any(n in t for n in needles):
            return label
    return ""
