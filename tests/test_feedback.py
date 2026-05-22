"""Tests for pipeline/feedback.py — Pipedrive NOT RELEVANT label polling."""

from __future__ import annotations

import json
import unittest
from typing import Callable
from unittest.mock import patch

import httpx

from pipeline import config, feedback


def _settings() -> config.Settings:
    return config.Settings(
        pipedrive_api_token="test-pd-token",
        pipedrive_domain="test-co",
        pipedrive_field_article_url="field_hash",
        apollo_api_key=None,
    )


def _mock_client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(
        base_url="https://test-co.pipedrive.com/api/v1/",
        params={"api_token": "test-pd-token"},
        transport=httpx.MockTransport(handler),
    )


class TestGetNotRelevantLabelId(unittest.TestCase):
    def test_finds_label_by_name_exact_match(self):
        def handler(req):
            return httpx.Response(200, json={
                "success": True,
                "data": [
                    {"id": "abc-uuid", "name": "Hot",          "color": "red"},
                    {"id": "def-uuid", "name": "NOT RELEVANT", "color": "gray"},
                ],
            })

        with _mock_client(handler) as http:
            label_id = feedback.get_not_relevant_label_id(http)
        self.assertEqual(label_id, "def-uuid")

    def test_case_insensitive_match(self):
        def handler(req):
            return httpx.Response(200, json={
                "success": True,
                "data": [{"id": "x-uuid", "name": "not relevant", "color": "gray"}],
            })

        with _mock_client(handler) as http:
            label_id = feedback.get_not_relevant_label_id(http)
        self.assertEqual(label_id, "x-uuid")

    def test_returns_none_when_label_absent(self):
        """If Jordan hasn't created the label yet, return None — let caller
        surface a helpful 'create the label in Pipedrive UI first' message
        rather than crashing."""
        def handler(req):
            return httpx.Response(200, json={
                "success": True,
                "data": [{"id": "abc", "name": "Hot", "color": "red"}],
            })

        with _mock_client(handler) as http:
            self.assertIsNone(feedback.get_not_relevant_label_id(http))

    def test_returns_none_on_empty_label_list(self):
        def handler(req):
            return httpx.Response(200, json={"success": True, "data": []})

        with _mock_client(handler) as http:
            self.assertIsNone(feedback.get_not_relevant_label_id(http))


class TestListFlaggedLeads(unittest.TestCase):
    def test_parses_leads_with_article_url(self):
        def handler(req):
            return httpx.Response(200, json={
                "success": True,
                "data": [
                    {
                        "id": "lead-1",
                        "title": "Mark-Taylor — expansion — Phoenix",
                        "organization_id": 42,
                        "update_time": "2026-05-22T18:30:00Z",
                        "field_hash": "https://example.com/article-1",
                    },
                    {
                        "id": "lead-2",
                        "title": "Some other lead",
                        "organization_id": 43,
                        "update_time": "2026-05-22T17:00:00Z",
                        "field_hash": "https://example.com/article-2",
                    },
                ],
            })

        with _mock_client(handler) as http:
            flagged = feedback.list_flagged_leads(http, "label-uuid", "field_hash")

        self.assertEqual(len(flagged), 2)
        self.assertEqual(flagged[0].lead_id, "lead-1")
        self.assertEqual(flagged[0].title, "Mark-Taylor — expansion — Phoenix")
        self.assertEqual(flagged[0].article_url, "https://example.com/article-1")
        self.assertEqual(flagged[0].flagged_at, "2026-05-22T18:30:00Z")

    def test_handles_missing_article_url_field(self):
        """Some Leads (created manually outside the pipeline) may not have
        the Article URL custom field populated."""
        def handler(req):
            return httpx.Response(200, json={
                "success": True,
                "data": [
                    {"id": "lead-x", "title": "Manual lead", "organization_id": 1,
                     "update_time": "2026-05-22T18:30:00Z"},
                ],
            })

        with _mock_client(handler) as http:
            flagged = feedback.list_flagged_leads(http, "label-uuid", "field_hash")
        self.assertEqual(len(flagged), 1)
        self.assertIsNone(flagged[0].article_url)

    def test_returns_empty_when_no_flagged_leads(self):
        def handler(req):
            return httpx.Response(200, json={"success": True, "data": []})

        with _mock_client(handler) as http:
            self.assertEqual(feedback.list_flagged_leads(http, "label-uuid", "field_hash"), [])

    def test_returns_empty_when_data_is_null(self):
        """Pipedrive's `/leads?label_id=X` returns data:null when no matches —
        not data:[]. Must not crash on this."""
        def handler(req):
            return httpx.Response(200, json={"success": True, "data": None})

        with _mock_client(handler) as http:
            self.assertEqual(feedback.list_flagged_leads(http, "label-uuid", "field_hash"), [])

    def test_passes_label_id_in_query_string(self):
        captured = {}

        def handler(req):
            captured["url"] = str(req.url)
            return httpx.Response(200, json={"success": True, "data": []})

        with _mock_client(handler) as http:
            feedback.list_flagged_leads(http, "the-label-uuid", "field_hash")
        self.assertIn("label_id=the-label-uuid", captured["url"])


if __name__ == "__main__":
    unittest.main()
