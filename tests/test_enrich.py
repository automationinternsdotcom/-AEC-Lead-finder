"""Tests for enrich.py — Apollo optional path."""

from __future__ import annotations

import unittest

from pipeline import config, enrich


class TestApolloOptional(unittest.TestCase):
    def test_find_lead_returns_none_when_no_api_key(self):
        settings = config.Settings(
            apollo_api_key=None,    # optional now
            pipedrive_api_token="x",
            pipedrive_domain="x",
            pipedrive_pipeline_id=1,
            pipedrive_stage_id=1,
            pipedrive_field_article_url="x",  # added in Task 6 — this test will need it
        )
        self.assertIsNone(enrich.find_lead("example.com", settings))

    def test_find_lead_returns_none_when_apollo_key_blank(self):
        settings = config.Settings(
            apollo_api_key="",
            pipedrive_api_token="x",
            pipedrive_domain="x",
            pipedrive_pipeline_id=1,
            pipedrive_stage_id=1,
            pipedrive_field_article_url="x",
        )
        self.assertIsNone(enrich.find_lead("example.com", settings))


if __name__ == "__main__":
    unittest.main()
