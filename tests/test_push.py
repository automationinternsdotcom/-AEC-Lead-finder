"""Tests for pipeline/push.py — httpx.MockTransport to avoid network."""

from __future__ import annotations

import unittest
from typing import Callable
from unittest.mock import patch

import httpx

from pipeline import config, push


def _settings() -> config.Settings:
    return config.Settings(
        pipedrive_api_token="test-pd-token",
        pipedrive_domain="test-co",
        pipedrive_pipeline_id=4,
        pipedrive_stage_id=20,
        pipedrive_field_article_url="test-field-key",
        apollo_api_key="test-apollo",
    )


def _client_with(handler: Callable[[httpx.Request], httpx.Response]) -> push.PipedriveClient:
    """Construct a PipedriveClient with httpx routed to a MockTransport handler."""
    client = push.PipedriveClient(_settings())
    client._http.close()  # discard the real one
    client._http = httpx.Client(
        base_url="https://test-co.pipedrive.com/api/v1/",
        params={"api_token": "test-pd-token"},
        transport=httpx.MockTransport(handler),
    )
    return client


class TestSuccessFalseEnvelopeCheck(unittest.TestCase):
    """Pipedrive returns HTTP 200 with {"success": false, "error": "..."}
    for validation failures (bad field, missing required, stage_id mismatch,
    etc.). Without an envelope check, _req returns {} silently and downstream
    code either crashes with a KeyError (post_id) or just drops the call
    (search_id, post). All three cases lose data."""

    def test_raises_PipedriveError_on_success_false(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "success": False,
                "error": "Pipeline_id must be a positive integer",
            })

        client = _client_with(handler)
        try:
            with self.assertRaises(push.PipedriveError) as ctx:
                client.post_id("deals", {"title": "X"})
            self.assertIn(
                "Pipeline_id must be a positive integer", str(ctx.exception),
            )
        finally:
            client.__exit__()

    def test_raises_on_search_too(self):
        """search_id is the silent-failure path: returns None on missing items,
        so a success:false envelope previously looked indistinguishable from
        'no match found'."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "success": False, "error": "fields param invalid",
            })

        client = _client_with(handler)
        try:
            with self.assertRaises(push.PipedriveError):
                client.search_id("deals", term="x")
        finally:
            client.__exit__()

    def test_raises_on_void_post_too(self):
        """post (no return value, for /notes etc) must also raise — otherwise
        a failed note POST silently drops the audit trail."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "success": False, "error": "content too long",
            })

        client = _client_with(handler)
        try:
            with self.assertRaises(push.PipedriveError):
                client.post("notes", {"deal_id": 1, "content": "X"})
        finally:
            client.__exit__()

    def test_success_true_still_returns_data(self):
        """Sanity: legitimate responses still work."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "success": True, "data": {"id": 42, "name": "Acme"},
            })

        client = _client_with(handler)
        try:
            result_id = client.post_id("organizations", {"name": "Acme"})
            self.assertEqual(result_id, 42)
        finally:
            client.__exit__()

    def test_auth_failure_still_raises_PipedriveError(self):
        """Pre-existing behavior — preserved."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "unauthorized"})

        client = _client_with(handler)
        try:
            with self.assertRaises(push.PipedriveError):
                client.post_id("deals", {"title": "X"})
        finally:
            client.__exit__()


class TestArticleUrlCustomField(unittest.TestCase):
    """Article URL goes into the Deal's custom field so Jordan can filter/
    sort/column on it, AND so we have a server-side dedup gate (defense
    in depth on top of the SQLite seen_urls check)."""

    def test_deal_payload_includes_article_url_custom_field(self):
        from pipeline.config import Settings
        from schema import ExtractedArticle

        settings = Settings(
            apollo_api_key=None,
            pipedrive_api_token="x",
            pipedrive_domain="x",
            pipedrive_pipeline_id=4,
            pipedrive_stage_id=20,
            pipedrive_field_article_url="field_hash_xyz",
        )
        article = ExtractedArticle.model_validate({
            "title": "x", "published_date": None, "summary_2sent": "x",
            "signal_type": "lease", "company_name": "Acme",
            "company_domain_guess": None, "property_type": "retail",
            "address": None, "city": "Tempe", "square_footage": None,
            "dollar_value": None, "unit_count": None,
            "az_relevant": True, "confidence": 0.7,
        })
        payload = push._deal_payload(
            article, est_value=100_000, org_id=1, person_id=None,
            settings=settings, url="https://example.com/x",
        )
        self.assertEqual(payload["field_hash_xyz"], "https://example.com/x")


class TestFindDealByUrl(unittest.TestCase):
    def test_returns_deal_id_when_found(self):
        captured = {}

        def handler(request):
            captured["url"] = str(request.url)
            return httpx.Response(200, json={
                "success": True,
                "data": {"items": [{"item": {"id": 999}}]},
            })

        client = _client_with(handler)
        try:
            result = client.find_deal_by_url("https://example.com/x")
            self.assertEqual(result, 999)
            # Pipedrive only accepts the literal "custom_fields" — not a hash —
            # so we MUST send that exact string. Caught a 400-Bad-Request live.
            self.assertIn("fields=custom_fields", captured["url"])
            self.assertIn("exact_match=true", captured["url"])
        finally:
            client.__exit__()

    def test_returns_none_when_no_match(self):
        def handler(request):
            return httpx.Response(200, json={
                "success": True, "data": {"items": []},
            })

        client = _client_with(handler)
        try:
            result = client.find_deal_by_url("https://example.com/x")
            self.assertIsNone(result)
        finally:
            client.__exit__()


class TestSyncToPipedriveSkipsWhenDealExists(unittest.TestCase):
    def test_returns_none_none_existing_id_on_dedup_hit(self):
        """When find_deal_by_url returns an id, sync skips and returns it."""
        from pipeline.config import Settings
        from schema import ExtractedArticle

        settings = Settings(
            apollo_api_key=None,
            pipedrive_api_token="x",
            pipedrive_domain="test-co",
            pipedrive_pipeline_id=4,
            pipedrive_stage_id=20,
            pipedrive_field_article_url="field_hash_xyz",
        )
        article = ExtractedArticle.model_validate({
            "title": "x", "published_date": None, "summary_2sent": "x",
            "signal_type": "lease", "company_name": "Acme",
            "company_domain_guess": None, "property_type": "retail",
            "address": None, "city": "Tempe", "square_footage": None,
            "dollar_value": None, "unit_count": None,
            "az_relevant": True, "confidence": 0.7,
        })

        def handler(request):
            # Always responds with one match — simulates dedup hit
            return httpx.Response(200, json={
                "success": True,
                "data": {"items": [{"item": {"id": 4242}}]},
            })

        # Capture the real class so _client_with doesn't recurse when patched.
        RealClient = push.PipedriveClient

        def make_mock_client(s):
            client = RealClient.__new__(RealClient)
            client._http = httpx.Client(
                base_url=f"https://{s.pipedrive_domain}.pipedrive.com/api/v1/",
                params={"api_token": s.pipedrive_api_token},
                transport=httpx.MockTransport(handler),
            )
            return client

        with patch.object(push, "PipedriveClient", make_mock_client):
            org, person, deal = push.sync_to_pipedrive(
                article, lead=None, est_value=0, basis="none",
                url="https://example.com/dup", settings=settings,
            )
        self.assertIsNone(org)
        self.assertIsNone(person)
        self.assertEqual(deal, 4242)


if __name__ == "__main__":
    unittest.main()
