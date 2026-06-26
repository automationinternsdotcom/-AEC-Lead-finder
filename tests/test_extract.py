"""Tests for extract.py — text extraction only (no LLM)."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

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


# Real-article HTML reused by the resolution tests below.
_RESOLVED_HTML = """
<html><body><article>
<p>Tempe industrial development announced. The 200,000-square-foot project
covers two buildings on a 12-acre parcel near Loop 202. The developer
expects construction completion in Q3 2027. Tenants are not yet
announced but the project is targeting last-mile logistics users.</p>
</article></body></html>
"""


class TestResolveGoogleNews(unittest.TestCase):
    """Google News URLs are JS-redirects; httpx can't follow them. _resolve_google_news
    decodes the embedded publisher URL via the googlenewsdecoder library."""

    def test_non_google_news_url_passes_through_unchanged(self):
        url = "https://www.bisnow.com/phoenix/news/industrial/some-article"
        # gnewsdecoder is NOT called for non-Google-News URLs (asserted by patching it
        # to a function that would fail the test if invoked).
        with patch.object(extract, "gnewsdecoder",
                          side_effect=AssertionError("should not be called")):
            self.assertEqual(extract._resolve_google_news(url), url)

    def test_google_news_url_returns_decoded_publisher_url(self):
        publisher = "https://www.queencreektribune.com/business/example/"
        with patch.object(extract, "gnewsdecoder",
                          return_value={"status": True, "decoded_url": publisher}):
            result = extract._resolve_google_news(
                "https://news.google.com/rss/articles/CBMi-encoded?oc=5",
            )
        self.assertEqual(result, publisher)

    def test_decoder_failure_raises_extract_error(self):
        """Decoder returns {status: False, message: ...} when it can't resolve.
        Must surface as ExtractError so the caller marks the URL as failed
        instead of trying to fetch the redirect HTML."""
        with patch.object(extract, "gnewsdecoder",
                          return_value={"status": False, "message": "no match"}):
            with self.assertRaises(extract.ExtractError) as ctx:
                extract._resolve_google_news(
                    "https://news.google.com/rss/articles/CBMi-bad?oc=5",
                )
        self.assertIn("gnews_decode_failed", str(ctx.exception))

    def test_decoder_timeout_raises_extract_error(self):
        """The library has no built-in timeout; we wrap it in a ThreadPoolExecutor
        with a hard wall-clock cap. A hung resolution must produce ExtractError,
        not a hung process."""
        import time

        def slow(*args, **kwargs):
            # Just-long-enough sleep to exceed the patched timeout. Real-world
            # hangs would be longer, but a tight test sleep keeps suite fast.
            time.sleep(0.5)
            return {"status": True, "decoded_url": "x"}

        with patch.object(extract, "GNEWS_RESOLVE_TIMEOUT_SEC", 0.05):
            with patch.object(extract, "gnewsdecoder", side_effect=slow):
                with self.assertRaises(extract.ExtractError) as ctx:
                    extract._resolve_google_news(
                        "https://news.google.com/rss/articles/CBMi-slow?oc=5",
                    )
        self.assertIn("timeout", str(ctx.exception).lower())

    def test_extract_passes_resolved_url_to_http_get(self):
        """End-to-end: extract_article_text resolves the Google News URL FIRST,
        then GETs the publisher URL — not the redirect URL."""
        publisher = "https://www.azbex.com/article/x/"
        mock_http = MagicMock()
        mock_http.get.return_value.status_code = 200
        mock_http.get.return_value.text = _RESOLVED_HTML
        with patch.object(extract, "gnewsdecoder",
                          return_value={"status": True, "decoded_url": publisher}):
            extract.extract_article_text(
                "https://news.google.com/rss/articles/CBMi-x?oc=5", mock_http,
            )
        mock_http.get.assert_called_once_with(publisher)


class TestIsQualifying(unittest.TestCase):
    """is_qualifying operates on an ExtractedArticle from the assessment step."""

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
        news, rankings, etc.) should never reach delivery even if the assessment
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
