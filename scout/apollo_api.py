"""Apollo.io People Match API client. Keys come from the repo-root .env."""
from __future__ import annotations

import json
import os
import ssl
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import certifi
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))

MATCH_URL = "https://api.apollo.io/api/v1/people/match"
SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
FATAL = {401, 402, 403, 429}


def empty():
    return {"email": "", "phone": "", "linkedin": "", "org": "", "sources": []}


def find_contact(name, business, phone_webhook=""):
    key = os.getenv("APOLLO_API_KEY")
    if not key:
        raise SystemExit("APOLLO_API_KEY is not set (check .env)")
    if not name:
        return empty()
    body = {
        "name": name,
        "organization_name": business or "",
        "reveal_personal_emails": True,
        **({"reveal_phone_number": True, "webhook_url": phone_webhook} if phone_webhook else {}),
    }
    request = Request(
        MATCH_URL,
        json.dumps(body).encode(),
        {
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
            "x-api-key": key,
        },
    )
    try:
        with urlopen(request, timeout=60, context=SSL_CONTEXT) as response:
            payload = json.load(response)
    except HTTPError as error:
        detail = error.read().decode()[:200]
        if error.code in FATAL:
            raise SystemExit(
                f"Apollo HTTP {error.code}: {detail}\nStopping: the remaining people are unchecked."
            ) from None
        return empty() | {"error": f"HTTP {error.code}: {detail}"}
    except Exception as error:
        raise SystemExit(
            f"{type(error).__name__}: {error}\nStopping: reached nobody, so nothing is known."
        ) from None
    return parse_person(payload.get("person"))


def parse_person(person):
    if not person:
        return empty()
    email = person.get("email") or ""
    if "email_not_unlocked" in email:
        email = ""
    phone = next(
        (
            p.get("sanitized_number") or p.get("raw_number") or ""
            for p in person.get("phone_numbers") or []
        ),
        "",
    )
    return {
        "email": email,
        "phone": phone,
        "linkedin": person.get("linkedin_url") or "",
        "org": (person.get("organization") or {}).get("name") or "",
        "sources": ["https://app.apollo.io/"],
    }


def _self_check():
    assert parse_person(None) == empty()
    assert parse_person({"email": "email_not_unlocked@x.com"}) == empty() | {
        "sources": ["https://app.apollo.io/"]
    }
    assert parse_person({
        "email": "a@b.com",
        "phone_numbers": [{"sanitized_number": "+1555"}],
        "linkedin_url": "li",
        "organization": {"name": "Acme"},
    }) == {"email": "a@b.com", "phone": "+1555", "linkedin": "li", "org": "Acme", "sources": ["https://app.apollo.io/"]}


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        _self_check()
        print("ok")
    else:
        print(json.dumps(find_contact(*sys.argv[1:3]), ensure_ascii=False, indent=2))
