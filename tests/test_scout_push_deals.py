from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx

from scout import push_deals


class TestArticleDealPayload(unittest.TestCase):
    def test_payload_targets_aether_pipeline_and_article_url_field(self):
        row = {"business_name": "Acme Plaza", "link": "https://example.com/acme"}
        with mock.patch.object(push_deals.config, "PIPEDRIVE_FIELD_ARTICLE_URL", "article-url-field"), \
             mock.patch.object(push_deals.config, "PIPEDRIVE_ARTICLE_DEAL_PIPELINE_ID", 47), \
             mock.patch.object(push_deals.config, "PIPEDRIVE_ARTICLE_DEAL_STAGE_ID", 311):
            payload = push_deals.article_deal_payload(row, org_id=10, person_id=20)

        self.assertEqual(payload["title"], "Article Lead: Acme Plaza")
        self.assertEqual(payload["org_id"], 10)
        self.assertEqual(payload["person_id"], 20)
        self.assertEqual(payload["pipeline_id"], 47)
        self.assertEqual(payload["stage_id"], 311)
        self.assertEqual(payload["custom_fields"], {"article-url-field": "https://example.com/acme"})


class TestFindDealByArticleUrl(unittest.TestCase):
    def test_returns_matching_deal_id_from_custom_field(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/api/v2/deals/search")
            return httpx.Response(200, json={
                "success": True,
                "data": {
                    "items": [
                        {"item": {"id": 123, "custom_fields": {"article-url-field": "https://example.com/acme"}}}
                    ]
                },
            })

        with mock.patch.object(push_deals.config, "PIPEDRIVE_FIELD_ARTICLE_URL", "article-url-field"), \
             mock.patch.object(push_deals.config, "PIPEDRIVE_API_TOKEN", "token"), \
             mock.patch.object(push_deals.config, "PIPEDRIVE_DOMAIN", "test"):
            with push_deals.PipedriveDealClient(transport=httpx.MockTransport(handler)) as client:
                self.assertEqual(client.find_deal_by_article_url("https://example.com/acme"), 123)


class TestPushDailyDeals(unittest.TestCase):
    def test_push_daily_deals_creates_deal_from_raw_leads_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            day = Path(tmp) / "2026-08-29"
            day.mkdir()
            with (day / "raw_leads.csv").open("w", newline="", encoding="utf-8") as file:
                writer = csv.DictWriter(file, ["business_name", "link", "event"])
                writer.writeheader()
                writer.writerow({
                    "business_name": "Acme Plaza",
                    "link": "https://example.com/acme",
                    "event": "Lease-up",
                })

            fake = mock.Mock()
            fake.__enter__ = mock.Mock(return_value=fake)
            fake.__exit__ = mock.Mock(return_value=None)
            fake.find_deal_by_article_url.return_value = None
            fake.create_org.return_value = 10
            fake.create_person.return_value = None
            fake.create_deal.return_value = 99

            with mock.patch.object(push_deals.config, "RESULTS_DIR", tmp), \
                 mock.patch.object(push_deals.config, "PIPEDRIVE_API_TOKEN", "token"), \
                 mock.patch.object(push_deals.config, "PIPEDRIVE_DOMAIN", "test"), \
                 mock.patch.object(push_deals.config, "PIPEDRIVE_FIELD_ARTICLE_URL", "article-url-field"), \
                 mock.patch.object(push_deals, "PipedriveDealClient", return_value=fake):
                self.assertEqual(push_deals.push_daily_deals("2026-08-29"), (1, 0, 0))

            fake.find_deal_by_article_url.assert_called_once_with("https://example.com/acme")
            fake.create_org.assert_called_once()
            fake.create_deal.assert_called_once_with(mock.ANY, 10, None)
            fake.add_note.assert_called_once()


if __name__ == "__main__":
    unittest.main()
