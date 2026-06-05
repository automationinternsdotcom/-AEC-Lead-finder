#!/usr/bin/env python3
"""
PipeDrive lead hygiene — single-file script.

Pipeline (one file, invoked by CoWork on an interval):
  1. FETCH  all leads from PipeDrive via the API.
  2. CLEAN  fields in memory — trim title, sort labels, parse packed contacts.
  3. WRITE  each changed lead back in place with PUT /leads/{id}.
  4. FLAG   suspected duplicates to a log. NEVER deletes or merges them.

Handles BOTH data shapes in one pass:
  - Old "Import" rows: title/labels only; contact fields stay empty.
  - New "API" rows: packed "Lead 1/2/3" strings get split into flat columns.

Safety (do not weaken):
  - In-place PUT by id only. Never creates, never deletes.
  - Duplicate handling is FLAG-ONLY. Merge/delete is a manual step.
  - Idempotent: re-running on an already-clean lead is a no-op.
  - Use --dry-run first: writes NOTHING, prints exactly what would change.

Setup:
  .env file next to this script (and a .gitignore line ".env"):
      PIPEDRIVE_API_TOKEN=your_token_here
      PIPEDRIVE_DOMAIN=yourcompany        # the 'yourcompany' in yourcompany.pipedrive.com
  pip install requests python-dotenv
"""

import argparse
import os
import re
import sys
import time
from collections import defaultdict

import requests
from dotenv import load_dotenv

load_dotenv()
API_TOKEN = os.getenv("PIPEDRIVE_API_TOKEN")
DOMAIN = os.getenv("PIPEDRIVE_DOMAIN")
if not API_TOKEN or not DOMAIN:
    sys.exit("ERROR: set PIPEDRIVE_API_TOKEN and PIPEDRIVE_DOMAIN in your .env file.")

BASE_URL = f"https://{DOMAIN}.pipedrive.com/api/v1"
PAGE_LIMIT = 100        # PipeDrive max page size for leads
REQUEST_PAUSE = 0.3     # gentle throttle between write calls (seconds)

# ---------------------------------------------------------------------------
# >>> VERIFY ON FIRST DRY-RUN <<<
# Custom fields use hash-style API keys that vary by account. The dry-run prints
# one lead's real keys — map them here before going live. Do NOT trust guesses.
# Set a value to None to skip writing that field until you've confirmed its key.
# ---------------------------------------------------------------------------
FIELD_TITLE = "title"          # lead title
FIELD_LABELS = "label_ids"     # labels are a LIST OF IDS, not text
FIELD_NEXT_ACTIVITY = "next_activity_id"  # DEAD field — never written; here only to document it's dropped

# Packed source strings coming from the enrichment/API rows.
PACKED_CONTACT_FIELDS = ["lead_1", "lead_2", "lead_3"]

# Flat per-contact target fields. contactN_* -> real PipeDrive custom-field key.
# linkedin is RESERVED (no data in source yet) — left None so nothing is written.
CONTACT_TARGETS = [
    {"name": "contact1_name", "role": "contact1_role", "email": "contact1_email",
     "email_likely": "contact1_email_likely", "phone": "contact1_phone", "linkedin": None},
    {"name": "contact2_name", "role": "contact2_role", "email": "contact2_email",
     "email_likely": "contact2_email_likely", "phone": "contact2_phone", "linkedin": None},
    {"name": "contact3_name", "role": "contact3_role", "email": "contact3_email",
     "email_likely": "contact3_email_likely", "phone": "contact3_phone", "linkedin": None},
]
# ---------------------------------------------------------------------------


def parse_contact(raw):
    """Split a packed 'Name | Role | [Likely ]Email | Phone' string into parts.

    Segments are NOT positional: role can span several segments, so email is
    found by '@' and phone by its digit run — not by index. Returns None if empty.
    """
    if not raw or not str(raw).strip():
        return None
    parts = [p.strip() for p in str(raw).split("|")]
    name = parts[0] if parts else ""

    # Email = the segment containing '@'. A leading "Likely" marks a guessed email.
    email_seg = next((p for p in parts if "@" in p), "")
    email_likely = bool(re.match(r"(?i)^likely\b", email_seg))
    email = re.sub(r"(?i)^likely\s+", "", email_seg).strip()

    # Phone = a non-email segment with >=7 digits. Strip a dangling "(" but keep
    # extensions like "x215".
    phone_seg = next((p for p in parts if "@" not in p and sum(c.isdigit() for c in p) >= 7), "")
    phone = re.sub(r"\s*\($", "", phone_seg).strip()

    # Role = whatever middle segments are left after removing name/email/phone.
    used = {parts[0], email_seg, phone_seg}
    role = ", ".join(p for p in parts[1:] if p and p not in used)

    return {"name": name, "role": role, "email": email,
            "email_likely": email_likely, "phone": phone}


def clean_lead(lead):
    """Return {field: new_value} for fields that should change ({} = already clean).

    Idempotent and non-destructive: only normalizes/derives fields, never deletes.
    """
    updates = {}

    # 1. Trim title whitespace.
    title = lead.get(FIELD_TITLE)
    if isinstance(title, str) and title.strip() != title:
        updates[FIELD_TITLE] = title.strip()

    # 2. Sort labels (IDs) so "A,B" and "B,A" become identical and stable.
    labels = lead.get(FIELD_LABELS)
    if isinstance(labels, list) and labels and sorted(labels) != labels:
        updates[FIELD_LABELS] = sorted(labels)

    # 3. Parse each packed contact string into its flat target fields.
    #    Only write a field when its target key is configured AND the value
    #    actually changes — that keeps re-runs a no-op (idempotent).
    for raw_field, target in zip(PACKED_CONTACT_FIELDS, CONTACT_TARGETS):
        parsed = parse_contact(lead.get(raw_field))
        if not parsed:
            continue
        for part, value in parsed.items():
            key = target.get(part)              # None for reserved fields (e.g. linkedin)
            if key and lead.get(key) != value:
                updates[key] = value

    return updates


def find_duplicate_groups(leads):
    """Group leads by normalized title to surface likely duplicates. FLAG ONLY."""
    by_title = defaultdict(list)
    for lead in leads:
        title = lead.get(FIELD_TITLE)
        if isinstance(title, str) and title.strip():
            by_title[title.strip().lower()].append(lead)
    return {k: v for k, v in by_title.items() if len(v) > 1}


def fetch_all_leads():
    """Page through GET /leads and return all lead dicts."""
    leads, start = [], 0
    while True:
        resp = requests.get(f"{BASE_URL}/leads",
                            params={"api_token": API_TOKEN, "start": start, "limit": PAGE_LIMIT},
                            timeout=30)
        resp.raise_for_status()
        payload = resp.json()
        leads.extend(payload.get("data") or [])
        page = (payload.get("additional_data") or {}).get("pagination") or {}
        if not page.get("more_items_in_collection"):
            break
        start = page.get("next_start", start + PAGE_LIMIT)
    return leads


def write_lead(lead_id, updates):
    """PUT field updates to a single lead, in place, by id."""
    resp = requests.put(f"{BASE_URL}/leads/{lead_id}",
                        params={"api_token": API_TOKEN}, json=updates, timeout=30)
    resp.raise_for_status()
    return resp.json()


def main():
    ap = argparse.ArgumentParser(description="PipeDrive lead hygiene (in-place, safe).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Report what would change without writing anything.")
    args = ap.parse_args()

    print(f"=== PipeDrive cleanup — {'DRY-RUN (no writes)' if args.dry_run else 'LIVE (writing changes)'} ===\n")

    print("Fetching leads...")
    leads = fetch_all_leads()
    print(f"Fetched {len(leads)} leads.\n")

    # Print one lead's real field keys so you can confirm the FIELD_*/CONTACT_TARGETS config.
    if leads:
        print("Sample lead field keys (verify config against these):")
        print("  " + ", ".join(sorted(leads[0].keys())) + "\n")

    # --- Clean pass ---
    changed = 0
    for lead in leads:
        updates = clean_lead(lead)
        if not updates:
            continue
        changed += 1
        lead_id = lead.get("id")
        if args.dry_run:
            print(f"[would update] lead {lead_id}: {updates}")
        else:
            write_lead(lead_id, updates)
            print(f"[updated] lead {lead_id}: {updates}")
            time.sleep(REQUEST_PAUSE)
    print(f"\n{'Would update' if args.dry_run else 'Updated'} {changed} lead(s).")

    # --- Duplicate flagging (never deletes) ---
    dupes = find_duplicate_groups(leads)
    if dupes:
        print(f"\n--- FLAGGED: {len(dupes)} possible duplicate group(s) (NOT merged — review manually) ---")
        for key, group in dupes.items():
            print(f"  '{key}': lead ids {[g.get('id') for g in group]}")
    else:
        print("\nNo duplicate titles found.")

    print("\nDone.")


if __name__ == "__main__":
    main()
