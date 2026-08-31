"""Webhook and unsubscribe-token security helpers."""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
import uuid


class SignatureError(ValueError):
    pass


def verify_warmy_signature(
    body: bytes,
    signature_header: str,
    timestamp_header: str,
    secret: str,
    *,
    now_ms: int | None = None,
    tolerance_ms: int = 300_000,
) -> None:
    if not secret:
        raise SignatureError("webhook secret is not configured")
    parts = {}
    for component in signature_header.split(","):
        key, separator, value = component.strip().partition("=")
        if separator:
            parts[key] = value
    signed_timestamp = parts.get("t") or timestamp_header
    supplied = parts.get("v1")
    if not signed_timestamp or not supplied:
        raise SignatureError("malformed Warmy signature")
    try:
        timestamp = int(signed_timestamp)
    except ValueError as error:
        raise SignatureError("invalid Warmy timestamp") from error
    current = int(time.time() * 1000) if now_ms is None else now_ms
    if abs(current - timestamp) > tolerance_ms:
        raise SignatureError("Warmy webhook timestamp is outside the replay window")
    signed = str(timestamp).encode("ascii") + b"." + body
    expected = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, supplied):
        raise SignatureError("invalid Warmy signature")


def issue_unsubscribe_token(secret: str, subject: str = "") -> tuple[str, str]:
    """Return (public token, opaque token id stored with the email)."""
    if not secret:
        raise SignatureError("unsubscribe secret is not configured")
    token_id = (
        hmac.new(
            secret.encode(),
            f"unsubscribe:{subject.casefold()}".encode(),
            hashlib.sha256,
        ).hexdigest()[:32]
        if subject
        else uuid.uuid4().hex
    )
    signature = hmac.new(secret.encode(), token_id.encode(), hashlib.sha256).digest()
    encoded = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
    return f"{token_id}.{encoded}", token_id


def verify_unsubscribe_token(token: str, secret: str) -> str:
    token_id, separator, supplied = token.partition(".")
    if not separator or len(token_id) != 32 or not secret:
        raise SignatureError("invalid unsubscribe token")
    signature = hmac.new(secret.encode(), token_id.encode(), hashlib.sha256).digest()
    expected = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
    if not hmac.compare_digest(expected, supplied):
        raise SignatureError("invalid unsubscribe token")
    return token_id
