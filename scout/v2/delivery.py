"""Exactly-once comparison-email delivery and read-only monitoring."""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence


DISCLOSURE = "Sent by Codex on Jon Schack’s behalf."


class DeliveryError(RuntimeError):
    """Base error for a delivery invariant failure."""


class DeliveryCollisionError(DeliveryError):
    """An exact subject already exists or was duplicated."""


class ProfileMismatchError(DeliveryError):
    """The authenticated sender does not match runtime configuration."""


class MailGateway(Protocol):
    def authenticated_email(self) -> str: ...

    def search_sent_exact(self, subject: str) -> Sequence[str]: ...

    def send_html(
        self, *, sender: str, recipients: Sequence[str], subject: str, html: str
    ) -> str: ...


class GogMailGateway:
    """Authenticated Gmail adapter for the non-interactive `gog` CLI."""

    def __init__(self, account: str, executable: str = "gog"):
        self.account = account.strip().casefold()
        self.executable = executable

    def authenticated_email(self) -> str:
        payload = self._json(["auth", "list", "--check"])
        emails = {value.casefold() for value in _find_values(payload, {"email", "account"})}
        if self.account not in emails:
            raise ProfileMismatchError("configured Gmail account is not authenticated")
        return self.account

    def search_sent_exact(self, subject: str) -> Sequence[str]:
        payload = self._json(
            ["gmail", "search", f'in:sent subject:"{subject}"', "--all"]
        )
        matches = []
        for item in _objects(payload):
            identifier = str(item.get("id") or item.get("messageId") or item.get("threadId") or "")
            candidate_subject = str(item.get("subject") or "")
            if not identifier:
                continue
            if not candidate_subject:
                metadata = self._json(
                    ["gmail", "get", identifier, "--format", "metadata", "--headers", "Subject"]
                )
                candidate_subject = _extract_subject(metadata)
            if candidate_subject == subject:
                matches.append(identifier)
        return tuple(dict.fromkeys(matches))

    def send_html(
        self, *, sender: str, recipients: Sequence[str], subject: str, html: str
    ) -> str:
        payload = self._json(
            [
                "gmail",
                "send",
                "--to",
                ",".join(recipients),
                "--subject",
                subject,
                "--body-html",
                html,
            ]
        )
        identifiers = _find_values(payload, {"id", "messageId"})
        if not identifiers:
            raise DeliveryError("Gmail send returned no message ID")
        return identifiers[0]

    def _json(self, arguments: Sequence[str]) -> object:
        command = [
            self.executable,
            *arguments,
            "--account",
            self.account,
            "--json",
            "--results-only",
            "--no-input",
        ]
        completed = subprocess.run(command, text=True, capture_output=True, check=False)
        if completed.returncode:
            raise DeliveryError(completed.stderr.strip() or "gog command failed")
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise DeliveryError("gog returned invalid JSON") from exc


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    subject: str
    message_id: str
    recipients: tuple[str, ...]
    recovered_after_uncertain_send: bool = False


class ExactlyOnceDelivery:
    def __init__(self, gateway: MailGateway, expected_sender: str):
        self.gateway = gateway
        self.expected_sender = expected_sender.strip().casefold()

    def deliver(
        self,
        *,
        subject: str,
        recipients: Sequence[str],
        html: str,
        manifest_paths: Sequence[str | Path],
        include_sender: bool = True,
    ) -> DeliveryResult:
        sender = self.gateway.authenticated_email().strip().casefold()
        if not sender or sender != self.expected_sender:
            raise ProfileMismatchError(
                f"authenticated sender {sender or '<missing>'} does not match configured sender"
            )
        _require_terminal_manifests(manifest_paths)
        before = tuple(self.gateway.search_sent_exact(subject))
        if before:
            raise DeliveryCollisionError(
                f"exact subject already has {len(before)} Sent message(s): {subject}"
            )
        unique_recipients = _dedupe_recipients(
            [sender, *recipients] if include_sender else recipients
        )
        if not unique_recipients:
            raise DeliveryError("delivery requires at least one recipient")
        body = _with_disclosure(html)
        recovered = False
        try:
            message_id = self.gateway.send_html(
                sender=sender,
                recipients=unique_recipients,
                subject=subject,
                html=body,
            )
        except Exception:
            after_error = tuple(self.gateway.search_sent_exact(subject))
            if len(after_error) != 1:
                if len(after_error) > 1:
                    raise DeliveryCollisionError(
                        f"uncertain send produced {len(after_error)} exact-subject messages"
                    )
                raise
            message_id = after_error[0]
            recovered = True
        after = tuple(self.gateway.search_sent_exact(subject))
        if len(after) != 1:
            raise DeliveryCollisionError(
                f"post-send verification found {len(after)} exact-subject messages"
            )
        if message_id and message_id != after[0]:
            raise DeliveryError("sent message ID does not match exact-subject verification")
        return DeliveryResult(subject, after[0], tuple(unique_recipients), recovered)


@dataclass(frozen=True, slots=True)
class MonitorFinding:
    ok: bool
    problems: tuple[str, ...]


def monitor_comparison_day(
    gateway: MailGateway,
    *,
    subjects: Sequence[str],
    manifest_paths: Sequence[str | Path],
) -> MonitorFinding:
    """Inspect only. This function deliberately has no send capability."""
    problems: list[str] = []
    for subject in subjects:
        count = len(tuple(gateway.search_sent_exact(subject)))
        if count != 1:
            problems.append(f"{subject}: expected one Sent message, found {count}")
    try:
        _require_terminal_manifests(manifest_paths)
    except DeliveryError as exc:
        problems.append(str(exc))
    return MonitorFinding(not problems, tuple(problems))


def _require_terminal_manifests(paths: Sequence[str | Path]) -> None:
    if not paths:
        raise DeliveryError("delivery requires terminal manifests")
    for raw_path in paths:
        path = Path(raw_path)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DeliveryError(f"unreadable manifest: {path}") from exc
        if payload.get("status") not in {"completed", "failed", "review"}:
            raise DeliveryError(f"manifest is not terminal: {path}")


def _dedupe_recipients(recipients: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for recipient in recipients:
        normalized = recipient.strip().casefold()
        if normalized and normalized not in seen:
            seen.add(normalized)
            output.append(normalized)
    return output


def _with_disclosure(html: str) -> str:
    if DISCLOSURE in html:
        return html
    return f'{html}\n<p data-codex-disclosure="true">{DISCLOSURE}</p>'


def _objects(value: object):
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _objects(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _objects(nested)


def _find_values(value: object, keys: set[str]) -> list[str]:
    found = []
    for item in _objects(value):
        for key, nested in item.items():
            if key in keys and isinstance(nested, str) and nested.strip():
                found.append(nested.strip())
    return list(dict.fromkeys(found))


def _extract_subject(value: object) -> str:
    if isinstance(value, dict):
        direct = value.get("subject")
        if isinstance(direct, str):
            return direct
        if str(value.get("name") or "").casefold() == "subject" and isinstance(
            value.get("value"), str
        ):
            return value["value"]
        for nested in value.values():
            found = _extract_subject(nested)
            if found:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = _extract_subject(nested)
            if found:
                return found
    return ""
