"""Tests for extract.py — text extraction only (no LLM)."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from pipeline import extract


class TestExtractArticleText(unittest.TestCase):
    def test_returns_cleaned_text_from_html(self):
        html = """
        <html><body>
        <article>
          <p>Tempe retail tower signs Trader Joe's as anchor tenant. The
          new 45,000-square-foot development is set to open in early 2027.
          The mixed-use project will include ground-floor retail, upper-floor
          office space, and structured parking. The developer secured $18M
          in construction financing from a regional lender. Leasing efforts
          are already underway for the remaining inline retail bays.</p>
        </article>
        </body></html>
        """
        mock_http = MagicMock()
        mock_http.get.return_value.status_code = 200
        mock_http.get.return_value.text = html
        text = extract.extract_article_text("https://example.com/x", mock_http)
        self.assertIn("Trader Joe", text)
        self.assertIn("45,000-square-foot", text)

    def test_raises_on_http_error(self):
        mock_http = MagicMock()
        mock_http.get.return_value.status_code = 404
        with self.assertRaises(extract.ExtractError) as ctx:
            extract.extract_article_text("https://example.com/missing", mock_http)
        self.assertIn("404", str(ctx.exception))

    def test_raises_on_empty_or_short_content(self):
        html = "<html><body><p>x</p></body></html>"
        mock_http = MagicMock()
        mock_http.get.return_value.status_code = 200
        mock_http.get.return_value.text = html
        with self.assertRaises(extract.ExtractError) as ctx:
            extract.extract_article_text("https://example.com/short", mock_http)
        self.assertIn("empty_or_short", str(ctx.exception))


class TestIsQualifying(unittest.TestCase):
    """is_qualifying still operates on an ExtractedArticle (now Claude-produced)."""

    def _article(self, **overrides):
        from schema import ExtractedArticle
        base = dict(
            title="x", published_date=None, summary_2sent="x",
            signal_type="lease", company_name="Acme",
            company_domain_guess=None, property_type="retail",
            address=None, city="Tempe", square_footage=None,
            dollar_value=None, unit_count=None,
            az_relevant=True, confidence=0.7,
            priority="high", filter_reason="x",
            service_angle="x",
        )
        base.update(overrides)
        return ExtractedArticle.model_validate(base)

    def test_passes_az_relevant_high_confidence(self):
        passes, reason = extract.is_qualifying(self._article())
        self.assertTrue(passes)
        self.assertIsNone(reason)

    def test_drops_non_az(self):
        passes, reason = extract.is_qualifying(self._article(az_relevant=False))
        self.assertFalse(passes)
        self.assertEqual(reason, "not_az")

    def test_drops_low_confidence_other_signal(self):
        passes, reason = extract.is_qualifying(
            self._article(signal_type="other", confidence=0.55)
        )
        self.assertFalse(passes)
        self.assertEqual(reason, "other_low_conf")

    def test_drops_baseline_low_confidence(self):
        passes, reason = extract.is_qualifying(self._article(confidence=0.4))
        self.assertFalse(passes)
        self.assertEqual(reason, "low_conf")

    def test_drops_low_priority(self):
        """Jordan's protocol: low-priority articles (macro commentary, mortgage
        news, rankings, etc.) should never reach Pipedrive even if Claude
        rated them confidently."""
        passes, reason = extract.is_qualifying(self._article(priority="low"))
        self.assertFalse(passes)
        self.assertEqual(reason, "low_priority")

    def test_passes_medium_priority(self):
        passes, _ = extract.is_qualifying(self._article(priority="medium"))
        self.assertTrue(passes)

    def test_not_az_beats_low_priority_in_reason(self):
        """When both rules would fire, not_az wins (it's first in DROP_RULES)."""
        passes, reason = extract.is_qualifying(
            self._article(az_relevant=False, priority="low")
        )
        self.assertFalse(passes)
        self.assertEqual(reason, "not_az")


class TestEstimateDealSize(unittest.TestCase):
    def _article(self, **overrides):
        from schema import ExtractedArticle
        base = dict(
            title="x", published_date=None, summary_2sent="x",
            signal_type="lease", company_name="Acme",
            company_domain_guess=None, property_type="retail",
            address=None, city="Tempe", square_footage=None,
            dollar_value=None, unit_count=None,
            az_relevant=True, confidence=0.7,
            priority="high", filter_reason="x",
            service_angle="x",
        )
        base.update(overrides)
        return ExtractedArticle.model_validate(base)

    def test_sqft_x_rate_x_12(self):
        rates = {"retail": 1.50}
        value, basis = extract.estimate_deal_size(
            self._article(property_type="retail", square_footage=10_000),
            rates,
        )
        self.assertEqual(value, 180_000)  # 10000 * 1.50 * 12
        self.assertEqual(basis, "sqft")

    def test_returns_none_when_no_signal(self):
        value, basis = extract.estimate_deal_size(self._article(), {"retail": 1.50})
        self.assertIsNone(value)
        self.assertEqual(basis, "none")


if __name__ == "__main__":
    unittest.main()
