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
# Grok uses both "Current Title:" and the shorter "Title:" — accept either.
_TITLE_LINE = re.compile(r"(?:Current\s+)?Title:\s*(.+?)\s*$", re.MULTILINE)
_LINKEDIN = re.compile(r"LinkedIn:\s*(https?://\S+)", re.IGNORECASE)
# Same for emails: "Professional Email:" and bare "Email:". TLD min 2 chars
# (no 1-char TLDs exist; Grok occasionally hallucinates `@example.c` truncations).
# IGNORECASE: live Grok sometimes lowercases the label ("Professional email:").
_EMAIL = re.compile(r"(?:Professional\s+)?Email:[^\n]*?([\w.+-]+@[\w.-]+\.\w{2,})", re.IGNORECASE)
# Phone numbers — Grok formats vary: "Phone: (480) 555-1234", "Direct: +1-480-555-1234",
# "Direct Phone: 480.555.1234", "Direct Dial: 480 555 1234 ext 102". We capture a
# 10-digit US-phone-looking substring after a Phone:/Direct:/Direct Dial:/Direct Phone:
# label. Extension suffixes (ext, x) are kept in the value.
_PHONE = re.compile(
    r"(?:Direct\s+(?:Phone|Dial)|Direct|Phone|Tel|Telephone):\s*"
    r"([+]?[\d().\-\s]{10,30}(?:\s*(?:ext|x|extension)\.?\s*\d+)?)",
    re.IGNORECASE,
)
# "Likely X" prefix on the email line signals Grok hedged on the address (it
# inferred from a `first.last@domain` format pattern, not a verified source).
# We still capture the address, but mark the contact as low-confidence.
_EMAIL_LIKELY = re.compile(
    r"(?:Professional\s+)?Email:\s*(?:Likely|Possibly|Estimated|Guess|Inferred)",
    re.IGNORECASE,
)
# Strip "(AltName)" or "(Christopher Madison)" parentheticals from names.
_PAREN_TAIL = re.compile(r"\s*\([^)]+\)\s*$")
# Separators Grok uses between an inline name and its title on the name line:
# comma ("Jane Doe, COO"), en/em dash ("Allan Gutkin – Principal"), or a
# space-padded hyphen ("Allan Gutkin - Principal"). A bare hyphen inside a name
# ("Mark-Taylor") has no surrounding spaces, so it is never split.
_NAME_TITLE_SEP = re.compile(r"\s*[,–—]\s*|\s+-\s+")
# A line that is a labelled contact field, not a title. Used to know when the
# line after the name is a title vs. already a LinkedIn/Email/Phone line.
_LABEL_LINE = re.compile(
    r"^\s*(?:Current\s+Title|Title|LinkedIn|Professional\s+Email|Email"
    r"|Direct\s+(?:Phone|Dial)|Direct|Phone|Tel|Telephone)\s*:",
    re.IGNORECASE,
)


def _title_from_following_line(block: str) -> str | None:
    """Grok sometimes puts the title on the line AFTER the name, with no label
    ("1. Shubham Pandey\nPrincipal, SSS Partners ..."). Return that line as the
    title, or None if the first non-empty line after the name is already a
    labelled contact field (i.e. there is no title)."""
    for ln in block.splitlines()[1:]:
        s = ln.strip()
        if not s:
            continue
        if _LABEL_LINE.match(s):
            return None
        return s.rstrip(".")
    return None

# Email local-parts that are role/team mailboxes, not a specific person's inbox.
# Used by is_high_confidence_contact: a generic mailbox isn't a verified contact.
_GENERIC_EMAIL_LOCALS = frozenset({
    "info", "contact", "office", "hello", "admin", "support",
    "sales", "marketing", "general", "inquiries", "team",
    "service", "customerservice", "help", "noreply", "no-reply",
})


def parse_grok_response(text: str) -> Lead | None:
    """Return the first qualifying decision-maker, or None if none found."""
    leads = parse_grok_response_all(text)
    return leads[0] if leads else None


def parse_grok_response_all(text: str, max_leads: int = 3) -> list[Lead]:
    """Return up to `max_leads` decision-makers in source order. Empty list
    if none parse cleanly. Used by the Lead 1/2/3 retrofit (pipeline.cli.
    grok_parse --all)."""
    if not text or not text.strip():
        return []
    matches = list(_ENTRY_HEAD.finditer(text))
    if not matches:
        return []
    out: list[Lead] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        lead = _parse_block(text[start:end])
        if lead is not None:
            out.append(lead)
            if len(out) >= max_leads:
                break
    return out


# Words that mean Grok returned only a job-title placeholder (no specific
# named person). Used by the enricher to decide whether to escalate to Heavy.
_GENERIC_TITLE_TOKENS = (
    "property manager", "leasing agent", "leasing manager",
    "general manager", "facilities manager", "the property manager",
    "the leasing agent", "site manager", "operations team",
    "asset manager", "property management",
)

# Suffix words that mean "team/org/role, not a person" — these catch responses
# like "Acme Property Management" or "Operations Team" that pass the two-token
# heuristic but aren't real names.
_GENERIC_SUFFIXES = (
    "management", "team", "group", "services", "department",
    "office", "company", "corporation", "llc", "inc",
)


def is_lead_generic(lead: Lead) -> bool:
    """True if the Lead.name looks like a job title placeholder or a team/org
    name rather than a specific named person. Trigger for Heavy-mode retry.

    Layered checks (cheapest first):
      1. Empty / whitespace-only.
      2. Multi-token role phrases ("Property Manager", "Asset Manager").
      3. Fewer than 2 tokens (single names like "Owner", "Manager").
      4. Last token is an org/role suffix ("Acme Property Management",
         "Operations Team") — catches multi-token placeholders that pass (3).
    """
    name = (lead.name or "").lower().strip()
    if not name or any(t in name for t in _GENERIC_TITLE_TOKENS):
        return True
    tokens = name.split()
    if len(tokens) < 2:
        return True
    if tokens[-1].rstrip(".,") in _GENERIC_SUFFIXES:
        return True
    return False


def _parse_block(block: str) -> Lead | None:
    head = _ENTRY_HEAD.match(block)
    if not head:
        return None
    raw_name = head.group(2).strip()
    name = _PAREN_TAIL.sub("", raw_name).strip()

    title_m = _TITLE_LINE.search(block)
    if title_m:
        title = title_m.group(1).strip().rstrip(".")
    else:
        # Live Grok usually omits the "Title:" line. It writes the title either
        # inline on the name line — separated by a comma OR a dash
        # ("Jessica Denison, Owner" / "Allan Gutkin – Principal/Owner") — or on
        # the line immediately after the name ("Shubham Pandey\nPrincipal, ...").
        parts = _NAME_TITLE_SEP.split(name, maxsplit=1)
        if len(parts) == 2 and parts[1].strip():
            name = parts[0].strip()
            title = parts[1].strip().rstrip(".")
        else:
            title = _title_from_following_line(block)
            if not title:
                return None

    linkedin_m = _LINKEDIN.search(block)
    email_m = _EMAIL.search(block)
    phone_m = _PHONE.search(block)

    email = email_m.group(1).strip() if email_m else None
    phone = _normalize_phone(phone_m.group(1)) if phone_m else None

    # An email counts as verified only if present, NOT hedged ("Likely ..."),
    # and NOT a generic role mailbox (info@, sales@, ...). Phones from Grok are
    # direct-dial (switchboard lines are reported as "Not found" and don't
    # match _PHONE), so a present phone is treated as verified.
    local = email.split("@", 1)[0].lower().replace(".", "").replace("-", "") if email else ""
    email_verified = bool(email) and not _EMAIL_LIKELY.search(block) and local not in _GENERIC_EMAIL_LOCALS
    phone_verified = bool(phone)

    return Lead(
        name=name,
        title=title,
        email=email,
        phone=phone,
        linkedin_url=linkedin_m.group(1).strip() if linkedin_m else None,
        seniority=_derive_seniority(title),
        apollo_id="grok",
        email_verified=email_verified,
        phone_verified=phone_verified,
    )


def _normalize_phone(raw: str) -> str:
    """Collapse whitespace and trim trailing punctuation. Preserves digits,
    parens, dashes, dots, ext suffixes — Grok's varied formats stay readable
    in Pipedrive's Lead 1/2/3 fields without re-parsing."""
    return " ".join(raw.split()).strip().rstrip(".,;")


def is_high_confidence_contact(lead: Lead, raw_block: str = "") -> bool:
    """True when both email AND phone are present AND the email isn't a hedged
    guess or a generic role mailbox. Used by the enricher to decide whether
    to escalate a Fast result to Heavy.

    The `raw_block` parameter is the original Grok response chunk for this
    contact (between this entry and the next "N. " head). We need it to
    detect the "Likely jdoe@..." pattern — once parsed, the email value is
    identical whether or not Grok hedged.
    """
    if not lead.email or not lead.phone:
        return False
    local = lead.email.split("@", 1)[0].lower().replace(".", "").replace("-", "")
    if local in _GENERIC_EMAIL_LOCALS:
        return False
    if raw_block and _EMAIL_LIKELY.search(raw_block):
        return False
    return True


def parse_grok_response_blocks(text: str, max_leads: int = 3) -> list[tuple[Lead, str]]:
    """Same as parse_grok_response_all but returns (Lead, raw_block) tuples so
    the caller can run is_high_confidence_contact (which needs the raw text to
    detect 'Likely' hedging). Order preserved."""
    if not text or not text.strip():
        return []
    matches = list(_ENTRY_HEAD.finditer(text))
    if not matches:
        return []
    out: list[tuple[Lead, str]] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        block = text[start:end]
        lead = _parse_block(block)
        if lead is not None:
            out.append((lead, block))
            if len(out) >= max_leads:
                break
    return out


# Check order matters: 'owner' (which catches "Founder") before 'c_suite' loses CEOs;
# 'c_suite' before 'vp' (CEO is not VP); 'director' before 'manager'.
_SENIORITY_RULES = (
    (("owner", "founder", "principal"), "owner"),
    (("chief ", "chairman", " coo", " ceo", " cfo", " cmo", " cto",
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
