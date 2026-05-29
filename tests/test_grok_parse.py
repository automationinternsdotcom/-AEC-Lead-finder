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


# Verbatim from a 2026-05-28 live Fast-mode query for Top Fuel Espresso.
# Note the drift from SPIKE_RESPONSE: the title is INLINE on the numbered name
# line (no separate "Title:" line) and the labels are lowercase ("Professional
# email:", "Direct phone:"). This is the shape the parser was silently dropping.
INLINE_TITLE_RESPONSE = """1. Jessica Denison, Owner / Business Owner (Top Fuel Espresso)
LinkedIn: https://www.linkedin.com/in/jessica-denison-517817124
Professional email: Likely jessican@tfespresso.us or similar (hedged; personal often je****n@gmail.com via data providers)
Direct phone: Not found (main store line (480) 599-2749)

No other strong matches for priority roles like GM/Operations Manager with public contact details for the Mesa location(s).

105 sources"""


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


class TestIsLeadGeneric(unittest.TestCase):
    """Heuristic: detect job-title placeholders so the enricher escalates to
    Heavy mode instead of pushing them into Lead 1/2/3 fields."""

    def _lead(self, name):
        from pipeline.enrich import Lead
        return Lead(name=name, title="X", email=None, phone=None,
                    linkedin_url=None, seniority="", apollo_id="grok")

    def test_specific_person_is_not_generic(self):
        self.assertFalse(grok_parse.is_lead_generic(self._lead("Michael Wilson")))
        self.assertFalse(grok_parse.is_lead_generic(self._lead("Chris Madison")))
        self.assertFalse(grok_parse.is_lead_generic(self._lead("Erin L'Huillier")))

    def test_multiword_role_phrases_are_generic(self):
        self.assertTrue(grok_parse.is_lead_generic(self._lead("Property Manager")))
        self.assertTrue(grok_parse.is_lead_generic(self._lead("Asset Manager")))
        self.assertTrue(grok_parse.is_lead_generic(self._lead("Leasing Agent")))
        self.assertTrue(grok_parse.is_lead_generic(self._lead("The Property Manager")))

    def test_single_token_names_are_generic(self):
        """Single-name 'Owner' or 'Manager' is a role placeholder, not a person."""
        self.assertTrue(grok_parse.is_lead_generic(self._lead("Owner")))
        self.assertTrue(grok_parse.is_lead_generic(self._lead("Manager")))
        self.assertTrue(grok_parse.is_lead_generic(self._lead("Founder")))

    def test_org_suffix_names_are_generic(self):
        """'Acme Property Management' looks like a name but is an org placeholder."""
        self.assertTrue(grok_parse.is_lead_generic(self._lead("Acme Property Management")))
        self.assertTrue(grok_parse.is_lead_generic(self._lead("Operations Team")))
        self.assertTrue(grok_parse.is_lead_generic(self._lead("Facilities Group")))
        self.assertTrue(grok_parse.is_lead_generic(self._lead("Customer Services")))

    def test_empty_or_whitespace_is_generic(self):
        self.assertTrue(grok_parse.is_lead_generic(self._lead("")))
        self.assertTrue(grok_parse.is_lead_generic(self._lead("   ")))

    def test_case_insensitive(self):
        self.assertTrue(grok_parse.is_lead_generic(self._lead("PROPERTY MANAGER")))
        self.assertTrue(grok_parse.is_lead_generic(self._lead("property manager")))


class TestPhoneExtraction(unittest.TestCase):
    """Phone numbers come through varied label conventions and number formats.
    The parser captures the raw form and lets Pipedrive's Lead 1/2/3 text
    fields render it as-is."""

    def _parse_one(self, block):
        text = f"1. Jane Doe\nTitle: COO\n{block}\n"
        leads = grok_parse.parse_grok_response_all(text)
        return leads[0] if leads else None

    def test_extracts_us_parens_format(self):
        lead = self._parse_one("Direct Phone: (480) 555-1234")
        self.assertEqual(lead.phone, "(480) 555-1234")

    def test_extracts_dash_format(self):
        lead = self._parse_one("Phone: 480-555-1234")
        self.assertEqual(lead.phone, "480-555-1234")

    def test_extracts_intl_format(self):
        lead = self._parse_one("Direct: +1 480 555 1234")
        self.assertEqual(lead.phone, "+1 480 555 1234")

    def test_extracts_extension(self):
        lead = self._parse_one("Direct Dial: 480-555-1234 ext 102")
        self.assertIn("ext 102", lead.phone)

    def test_returns_none_when_absent(self):
        lead = self._parse_one("LinkedIn: https://example.com/x")
        self.assertIsNone(lead.phone)


class TestIsHighConfidenceContact(unittest.TestCase):
    """Trigger for Heavy-mode fallback: a 'high confidence' contact has both
    a verified (non-hedged, non-generic) email AND a phone number."""

    def _lead(self, **overrides):
        from pipeline.enrich import Lead
        base = {"name": "Jane Doe", "title": "COO", "email": None, "phone": None,
                "linkedin_url": None, "seniority": "", "apollo_id": "grok"}
        base.update(overrides)
        return Lead(**base)

    def test_email_and_phone_present_real_local_is_high_confidence(self):
        lead = self._lead(email="jane.doe@acme.com", phone="(480) 555-1234")
        self.assertTrue(grok_parse.is_high_confidence_contact(lead))

    def test_missing_phone_is_low_confidence(self):
        lead = self._lead(email="jane@acme.com", phone=None)
        self.assertFalse(grok_parse.is_high_confidence_contact(lead))

    def test_missing_email_is_low_confidence(self):
        lead = self._lead(email=None, phone="(480) 555-1234")
        self.assertFalse(grok_parse.is_high_confidence_contact(lead))

    def test_generic_local_part_is_low_confidence(self):
        """info@/contact@/sales@ are role mailboxes, not specific people."""
        for local in ("info", "contact", "sales", "support", "hello", "office"):
            with self.subTest(local=local):
                lead = self._lead(email=f"{local}@acme.com", phone="(480) 555-1234")
                self.assertFalse(grok_parse.is_high_confidence_contact(lead))

    def test_likely_hedged_email_is_low_confidence(self):
        """Grok prefixes guesses with 'Likely' — we still capture the address
        for human verification, but it's not high-confidence."""
        lead = self._lead(email="jane.doe@acme.com", phone="(480) 555-1234")
        raw_block = "1. Jane Doe\nTitle: COO\nEmail: Likely jane.doe@acme.com"
        self.assertFalse(grok_parse.is_high_confidence_contact(lead, raw_block))

    def test_likely_check_skipped_when_raw_block_empty(self):
        """Callers without the raw block (e.g. tests) get no Likely detection."""
        lead = self._lead(email="jane.doe@acme.com", phone="(480) 555-1234")
        self.assertTrue(grok_parse.is_high_confidence_contact(lead, ""))


class TestParseGrokResponseBlocks(unittest.TestCase):
    """parse_grok_response_blocks pairs each Lead with its raw text chunk so
    is_high_confidence_contact can detect 'Likely' hedging."""

    def test_returns_pairs_in_order(self):
        text = """1. Person One
Title: COO
Email: one@acme.com
Direct Phone: 480-555-0001

2. Person Two
Title: VP
Email: Likely two@acme.com
"""
        pairs = grok_parse.parse_grok_response_blocks(text)
        self.assertEqual(len(pairs), 2)
        self.assertEqual(pairs[0][0].name, "Person One")
        self.assertIn("Person One", pairs[0][1])
        self.assertIn("Likely", pairs[1][1])

    def test_empty_input(self):
        self.assertEqual(grok_parse.parse_grok_response_blocks(""), [])


class TestParseGrokResponseAll(unittest.TestCase):
    """`parse_grok_response_all` returns up to N leads in source order."""

    def test_returns_all_entries_up_to_max(self):
        leads = grok_parse.parse_grok_response_all(SPIKE_RESPONSE, max_leads=3)
        self.assertEqual(len(leads), 2)
        self.assertEqual(leads[0].name, "Michael Wilson")
        self.assertEqual(leads[1].name, "Chris Madison")

    def test_caps_at_max_leads(self):
        text = "\n".join(
            f"{i}. Person {i}\nCurrent Title: VP\nProfessional Email: p{i}@x.com"
            for i in range(1, 6)
        )
        leads = grok_parse.parse_grok_response_all(text, max_leads=3)
        self.assertEqual(len(leads), 3)
        self.assertEqual([l.name for l in leads], ["Person 1", "Person 2", "Person 3"])

    def test_accepts_short_title_and_email_form(self):
        """Real Grok responses use 'Title:' / 'Email:' (not 'Current Title:' /
        'Professional Email:'). Parser must accept both."""
        text = """1. Jane Doe
Title: COO, Acme
LinkedIn: https://linkedin.com/in/jdoe
Email: jane@acme.com
"""
        leads = grok_parse.parse_grok_response_all(text)
        self.assertEqual(len(leads), 1)
        self.assertEqual(leads[0].name, "Jane Doe")
        self.assertEqual(leads[0].title, "COO, Acme")
        self.assertEqual(leads[0].email, "jane@acme.com")

    def test_empty_input_returns_empty_list(self):
        self.assertEqual(grok_parse.parse_grok_response_all(""), [])
        self.assertEqual(grok_parse.parse_grok_response_all("just prose, no list"), [])

    def test_accepts_inline_title_and_lowercase_labels(self):
        """Live Grok Fast mode puts the title inline on the name line (no
        separate 'Title:' line) and uses lowercase 'email:' labels. The parser
        must extract the contact instead of silently dropping it."""
        leads = grok_parse.parse_grok_response_all(INLINE_TITLE_RESPONSE)
        self.assertEqual(len(leads), 1)
        lead = leads[0]
        self.assertEqual(lead.name, "Jessica Denison")
        self.assertIn("Owner", lead.title)
        self.assertEqual(lead.email, "jessican@tfespresso.us")
        self.assertEqual(
            lead.linkedin_url,
            "https://www.linkedin.com/in/jessica-denison-517817124",
        )
        self.assertEqual(lead.seniority, "owner")
        # "Direct phone: Not found ..." must NOT capture the switchboard number.
        self.assertIsNone(lead.phone)


if __name__ == "__main__":
    unittest.main()
