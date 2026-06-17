"""Tests for pipeline.cli.email_digest — arg parsing, watermark, send/skip, exit codes."""

from __future__ import annotations

import io
import unittest
from contextlib import ExitStack
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from pipeline import config, email_digest
from pipeline.cli import email_digest as cli


def _settings(**overrides):
    base = dict(
        pipedrive_api_token="t", pipedrive_domain="acme",
        pipedrive_field_article_url="url_field",
        smtp_host="smtp.x.com", lead_digest_to="jw@x.com",
        lead_digest_from="bot@x.com",
    )
    base.update(overrides)
    return config.Settings(**base)


def _digest_lead():
    return email_digest.DigestLead(
        lead_id="uuid-1", title="T", add_time="2026-06-17 09:00:00",
        org_name="Acme", city="Tempe", signal="lease", priority="high",
        summary="s", value=1, currency="USD", article_url="https://ex.com/a",
        contacts=["Jane | Owner"], lead_url="https://acme.pipedrive.com/leads/inbox/uuid-1",
    )


def _patches(stack, *, settings=None, leads=None, last_run=None,
             send_side_effect=None):
    """Common patch set; returns (send_mock, db_mock). last_run → watermark."""
    settings = settings or _settings()
    db_mock = MagicMock()
    db_mock.get_last_digest_run.return_value = last_run
    stack.enter_context(patch("pipeline.cli.email_digest.config.settings",
                              return_value=settings))
    stack.enter_context(patch("pipeline.cli.email_digest.email_digest.make_pipedrive_client"))
    stack.enter_context(patch("pipeline.cli.email_digest.email_digest.list_leads_since",
                              return_value=leads if leads is not None else [_digest_lead()]))
    send = stack.enter_context(patch("pipeline.cli.email_digest.email_digest.send_digest",
                                     side_effect=send_side_effect))
    stack.enter_context(patch("pipeline.cli.email_digest.db", db_mock))
    return send, db_mock


class TestCli(unittest.TestCase):
    def test_requires_a_mode(self):
        with self.assertRaises(SystemExit):
            cli.main([])

    def test_bad_since_returns_2(self):
        with patch("pipeline.cli.email_digest.config.settings", return_value=_settings()):
            rc = cli.main(["--since", "not-a-date"])
        self.assertEqual(rc, 2)

    def test_daily_sends_and_advances_watermark(self):
        with ExitStack() as stack:
            send, db_mock = _patches(stack, leads=[_digest_lead()])
            rc = cli.main(["--daily"])
        self.assertEqual(rc, 0)
        send.assert_called_once()
        db_mock.record_digest_run.assert_called_once()
        self.assertEqual(db_mock.record_digest_run.call_args.args[2], 1)  # lead_count

    def test_skips_send_when_no_leads_but_still_advances(self):
        with ExitStack() as stack:
            send, db_mock = _patches(stack, leads=[])
            rc = cli.main(["--daily"])
        self.assertEqual(rc, 0)
        send.assert_not_called()
        db_mock.record_digest_run.assert_called_once()
        self.assertEqual(db_mock.record_digest_run.call_args.args[2], 0)

    def test_failed_send_does_not_advance_watermark(self):
        with ExitStack() as stack:
            send, db_mock = _patches(
                stack, leads=[_digest_lead()],
                send_side_effect=email_digest.SmtpNotConfigured("nope"))
            stack.enter_context(patch("sys.stderr", new_callable=io.StringIO))
            rc = cli.main(["--daily"])
        self.assertEqual(rc, 4)
        db_mock.record_digest_run.assert_not_called()

    def test_daily_uses_watermark_when_present(self):
        captured = {}
        with ExitStack() as stack:
            _patches(stack, leads=[], last_run="2026-06-15T07:00:00Z")
            # Re-patch list_leads_since to capture the cutoff.
            stack.enter_context(patch(
                "pipeline.cli.email_digest.email_digest.list_leads_since",
                side_effect=lambda http, s, since: (captured.__setitem__("since", since), [])[1]))
            cli.main(["--daily"])
        self.assertEqual(
            captured["since"],
            datetime(2026, 6, 15, 7, 0, 0, tzinfo=timezone.utc))

    def test_daily_first_run_falls_back_to_today(self):
        captured = {}
        with ExitStack() as stack:
            _patches(stack, leads=[], last_run=None)
            stack.enter_context(patch(
                "pipeline.cli.email_digest.email_digest.list_leads_since",
                side_effect=lambda http, s, since: (captured.__setitem__("since", since), [])[1]))
            cli.main(["--daily"])
        since = captured["since"]
        self.assertEqual((since.hour, since.minute, since.second), (0, 0, 0))
        self.assertEqual(since.tzinfo, timezone.utc)

    def test_print_only_writes_html_no_send_no_db(self):
        db_mock = MagicMock()
        with patch("pipeline.cli.email_digest.config.settings", return_value=_settings()), \
             patch("pipeline.cli.email_digest.email_digest.make_pipedrive_client"), \
             patch("pipeline.cli.email_digest.email_digest.list_leads_since",
                   return_value=[_digest_lead()]), \
             patch("pipeline.cli.email_digest.email_digest.send_digest") as send, \
             patch("pipeline.cli.email_digest.db", db_mock), \
             patch("sys.stdout", new_callable=io.StringIO) as stdout:
            rc = cli.main(["--since", "2026-05-29", "--print"])
        self.assertEqual(rc, 0)
        send.assert_not_called()
        db_mock.record_digest_run.assert_not_called()
        self.assertIn("<table", stdout.getvalue())

    def test_since_backfill_does_not_touch_watermark(self):
        with ExitStack() as stack:
            send, db_mock = _patches(stack, leads=[_digest_lead()])
            rc = cli.main(["--since", "2026-05-29"])
        self.assertEqual(rc, 0)
        send.assert_called_once()
        db_mock.record_digest_run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
