"""Tests for pipeline/push.py — httpx.MockTransport to avoid network."""

from __future__ import annotations

import unittest
from typing import Callable

import httpx

from pipeline import config, push


def _settings() -> config.Settings:
    return config.Settings(
        anthropic_api_key="test-anthropic",
        apollo_api_key="test-apollo",
        pipedrive_api_token="test-pd-token",
        pipedrive_domain="test-co",
        pipedrive_pipeline_id=4,
        pipedrive_stage_id=20,
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


if __name__ == "__main__":
    unittest.main()
