"""Exactly-once delivery uses deterministic preflight and postflight checks."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scout"))

from v2.delivery import (  # noqa: E402
    DISCLOSURE,
    DeliveryCollisionError,
    ExactlyOnceDelivery,
    ProfileMismatchError,
    monitor_comparison_day,
)


class FakeGateway:
    def __init__(self, sender="jon@example.com"):
        self.sender = sender
        self.sent = {}
        self.bodies = []
        self.raise_after_send = False

    def authenticated_email(self):
        return self.sender

    def search_sent_exact(self, subject):
        return list(self.sent.get(subject, []))

    def send_html(self, *, sender, recipients, subject, html):
        self.bodies.append((sender, tuple(recipients), subject, html))
        message_id = f"message-{len(self.bodies)}"
        self.sent.setdefault(subject, []).append(message_id)
        if self.raise_after_send:
            raise TimeoutError("result unknown")
        return message_id


def manifest(tmp_path, status="completed"):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"status": status}))
    return path


def test_delivery_deduplicates_recipients_adds_disclosure_and_verifies(tmp_path):
    gateway = FakeGateway()
    result = ExactlyOnceDelivery(gateway, "JON@example.com").deliver(
        subject="Aether 8/28 [V2]",
        recipients=["digest@example.com", "jon@example.com", "DIGEST@example.com"],
        html="<h1>Digest</h1>",
        manifest_paths=[manifest(tmp_path)],
    )
    assert result.recipients == ("jon@example.com", "digest@example.com")
    assert DISCLOSURE in gateway.bodies[0][3]
    assert gateway.search_sent_exact(result.subject) == [result.message_id]


def test_delivery_blocks_profile_collision_and_nonterminal_artifacts(tmp_path):
    gateway = FakeGateway("other@example.com")
    delivery = ExactlyOnceDelivery(gateway, "jon@example.com")
    with pytest.raises(ProfileMismatchError):
        delivery.deliver(
            subject="subject", recipients=[], html="body", manifest_paths=[manifest(tmp_path)]
        )
    gateway.sender = "jon@example.com"
    gateway.sent["subject"] = ["existing"]
    with pytest.raises(DeliveryCollisionError):
        delivery.deliver(
            subject="subject", recipients=[], html="body", manifest_paths=[manifest(tmp_path)]
        )


def test_uncertain_send_is_reconciled_and_monitor_never_resends(tmp_path):
    gateway = FakeGateway()
    gateway.raise_after_send = True
    path = manifest(tmp_path)
    result = ExactlyOnceDelivery(gateway, "jon@example.com").deliver(
        subject="subject", recipients=[], html="body", manifest_paths=[path]
    )
    assert result.recovered_after_uncertain_send
    before = len(gateway.bodies)
    finding = monitor_comparison_day(
        gateway, subjects=["subject", "missing"], manifest_paths=[path]
    )
    assert not finding.ok and "found 0" in finding.problems[0]
    assert len(gateway.bodies) == before
