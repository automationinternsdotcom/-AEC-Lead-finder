"""Tests for pipeline/grok_parse.py — using fixed-shape Grok Fast-mode responses."""

from __future__ import annotations

import unittest

from pipeline import grok_parse


# Verbatim from the 2026-05-22 spike against Mark-Taylor Residential
SPIKE_RESPONSE = """1. Michael Wilson
Current Title: Chief Operating Officer (COO), Mark-Taylor, Inc. (verified current via company site and recent announcements).
LinkedIn: https://www.linkedin.com/in/michael-wilson-2a982625a
Professional Email: Likely michael.wilson@mark-taylor.com (common format: first.last@mark-taylor.com).⁠Rocketreach

2. Chris Madison (Christopher Madison)
Current Title: Director of Facilities & Renovation Management / Associate Director of Facilities, Mark-Taylor, Inc. (verified current via company leadership page).
LinkedIn: https://www.linkedin.com/in/christophermadison235
Professional Email: Likely chris.madison@mark-taylor.com or christopher.madison@mark-taylor.com (common format).⁠Rocketreach

These individuals align with high-priority roles (COO and Facilities leadership) with likely authority over janitorial/cleaning service contracts for multifamily properties.

95 sources"""


class TestParseGrokResponse(unittest.TestCase):
    def test_returns_first_entry_as_lead(self):
        lead = grok_parse.parse_grok_response(SPIKE_RESPONSE)
        self.assertIsNotNone(lead)
        self.assertEqual(lead.name, "Michael Wilson")
        self.assertIn("Chief Operating Officer", lead.title)
        self.assertEqual(lead.email, "michael.wilson@mark-taylor.com")
        self.assertEqual(lead.linkedin_url,
                         "https://www.linkedin.com/in/michael-wilson-2a982625a")
        self.assertEqual(lead.seniority, "c_suite")
        self.assertEqual(lead.apollo_id, "grok")
        self.assertIsNone(lead.phone)

    def test_handles_name_with_parenthetical(self):
        """Chris Madison (Christopher Madison) — keep the first name form only."""
        # Skip ahead so the parser sees entry 2 as the first
        from_entry_2 = SPIKE_RESPONSE.split("\n\n2.", 1)[1]
        lead = grok_parse.parse_grok_response("2." + from_entry_2)
        self.assertIsNotNone(lead)
        self.assertEqual(lead.name, "Chris Madison")  # parenthetical stripped

    def test_returns_none_on_empty_response(self):
        self.assertIsNone(grok_parse.parse_grok_response(""))

    def test_returns_none_when_no_entries(self):
        text = "Sorry, I couldn't find decision-makers at that company. 0 sources"
        self.assertIsNone(grok_parse.parse_grok_response(text))

    def test_email_field_handles_likely_prefix(self):
        text = "1. Jane Doe\nCurrent Title: VP Ops, Acme.\nProfessional Email: Likely jane@acme.com"
        lead = grok_parse.parse_grok_response(text)
        self.assertEqual(lead.email, "jane@acme.com")

    def test_email_field_handles_no_email(self):
        text = "1. Jane Doe\nCurrent Title: VP Ops, Acme.\nLinkedIn: https://linkedin.com/in/jane"
        lead = grok_parse.parse_grok_response(text)
        self.assertIsNotNone(lead)
        self.assertIsNone(lead.email)
        self.assertEqual(lead.linkedin_url, "https://linkedin.com/in/jane")

    def test_picks_first_email_when_two_offered(self):
        """Grok often gives 'X or Y' — take X."""
        text = "1. Chris Madison\nCurrent Title: Director of Facilities, Mark-Taylor.\nProfessional Email: Likely chris.madison@mark-taylor.com or christopher.madison@mark-taylor.com"
        lead = grok_parse.parse_grok_response(text)
        self.assertEqual(lead.email, "chris.madison@mark-taylor.com")


class TestDeriveSeniority(unittest.TestCase):
    def test_owner(self):
        self.assertEqual(grok_parse._derive_seniority("Owner, Acme"), "owner")
        self.assertEqual(grok_parse._derive_seniority("Founder & CEO"), "owner")
        self.assertEqual(grok_parse._derive_seniority("Principal"), "owner")

    def test_c_suite(self):
        self.assertEqual(grok_parse._derive_seniority("Chief Operating Officer"), "c_suite")
        self.assertEqual(grok_parse._derive_seniority("COO"), "c_suite")
        self.assertEqual(grok_parse._derive_seniority("CFO"), "c_suite")

    def test_vp(self):
        self.assertEqual(grok_parse._derive_seniority("VP of Facilities"), "vp")
        self.assertEqual(grok_parse._derive_seniority("Vice President, Ops"), "vp")

    def test_director(self):
        self.assertEqual(grok_parse._derive_seniority("Director of Facilities & Renovation"), "director")

    def test_manager(self):
        self.assertEqual(grok_parse._derive_seniority("Property Manager"), "manager")
        self.assertEqual(grok_parse._derive_seniority("Operations Manager"), "manager")

    def test_unknown(self):
        self.assertEqual(grok_parse._derive_seniority("Some Random Title"), "")


if __name__ == "__main__":
    unittest.main()
