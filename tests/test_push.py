"""Tests for pipeline/push.py — httpx.MockTransport to avoid network."""

from __future__ import annotations

import json
import unittest
from typing import Callable
from unittest.mock import patch

import httpx

from pipeline import config, push
from pipeline.enrich import Lead


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


class TestNoteBodyFormatting(unittest.TestCase):
    """The Pipedrive note assembles fields with newlines as line breaks. Any
    interpolated field containing its own newline would break the line
    structure (every embedded \\n becomes a top-level row in Jordan's view)."""

    def test_multiline_filter_reason_collapses_to_single_line(self):
        from pipeline.push import _note_body
        art = _article(filter_reason="Line one.\nSecondary line that would break the note.")
        body = _note_body(art, lead=None, basis="none", url="https://x.com/a")
        lines = body.split("\n")
        # No line in the output should be just the secondary text from filter_reason
        for line in lines:
            self.assertNotEqual(line.strip(), "Secondary line that would break the note.")
        # The collapsed filter_reason content must still be present somewhere
        self.assertIn("Secondary line that would break the note.", body)

    def test_multiline_summary_2sent_collapses(self):
        from pipeline.push import _note_body
        art = _article(summary_2sent="First sentence.\nSecond on its own line.")
        body = _note_body(art, lead=None, basis="none", url="https://x.com/a")
        # The summary line should appear as a single concatenated line
        first_line = body.splitlines()[0]
        self.assertIn("First sentence.", first_line)
        self.assertIn("Second on its own line.", first_line)

    def test_multiline_service_angle_collapses(self):
        from pipeline.push import _note_body
        art = _article(service_angle="Reach out.\nLine two.")
        body = _note_body(art, lead=None, basis="none", url="https://x.com/a")
        # Find the Aether angle line; it should contain BOTH halves on one line
        aether_lines = [l for l in body.splitlines() if l.startswith("Aether angle:")]
        self.assertEqual(len(aether_lines), 1)
        self.assertIn("Reach out.", aether_lines[0])
        self.assertIn("Line two.", aether_lines[0])

    def test_lead_gap_uses_human_readable_text(self):
        """'lead_gap' was internal jargon visible to Jordan in the note. Use
        plainer language so the note reads naturally."""
        from pipeline.push import _note_body
        art = _article()
        body = _note_body(art, lead=None, basis="none", url="https://x.com/a")
        self.assertNotIn("lead_gap", body)
        # The Contact line should explain absence in plain language
        contact_lines = [l for l in body.splitlines() if l.startswith("Contact:")]
        self.assertEqual(len(contact_lines), 1)
        self.assertIn("no decision-maker", contact_lines[0].lower())


class TestSchemaValidation(unittest.TestCase):
    """Tightened schema rules (commit reviewed-as-LOW findings 3 & 4):
       - filter_reason must be non-empty (audit trail can't be blank)
       - service_angle must be non-empty when priority is high/medium
         (the Aether-voice line in the note depends on it)
    """

    def _payload(self, **overrides):
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
        return base

    def test_rejects_empty_filter_reason(self):
        from schema import ExtractedArticle
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            ExtractedArticle.model_validate(self._payload(filter_reason=""))

    def test_rejects_whitespace_only_filter_reason(self):
        from schema import ExtractedArticle
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            ExtractedArticle.model_validate(self._payload(filter_reason="   "))

    def test_rejects_empty_service_angle_when_high_priority(self):
        from schema import ExtractedArticle
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            ExtractedArticle.model_validate(
                self._payload(priority="high", service_angle="")
            )

    def test_rejects_null_service_angle_when_high_priority(self):
        from schema import ExtractedArticle
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            ExtractedArticle.model_validate(
                self._payload(priority="high", service_angle=None)
            )

    def test_rejects_empty_service_angle_when_medium_priority(self):
        from schema import ExtractedArticle
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            ExtractedArticle.model_validate(
                self._payload(priority="medium", service_angle="")
            )

    def test_allows_null_service_angle_when_low_priority(self):
        """Low articles get dropped before push so service_angle is irrelevant."""
        from schema import ExtractedArticle
        ExtractedArticle.model_validate(
            self._payload(priority="low", service_angle=None)
        )


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


class TestArticleTitleAndExtraFields(unittest.TestCase):
    """Lead title = article headline; Date Posted + Lead 1/2/3 populated when
    field hashes are configured, silently skipped when blank."""

    def _settings_with_extras(self) -> config.Settings:
        return config.Settings(
            pipedrive_api_token="t", pipedrive_domain="d",
            pipedrive_field_article_url="art-url-hash",
            pipedrive_field_date_posted="date-hash",
            pipedrive_field_lead_1="l1-hash",
            pipedrive_field_lead_2="l2-hash",
            pipedrive_field_lead_3="l3-hash",
            pipedrive_field_lead_1_linkedin="l1-li-hash",
            pipedrive_field_lead_2_linkedin="l2-li-hash",
            pipedrive_field_lead_3_linkedin="l3-li-hash",
        )

    def _lead(self, name="Jane Doe", title="VP Ops", email="jane@x.com", phone=None, linkedin_url=None):
        from pipeline.enrich import Lead
        return Lead(name=name, title=title, email=email, phone=phone,
                    linkedin_url=linkedin_url, seniority="vp", apollo_id="grok")

    def test_lead_title_is_article_headline(self):
        art = _article(title="Foundation 8 plans $120M redevelopment of Sheraton Crescent")
        self.assertEqual(
            push._lead_title(art),
            "Foundation 8 plans $120M redevelopment of Sheraton Crescent",
        )

    def test_lead_title_truncated_to_255_chars(self):
        long_title = "x" * 300
        art = _article(title=long_title)
        self.assertEqual(len(push._lead_title(art)), 255)

    def test_lead_payload_includes_date_posted_when_published_date_set(self):
        art = _article(published_date="2026-05-11")
        payload = push._lead_payload(
            art, est_value=None, org_id=1, person_id=None,
            settings=self._settings_with_extras(), url="https://x.com/a",
        )
        self.assertEqual(payload["date-hash"], "2026-05-11")

    def test_lead_payload_omits_date_posted_when_published_date_null(self):
        art = _article(published_date=None)
        payload = push._lead_payload(
            art, est_value=None, org_id=1, person_id=None,
            settings=self._settings_with_extras(), url="https://x.com/a",
        )
        self.assertNotIn("date-hash", payload)

    def test_lead_payload_omits_date_posted_when_field_hash_unset(self):
        """If PIPEDRIVE_FIELD_DATE_POSTED isn't configured, never write it —
        we can't guess the hash and a wrong key would 400."""
        art = _article(published_date="2026-05-11")
        payload = push._lead_payload(
            art, est_value=None, org_id=1, person_id=None,
            settings=_settings(),  # default: no date_posted hash
            url="https://x.com/a",
        )
        # No custom field added beyond article_url
        self.assertNotIn("date-hash", payload)

    def test_lead_payload_populates_lead_1_2_3_in_order(self):
        contacts = [
            self._lead("Alice A", "COO", "alice@x.com"),
            self._lead("Bob B", "VP Facilities", "bob@x.com"),
            self._lead("Carol C", "Asset Manager", "carol@x.com"),
        ]
        payload = push._lead_payload(
            _article(), est_value=None, org_id=1, person_id=None,
            settings=self._settings_with_extras(),
            url="https://x.com/a", contacts=contacts,
        )
        self.assertEqual(payload["l1-hash"], "Alice A | COO | alice@x.com")
        self.assertEqual(payload["l2-hash"], "Bob B | VP Facilities | bob@x.com")
        self.assertEqual(payload["l3-hash"], "Carol C | Asset Manager | carol@x.com")

    def test_lead_payload_populates_individual_linkedin_fields(self):
        contacts = [
            self._lead("Alice A", "COO", "alice@x.com", linkedin_url="https://linkedin.com/in/alice"),
            self._lead("Bob B", "VP Facilities", "bob@x.com", linkedin_url="https://linkedin.com/in/bob"),
        ]
        payload = push._lead_payload(
            _article(), est_value=None, org_id=1, person_id=None,
            settings=self._settings_with_extras(),
            url="https://x.com/a", contacts=contacts,
        )
        self.assertEqual(payload["l1-li-hash"], "https://linkedin.com/in/alice")
        self.assertEqual(payload["l2-li-hash"], "https://linkedin.com/in/bob")
        self.assertNotIn("l3-li-hash", payload)

    def test_lead_payload_caps_extra_contacts_at_3(self):
        # Realistic two-token names so is_lead_generic doesn't filter them.
        contacts = [self._lead(f"Person {i}A") for i in range(5)]
        all_contacts = push._all_contacts(None, contacts)
        self.assertEqual(len(all_contacts), 3)

    def test_all_contacts_filters_generic_placeholders(self):
        """Defensive filter: even if the enricher passes a "Property Manager"
        placeholder through, push.py drops it so it never lands in Lead 1/2/3.
        See pipeline.grok_parse.is_lead_generic for the heuristic."""
        good = self._lead("Real Person")
        generic = self._lead("Property Manager")
        out = push._all_contacts(generic, [good])
        self.assertEqual([c.name for c in out], ["Real Person"])

    def test_lead_payload_fewer_than_3_contacts_leaves_remaining_blank(self):
        contacts = [self._lead("Alice A", "COO", "alice@x.com")]
        payload = push._lead_payload(
            _article(), est_value=None, org_id=1, person_id=None,
            settings=self._settings_with_extras(),
            url="https://x.com/a", contacts=contacts,
        )
        self.assertEqual(payload["l1-hash"], "Alice A | COO | alice@x.com")
        self.assertNotIn("l2-hash", payload)
        self.assertNotIn("l3-hash", payload)

    def test_fmt_contact_omits_null_parts(self):
        lead = self._lead(name="Solo", title=None, email=None, phone=None)
        self.assertEqual(push._fmt_contact(lead), "Solo")

    def test_fmt_contact_includes_phone(self):
        lead = self._lead(name="Pat", title="Mgr", email=None, phone="480-555-0000")
        self.assertEqual(push._fmt_contact(lead), "Pat | Mgr | 480-555-0000")

    def test_fmt_contact_includes_linkedin_url(self):
        """Jon specifically asked whether LinkedIn URLs survive Grok -> app ->
        Pipedrive. Lead 1/2/3 text is the visible Pipedrive surface, so the
        LinkedIn URL must be included there."""
        lead = self._lead(
            name="Pat Smith",
            title="Director of Facilities",
            email="pat@example.com",
            phone="480-555-0000",
        )
        lead.linkedin_url = "https://www.linkedin.com/in/pat-smith"
        self.assertEqual(
            push._fmt_contact(lead),
            "Pat Smith | Director of Facilities | LinkedIn: https://www.linkedin.com/in/pat-smith | pat@example.com | 480-555-0000",
        )

    def test_all_contacts_primary_then_extras(self):
        """Primary (the email-matching Person) goes first, extras fill the rest."""
        primary = self._lead("Primary P")
        extras = [self._lead("Extra E"), self._lead("Other O")]
        out = push._all_contacts(primary, extras)
        self.assertEqual([c.name for c in out], ["Primary P", "Extra E", "Other O"])

    def test_all_contacts_empty_when_no_inputs(self):
        self.assertEqual(push._all_contacts(None, None), [])
        self.assertEqual(push._all_contacts(None, []), [])


class TestFindLeadByUrl(unittest.TestCase):
    def test_returns_uuid_when_found(self):
        """find_lead_by_url truncates the search term (Pipedrive rejects >255
        char terms) and post-filters items by exact custom_field URL match."""
        captured = {}

        def handler(request):
            captured["url"] = str(request.url)
            return httpx.Response(200, json={
                "success": True,
                "data": {"items": [{"item": {
                    "id": "abc-uuid-123",
                    "custom_fields": ["https://example.com/x"],
                }}]},
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
        finally:
            client.__exit__()

    def test_returns_uuid_when_custom_field_is_dict(self):
        """Pipedrive sometimes wraps each custom_field value in a {value: ...}
        dict instead of a flat string. Both shapes must match."""
        def handler(request):
            return httpx.Response(200, json={
                "success": True,
                "data": {"items": [{"item": {
                    "id": "xyz-uuid",
                    "custom_fields": [{"value": "https://example.com/x"}],
                }}]},
            })

        client = _client_with(handler)
        try:
            self.assertEqual(client.find_lead_by_url("https://example.com/x"), "xyz-uuid")
        finally:
            client.__exit__()

    def test_matches_when_stored_url_truncated_to_255(self):
        """push.py caps the Article URL field at 255 chars. A lead created from
        an unresolvable >255-char Google News URL stores the truncation, so the
        dedup match must compare against article_url[:255] — otherwise the lead
        is never found and gets re-created (the prod-duplicate bug)."""
        long_url = "https://news.google.com/rss/articles/" + "A" * 300
        stored = long_url[:255]

        def handler(request):
            return httpx.Response(200, json={"success": True, "data": {"items": [
                {"item": {"id": "trunc-uuid", "custom_fields": {"test-field-key": stored}}}]}})

        client = _client_with(handler)
        try:
            self.assertEqual(client.find_lead_by_url(long_url), "trunc-uuid")
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

    def test_returns_none_when_item_url_does_not_match(self):
        """Truncated-prefix collisions in the search index resolve to the
        full URL — we must NOT count a different URL as a dedup hit."""
        def handler(request):
            return httpx.Response(200, json={
                "success": True,
                "data": {"items": [{"item": {
                    "id": "other-uuid",
                    "custom_fields": ["https://example.com/different"],
                }}]},
            })

        client = _client_with(handler)
        try:
            self.assertIsNone(client.find_lead_by_url("https://example.com/x"))
        finally:
            client.__exit__()

    def test_returns_none_on_400_too_long_term(self):
        """Pipedrive 400s on overlong terms even after truncation in some
        edge cases. We tolerate 400 as 'not found' so push proceeds — seen_urls
        is the primary dedup gate anyway."""
        def handler(request):
            return httpx.Response(400, json={"error": "term too long"})

        client = _client_with(handler)
        try:
            self.assertIsNone(client.find_lead_by_url("https://example.com/x"))
        finally:
            client.__exit__()


class TestSyncToPipedriveSkipsWhenLeadExists(unittest.TestCase):
    def test_returns_none_none_existing_uuid_on_dedup_hit(self):
        """When find_lead_by_url returns a UUID, sync skips and returns it."""

        def handler(request):
            # Always responds with one match — simulates dedup hit.
            return httpx.Response(200, json={
                "success": True,
                "data": {"items": [{"item": {
                    "id": "existing-uuid-xyz",
                    "custom_fields": ["https://example.com/dup"],
                }}]},
            })

        # Capture the real class so _client_with doesn't recurse when patched.
        RealClient = push.PipedriveClient

        def make_mock_client(s):
            client = RealClient.__new__(RealClient)
            client._settings = s
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


class TestVerifiedOnlyContactPolicy(unittest.TestCase):
    """Production policy: only VERIFIED email/phone attach to the Person record;
    hedged 'Likely' guesses appear only in the Lead 1/2/3 reference text."""

    def _lead(self, **kw):
        base = dict(name="Jane Doe", title="COO", email="jane@acme.com",
                    phone="480-555-1212", linkedin_url=None, seniority="c_suite",
                    apollo_id="grok")
        base.update(kw)
        return Lead(**base)

    def test_unverified_email_excluded_from_person_payload(self):
        captured = {}
        def handler(req: httpx.Request) -> httpx.Response:
            if req.url.path.endswith("/persons") and req.method == "POST":
                captured["body"] = json.loads(req.content)
                return httpx.Response(201, json={"data": {"id": 7}})
            return httpx.Response(200, json={"data": {"items": []}})
        client = _client_with(handler)
        lead = self._lead(email_verified=False, phone_verified=True)
        push._upsert_person(client, lead, org_id=1)
        # hedged email dropped; verified phone kept
        self.assertEqual(captured["body"]["email"], [])
        self.assertEqual(captured["body"]["phone"], [{"value": "480-555-1212"}])

    def test_verified_email_included_in_person_payload(self):
        captured = {}
        def handler(req: httpx.Request) -> httpx.Response:
            if req.url.path.endswith("/persons/search"):
                return httpx.Response(200, json={"data": {"items": []}})
            captured["body"] = json.loads(req.content)
            return httpx.Response(201, json={"data": {"id": 8}})
        client = _client_with(handler)
        push._upsert_person(client, self._lead(email_verified=True, phone_verified=True), org_id=1)
        self.assertEqual(captured["body"]["email"], [{"value": "jane@acme.com"}])
        self.assertEqual(captured["body"]["phone"], [{"value": "480-555-1212"}])

    def test_fmt_contact_marks_hedged_email_as_likely(self):
        lead = self._lead(email="jane@acme.com", email_verified=False, phone=None)
        out = push._fmt_contact(lead)
        self.assertIn("Likely jane@acme.com", out)

    def test_fmt_contact_verified_email_unmarked(self):
        lead = self._lead(email="jane@acme.com", email_verified=True, phone=None)
        out = push._fmt_contact(lead)
        self.assertIn("jane@acme.com", out)
        self.assertNotIn("Likely", out)


class TestUpdaterClientVerbs(unittest.TestCase):
    """PipedriveClient gains get/put/patch for the updater (the generic _req
    already supports any verb)."""

    def test_get_put_patch_issue_correct_verbs(self):
        seen = []
        def handler(req: httpx.Request) -> httpx.Response:
            seen.append((req.method, req.url.path))
            return httpx.Response(200, json={"success": True, "data": {"id": 1}})
        c = _client_with(handler)
        c.get("leads", "L1")
        c.put("persons", 9, {"email": [{"value": "x@y.com"}]})
        c.patch("leads", "L1", {"h": "v"})
        self.assertEqual(seen, [
            ("GET", "/api/v1/leads/L1"),
            ("PUT", "/api/v1/persons/9"),
            ("PATCH", "/api/v1/leads/L1"),
        ])


class TestUpdateLeadContacts(unittest.TestCase):
    """update_lead_contacts() finds an existing Lead by Article URL, then
    PUTs the Person (verified-only) and PATCHes the Lead 1/2/3 fields."""

    def _settings(self) -> config.Settings:
        return config.Settings(
            pipedrive_api_token="t", pipedrive_domain="test-co",
            pipedrive_field_article_url="art-url-hash",
            pipedrive_field_lead_1="l1-hash", pipedrive_field_lead_2="l2-hash",
            pipedrive_field_lead_3="l3-hash",
            pipedrive_field_lead_1_linkedin="l1-li-hash",
        )

    def _run(self, primary, extras, handler):
        RealClient = push.PipedriveClient
        def make(s):
            c = RealClient.__new__(RealClient)
            c._settings = s
            c._http = httpx.Client(
                base_url=f"https://{s.pipedrive_domain}.pipedrive.com/api/v1/",
                params={"api_token": s.pipedrive_api_token},
                transport=httpx.MockTransport(handler),
            )
            return c
        with patch.object(push, "PipedriveClient", make):
            return push.update_lead_contacts("https://ex.com/a", primary, extras, self._settings())

    def _hit_handler(self, bodies):
        def handler(req: httpx.Request) -> httpx.Response:
            p = req.url.path
            if p.endswith("/leads/search"):
                return httpx.Response(200, json={"success": True, "data": {"items": [
                    {"item": {"id": "L1", "custom_fields": {"art-url-hash": "https://ex.com/a"}}}]}})
            if p == "/api/v1/leads/L1" and req.method == "GET":
                return httpx.Response(200, json={"success": True, "data": {"id": "L1", "person_id": 9}})
            if p == "/api/v1/persons/9" and req.method == "PUT":
                bodies["person"] = json.loads(req.content)
                return httpx.Response(200, json={"success": True, "data": {"id": 9}})
            if p == "/api/v1/leads/L1" and req.method == "PATCH":
                bodies["lead"] = json.loads(req.content)
                return httpx.Response(200, json={"success": True, "data": {"id": "L1"}})
            return httpx.Response(200, json={"success": True, "data": {}})
        return handler

    def test_verified_contact_updates_person_and_lead_fields(self):
        bodies = {}
        primary = Lead(name="Jane Doe", title="COO", email="jane@acme.com", phone="480-555-1212",
                       linkedin_url="https://linkedin.com/in/jane", seniority="c_suite", apollo_id="grok",
                       email_verified=True, phone_verified=True)
        result = self._run(primary, [], self._hit_handler(bodies))
        self.assertEqual(bodies["person"]["email"], [{"value": "jane@acme.com"}])
        self.assertEqual(bodies["person"]["phone"], [{"value": "480-555-1212"}])
        self.assertEqual(
            bodies["lead"]["l1-hash"],
            "Jane Doe | COO | LinkedIn: https://linkedin.com/in/jane | jane@acme.com | 480-555-1212",
        )
        self.assertEqual(bodies["lead"]["l1-li-hash"], "https://linkedin.com/in/jane")
        self.assertTrue(result["updated"])

    def test_hedged_email_kept_out_of_person_but_marked_in_lead_text(self):
        bodies = {}
        primary = Lead(name="Jane Doe", title="COO", email="jane@acme.com", phone=None,
                       linkedin_url=None, seniority="c_suite", apollo_id="grok",
                       email_verified=False, phone_verified=False)
        self._run(primary, [], self._hit_handler(bodies))
        self.assertNotIn("person", bodies)  # nothing verified → no Person PUT
        self.assertIn("Likely jane@acme.com", bodies["lead"]["l1-hash"])

    def test_returns_not_found_when_lead_absent(self):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"success": True, "data": {"items": []}})
        result = self._run(None, None, handler)
        self.assertFalse(result["updated"])


class TestSyncAutomations(unittest.TestCase):
    """When enabled, lead creation should create follow-up tasks, not only a
    note. Default stays disabled so production behavior doesn't surprise us."""

    def test_sync_creates_followup_activities_when_enabled(self):
        settings = config.Settings(
            pipedrive_api_token="t",
            pipedrive_domain="test-co",
            pipedrive_field_article_url="art-url-hash",
            pipedrive_enable_automations=True,
            pipedrive_automation_owner_id=123,
        )
        seen = []

        def handler(req: httpx.Request) -> httpx.Response:
            p = req.url.path
            if req.method == "GET" and p.endswith("/search"):
                return httpx.Response(200, json={"success": True, "data": {"items": []}})
            if req.method == "POST" and p == "/api/v1/organizations":
                return httpx.Response(201, json={"success": True, "data": {"id": 10}})
            if req.method == "POST" and p == "/api/v1/persons":
                return httpx.Response(201, json={"success": True, "data": {"id": 20}})
            if req.method == "POST" and p == "/api/v1/leads":
                return httpx.Response(201, json={"success": True, "data": {"id": "L1"}})
            if req.method == "POST" and p == "/api/v1/notes":
                return httpx.Response(201, json={"success": True, "data": {"id": 30}})
            if req.method == "POST" and p == "/api/v2/activities":
                seen.append(json.loads(req.content))
                return httpx.Response(201, json={"success": True, "data": {"id": len(seen)}})
            return httpx.Response(404, json={"success": False, "error": p})

        RealClient = push.PipedriveClient

        def make(s):
            c = RealClient.__new__(RealClient)
            c._settings = s
            c._http = httpx.Client(
                base_url=f"https://{s.pipedrive_domain}.pipedrive.com/api/v1/",
                params={"api_token": s.pipedrive_api_token},
                transport=httpx.MockTransport(handler),
            )
            return c

        lead = Lead(name="Jane Doe", title="COO", email="jane@acme.com",
                    phone="480-555-1212", linkedin_url="https://linkedin.com/in/jane",
                    seniority="c_suite", apollo_id="grok",
                    email_verified=True, phone_verified=True)
        with patch.object(push, "PipedriveClient", make):
            push.sync_to_pipedrive(
                _article(service_angle="Book turnover-readiness review."),
                lead=lead,
                extra_contacts=[],
                est_value=100_000,
                basis="sqft",
                url="https://example.com/a",
                settings=settings,
            )

        self.assertEqual([a["type"] for a in seen], ["call", "email"])
        self.assertEqual(seen[0]["lead_id"], "L1")
        self.assertEqual(seen[0]["owner_id"], 123)
        self.assertIn("Book turnover-readiness review.", seen[0]["note"])
        self.assertIn("https://linkedin.com/in/jane", seen[1]["note"])


if __name__ == "__main__":
    unittest.main()
