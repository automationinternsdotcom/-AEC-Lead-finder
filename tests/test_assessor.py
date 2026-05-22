"""Tests for pipeline/assessor.py — Maricopa Assessor API lookup."""

from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock

from pipeline import assessor


# Sample of the real /search/rp/ JSON shape (captured live 2026-05-22).
_API_SAMPLE = {
    "TOTAL": 1,
    "Results": [{
        "MCR": "29135",
        "SubdivisonName": "HERITAGE SCOTTSDALE",
        "APN": "17352146",
        "Ownership": "ABC HOLDINGS LLC",
        "SitusAddress": "100 N MAIN ST",
        "SitusCity": "PHOENIX",
        "SitusZip": "85003",
        "PropertyType": "COMMERCIAL",
        "RentalID": None,
    }],
}

_API_EMPTY = {"TOTAL": 0, "Results": []}

# Minimal HTML containing the token line the scraper extracts.
_SEARCH_PAGE_HTML = """
<html><body>
<script>
  var g_id = 'rp';
  var g_search = '100 N Main St';
  var g_path = 'https://mcassessor.maricopa.gov/';
  var g_token = 'TEST_TOKEN_ABC123';
</script>
</body></html>
"""


def _mock_http(responses: list) -> MagicMock:
    """MagicMock httpx client. `responses` is a list of (status, body) per .get call.

    Body may be a str (returned as .text) or a dict (returned as .json()).
    """
    client = MagicMock()
    return_values = []
    for status, body in responses:
        r = MagicMock()
        r.status_code = status
        if isinstance(body, str):
            r.text = body
            r.json.side_effect = ValueError("not JSON")  # would fail if called on HTML
        else:
            r.text = json.dumps(body)
            r.json.return_value = body
        return_values.append(r)
    client.get.side_effect = return_values
    return client


class TestLookupByAddress(unittest.TestCase):
    def test_returns_top_result_on_match(self):
        http = _mock_http([
            (200, _SEARCH_PAGE_HTML),
            (200, _API_SAMPLE),
        ])
        result = assessor.lookup_by_address("100 N Main St, Phoenix AZ", http)
        self.assertIsNotNone(result)
        self.assertEqual(result["owner"], "ABC HOLDINGS LLC")
        self.assertEqual(result["apn"], "17352146")
        self.assertEqual(result["mailing_address"], "100 N MAIN ST, PHOENIX, 85003")
        self.assertEqual(result["property_type"], "COMMERCIAL")

    def test_returns_none_on_empty_api_results(self):
        http = _mock_http([
            (200, _SEARCH_PAGE_HTML),
            (200, _API_EMPTY),
        ])
        self.assertIsNone(assessor.lookup_by_address("nowhere st phoenix", http))

    def test_returns_none_on_token_extraction_failure(self):
        """If we can't find g_token in the search page, give up."""
        http = _mock_http([(200, "<html>no token here</html>")])
        self.assertIsNone(assessor.lookup_by_address("100 N Main St, Phoenix", http))

    def test_returns_none_on_search_page_http_error(self):
        http = _mock_http([(500, "")])
        self.assertIsNone(assessor.lookup_by_address("100 N Main St, Phoenix", http))

    def test_returns_none_on_api_http_error(self):
        http = _mock_http([
            (200, _SEARCH_PAGE_HTML),
            (500, ""),
        ])
        self.assertIsNone(assessor.lookup_by_address("100 N Main St, Phoenix", http))

    def test_skips_non_maricopa_cities(self):
        """Hardcoded Maricopa city list short-circuits non-Maricopa addresses."""
        http = _mock_http([])  # http.get should never be called
        self.assertIsNone(assessor.lookup_by_address("123 Main St, Tucson AZ", http))
        http.get.assert_not_called()

    def test_passes_token_in_authorization_header(self):
        """The second call (to /search/rp/) must include the extracted token."""
        http = _mock_http([
            (200, _SEARCH_PAGE_HTML),
            (200, _API_SAMPLE),
        ])
        assessor.lookup_by_address("100 N Main St, Phoenix", http)
        # Second call was the API call; check its kwargs included headers
        second_call = http.get.call_args_list[1]
        headers = second_call.kwargs.get("headers", {})
        self.assertEqual(headers.get("Authorization"), "TEST_TOKEN_ABC123")


class TestMaricopaCityFilter(unittest.TestCase):
    def test_phoenix_passes(self):
        self.assertTrue(assessor._in_maricopa("100 N Main St, Phoenix AZ"))

    def test_scottsdale_passes(self):
        self.assertTrue(assessor._in_maricopa("Scottsdale, AZ"))

    def test_queen_creek_passes(self):
        self.assertTrue(assessor._in_maricopa("123 anything, Queen Creek AZ 85142"))

    def test_tucson_rejected(self):
        self.assertFalse(assessor._in_maricopa("100 Main St, Tucson AZ"))

    def test_flagstaff_rejected(self):
        self.assertFalse(assessor._in_maricopa("Flagstaff"))


if __name__ == "__main__":
    unittest.main()
