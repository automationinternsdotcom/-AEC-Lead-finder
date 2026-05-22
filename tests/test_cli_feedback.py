"""Tests for pipeline.cli.feedback — stdout JSON for the routine's run report."""

from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch

from pipeline import config, feedback
from pipeline.cli import feedback as cli_feedback


def _settings():
    return config.Settings(
        pipedrive_api_token="t", pipedrive_domain="d",
        pipedrive_field_article_url="field_hash",
    )


class TestFeedbackCli(unittest.TestCase):
    def test_prints_json_array_of_flagged_leads(self):
        flagged = [
            feedback.FlaggedLead(
                lead_id="uuid-1", title="Test lead 1",
                article_url="https://example.com/a", flagged_at="2026-05-22T18:00:00Z",
            ),
            feedback.FlaggedLead(
                lead_id="uuid-2", title="Test lead 2",
                article_url=None, flagged_at="2026-05-22T17:00:00Z",
            ),
        ]
        with patch("pipeline.cli.feedback.config.settings", return_value=_settings()), \
             patch("pipeline.cli.feedback.feedback.get_not_relevant_label_id",
                   return_value="label-uuid"), \
             patch("pipeline.cli.feedback.feedback.list_flagged_leads",
                   return_value=flagged), \
             patch("sys.stdout", new_callable=io.StringIO) as stdout:
            rc = cli_feedback.main()
        self.assertEqual(rc, 0)
        data = json.loads(stdout.getvalue())
        self.assertEqual(len(data), 2)
        self.assertEqual(data[0]["lead_id"], "uuid-1")
        self.assertEqual(data[0]["article_url"], "https://example.com/a")
        self.assertIsNone(data[1]["article_url"])

    def test_prints_empty_array_when_no_flags(self):
        with patch("pipeline.cli.feedback.config.settings", return_value=_settings()), \
             patch("pipeline.cli.feedback.feedback.get_not_relevant_label_id",
                   return_value="label-uuid"), \
             patch("pipeline.cli.feedback.feedback.list_flagged_leads",
                   return_value=[]), \
             patch("sys.stdout", new_callable=io.StringIO) as stdout:
            cli_feedback.main()
        self.assertEqual(json.loads(stdout.getvalue()), [])

    def test_returns_3_when_label_missing(self):
        """Label not created in Pipedrive — surface a friendly error and a
        distinguishable exit code (3) so the routine can show 'create the
        label first' guidance instead of treating it as a transient failure."""
        with patch("pipeline.cli.feedback.config.settings", return_value=_settings()), \
             patch("pipeline.cli.feedback.feedback.get_not_relevant_label_id",
                   return_value=None), \
             patch("sys.stderr", new_callable=io.StringIO) as stderr, \
             patch("sys.stdout", new_callable=io.StringIO):
            rc = cli_feedback.main()
        self.assertEqual(rc, 3)
        self.assertIn("NOT RELEVANT", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
