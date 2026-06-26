"""Tests for Gemini API response handling."""
from __future__ import annotations

import unittest

from pipeline.gemini_client import extract_text


class TestGeminiClient(unittest.TestCase):
    def test_extract_text_joins_candidate_parts(self):
        raw = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": '{"sources": ['},
                            {"text": '{"url": "https://example.com"}]}'},
                        ]
                    }
                }
            ]
        }
        self.assertEqual(
            extract_text(raw),
            '{"sources": [\n{"url": "https://example.com"}]}',
        )


if __name__ == "__main__":
    unittest.main()
