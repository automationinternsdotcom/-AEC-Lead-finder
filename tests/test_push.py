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


def _article(**overrides):
    """Minimal valid ExtractedArticle for tests."""
    from schema import ExtractedArticle
    base = {
        "title": "x", "published_date": None, "summary_2sent": "x",
        "signal_type": "lease", "company_name": "Acme",
        "company_domain_guess": None, "property_type": "retail",
        "address": None, "city": "Tempe", "square_footage": None,
        "dollar_value": None, "unit_count": None,
        "az_relevant": True, "confidence": 0.7,
        "priority": "high", "filter_reason": "x", "service_angle": "x",
    }
    base.update(overrides)
    return ExtractedArticle.model_validate(base)


class TestSuccessFalseEnvelopeCheck(unittest.TestCase):
    """Pipedrive returns HTTP 200 with {"success": false, "error": "..."}
    for validation failures (bad field, missing required, etc.). Without an
    envelope check, _req returns {} silently and downstream code either
    crashes with a KeyError (post_id) or just drops the call (search_id,
    post). All three cases lose data."""

    def test_raises_PipedriveError_on_success_false(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "success": False,
                "error": "Title field is required",
            })

        client = _client_with(handler)
        try:
            with self.assertRaises(push.PipedriveError) as ctx:
                client.post_id("leads", {"organization_id": 1})
            self.assertIn("Title field is required", str(ctx.exception))
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
                client.search_id("leads", term="x")
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
                client.post("notes", {"lead_id": "uuid", "content": "X"})
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
                client.post_id("leads", {"title": "X", "organization_id": 1})
        finally:
            client.__exit__()


class TestLeadPayload(unittest.TestCase):
    """Lead payload differs from Deal: value is {amount, currency} dict;
    no pipeline_id / stage_id (Leads aren't in pipelines); organization_id
    not org_id; Article URL goes in the custom field for filterable column
    + server-side dedup."""

    def test_lead_payload_includes_article_url_custom_field(self):
        payload = push._lead_payload(
            _article(), est_value=100_000, org_id=1, person_id=None,
            settings=_settings(), url="https://example.com/x",
        )
        self.assertEqual(payload["test-field-key"], "https://example.com/x")

    def test_lead_payload_value_is_amount_currency_dict(self):
        """Lead /value is a dict, NOT flat value+currency at top level."""
        payload = push._lead_payload(
            _article(), est_value=45_000_000, org_id=1, person_id=None,
            settings=_settings(), url="https://example.com/x",
        )
        self.assertEqual(payload["value"], {"amount": 45_000_000, "currency": "USD"})

    def test_lead_payload_omits_value_when_none(self):
        """Don't push value:{amount:0,currency:USD} for unparseable deal sizes."""
        payload = push._lead_payload(
            _article(), est_value=None, org_id=1, person_id=None,
            settings=_settings(), url="https://example.com/x",
        )
        self.assertNotIn("value", payload)

    def test_lead_payload_no_pipeline_or_stage_keys(self):
        """Leads aren't in pipelines — these keys would 400."""
        payload = push._lead_payload(
            _article(), est_value=100, org_id=1, person_id=None,
            settings=_settings(), url="https://example.com/x",
        )
        self.assertNotIn("pipeline_id", payload)
        self.assertNotIn("stage_id", payload)

    def test_lead_payload_uses_organization_id_not_org_id(self):
        """Lead API uses 'organization_id' (full word) — Deal uses 'org_id'."""
        payload = push._lead_payload(
            _article(), est_value=None, org_id=42, person_id=None,
            settings=_settings(), url="https://example.com/x",
        )
        self.assertEqual(payload["organization_id"], 42)
        self.assertNotIn("org_id", payload)


class TestFindLeadByUrl(unittest.TestCase):
    def test_returns_uuid_when_found(self):
        captured = {}

        def handler(request):
            captured["url"] = str(request.url)
            return httpx.Response(200, json={
                "success": True,
                "data": {"items": [{"item": {"id": "abc-uuid-123"}}]},
            })

        client = _client_with(handler)
        try:
            result = client.find_lead_by_url("https://example.com/x")
            # Lead IDs are UUID strings, not ints.
            self.assertEqual(result, "abc-uuid-123")
            self.assertIsInstance(result, str)
            # Must hit /leads/search (NOT /deals/search) with literal "custom_fields".
            self.assertIn("/leads/search", captured["url"])
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
            result = client.find_lead_by_url("https://example.com/x")
            self.assertIsNone(result)
        finally:
            client.__exit__()


class TestSyncToPipedriveSkipsWhenLeadExists(unittest.TestCase):
    def test_returns_none_none_existing_uuid_on_dedup_hit(self):
        """When find_lead_by_url returns a UUID, sync skips and returns it."""

        def handler(request):
            # Always responds with one match — simulates dedup hit.
            return httpx.Response(200, json={
                "success": True,
                "data": {"items": [{"item": {"id": "existing-uuid-xyz"}}]},
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
            org, person, lead_id = push.sync_to_pipedrive(
                _article(), lead=None, est_value=0, basis="none",
                url="https://example.com/dup", settings=_settings(),
            )
        self.assertIsNone(org)
        self.assertIsNone(person)
        self.assertEqual(lead_id, "existing-uuid-xyz")


if __name__ == "__main__":
    unittest.main()
