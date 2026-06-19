"""Tests for pipeline.email_digest — read Leads back out, render, send."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from pipeline import config, email_digest


def _settings(**overrides):
    base = dict(
        pipedrive_api_token="t", pipedrive_domain="acme",
        pipedrive_field_article_url="url_field",
        pipedrive_field_lead_1="lead1", pipedrive_field_lead_2="lead2",
        pipedrive_field_lead_3="lead3",
    )
    base.update(overrides)
    return config.Settings(**base)


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeClient:
    """Routes GET calls by path to canned payloads."""

    def __init__(self, leads_pages, orgs=None, notes=None):
        self._leads_pages = leads_pages       # list of /leads response bodies
        self._orgs = orgs or {}               # org_id -> name
        self._notes = notes or {}             # lead_id -> note content
        self.calls = []

    def get(self, path, params=None):
        self.calls.append((path, params))
        if path == "leads":
            start = (params or {}).get("start", 0)
            idx = 0 if start == 0 else 1
            return _Resp(self._leads_pages[idx] if idx < len(self._leads_pages) else {"data": []})
        if path.startswith("organizations/"):
            oid = path.split("/", 1)[1]
            return _Resp({"data": {"name": self._orgs.get(int(oid))}})
        if path == "notes":
            lead_id = (params or {}).get("lead_id")
            content = self._notes.get(lead_id)
            return _Resp({"data": [{"content": content}] if content else []})
        raise AssertionError(f"unexpected path: {path}")


_NOTE = (
    "New retail tower leasing up fast.\n"
    "Source: https://ex.com/a\n"
    "Signal: lease | Property: retail | City: Tempe | Priority: high\n"
    "Filter reason: active occupancy\n"
)


def _lead(**overrides):
    base = {
        "id": "uuid-1", "title": "Tempe retail tower",
        "add_time": "2026-06-17 09:00:00",
        "organization_id": 5,
        "value": {"amount": 120000, "currency": "USD"},
        "url_field": "https://ex.com/a",
        "lead1": "Jane Doe | Owner | jane@ex.com | 555-1212",
        "lead2": "Bob Roe | GM | Likely bob@ex.com",
    }
    base.update(overrides)
    return base


class TestParseNote(unittest.TestCase):
    def test_extracts_summary_and_signal_line(self):
        meta = email_digest._parse_note(_NOTE)
        self.assertEqual(meta["summary"], "New retail tower leasing up fast.")
        self.assertEqual(meta["signal"], "lease")
        self.assertEqual(meta["city"], "Tempe")
        self.assertEqual(meta["priority"], "high")

    def test_skips_source_line_as_summary(self):
        meta = email_digest._parse_note("Source: https://ex.com/a\nSignal: lease | City: Mesa | Priority: low")
        self.assertNotIn("summary", meta)
        self.assertEqual(meta["city"], "Mesa")

    def test_empty_note(self):
        self.assertEqual(email_digest._parse_note(""), {})


class TestContacts(unittest.TestCase):
    def test_collects_nonempty_lead_fields_in_order(self):
        s = _settings()
        contacts = email_digest._contacts(_lead(), s)
        self.assertEqual(contacts, [
            "Jane Doe | Owner | jane@ex.com | 555-1212",
            "Bob Roe | GM | Likely bob@ex.com",
        ])

    def test_unwraps_nested_value_dicts(self):
        s = _settings()
        contacts = email_digest._contacts(
            _lead(lead1={"value": "Jane | Owner"}, lead2=None), s)
        self.assertEqual(contacts, ["Jane | Owner"])


class TestListLeadsSince(unittest.TestCase):
    def test_filters_by_add_time_and_enriches(self):
        pages = [{
            "data": [
                _lead(),
                _lead(id="old", add_time="2026-05-01 00:00:00"),
            ],
            "additional_data": {"pagination": {"more_items_in_collection": False}},
        }]
        client = _FakeClient(pages, orgs={5: "Acme Holdings"}, notes={"uuid-1": _NOTE})
        leads = email_digest.list_leads_since(client, _settings(), datetime(2026, 6, 1, tzinfo=timezone.utc))
        self.assertEqual(len(leads), 1)
        ld = leads[0]
        self.assertEqual(ld.lead_id, "uuid-1")
        self.assertEqual(ld.org_name, "Acme Holdings")
        self.assertEqual(ld.city, "Tempe")
        self.assertEqual(ld.priority, "high")
        self.assertEqual(ld.value, 120000)
        self.assertEqual(len(ld.contacts), 2)
        self.assertIn("acme.pipedrive.com/leads/inbox/uuid-1", ld.lead_url)

    def test_paginates(self):
        pages = [
            {"data": [_lead(id="a")],
             "additional_data": {"pagination": {"more_items_in_collection": True, "next_start": 1}}},
            {"data": [_lead(id="b")],
             "additional_data": {"pagination": {"more_items_in_collection": False}}},
        ]
        client = _FakeClient(pages, orgs={5: "Acme"}, notes={})
        leads = email_digest.list_leads_since(client, _settings(), datetime(2026, 6, 1, tzinfo=timezone.utc))
        self.assertEqual({l.lead_id for l in leads}, {"a", "b"})

    def test_org_name_fetched_once_per_org(self):
        pages = [{
            "data": [_lead(id="a"), _lead(id="b")],
            "additional_data": {"pagination": {"more_items_in_collection": False}},
        }]
        client = _FakeClient(pages, orgs={5: "Acme"}, notes={})
        email_digest.list_leads_since(client, _settings(), datetime(2026, 6, 1, tzinfo=timezone.utc))
        org_calls = [c for c in client.calls if c[0].startswith("organizations/")]
        self.assertEqual(len(org_calls), 1)


class TestWatermarkHelpers(unittest.TestCase):
    def test_parse_watermark_roundtrip(self):
        from pipeline import util
        dt = email_digest.parse_watermark(util.utc_now_iso())
        self.assertEqual(dt.tzinfo, timezone.utc)

    def test_parse_watermark_known_value(self):
        dt = email_digest.parse_watermark("2026-06-17T09:30:00Z")
        self.assertEqual(
            dt, datetime(2026, 6, 17, 9, 30, 0, tzinfo=timezone.utc))

    def test_start_of_today_is_midnight_utc(self):
        d = email_digest.start_of_today_utc()
        self.assertEqual((d.hour, d.minute, d.second, d.microsecond), (0, 0, 0, 0))
        self.assertEqual(d.tzinfo, timezone.utc)

    def test_lead_add_dt_parses_datetime_and_date(self):
        self.assertEqual(
            email_digest._lead_add_dt({"add_time": "2026-06-17 09:30:00"}),
            datetime(2026, 6, 17, 9, 30, 0, tzinfo=timezone.utc))
        self.assertEqual(
            email_digest._lead_add_dt({"add_time": "2026-06-17"}),
            datetime(2026, 6, 17, 0, 0, 0, tzinfo=timezone.utc))
        self.assertIsNone(email_digest._lead_add_dt({}))

    def test_filters_at_second_granularity(self):
        pages = [{
            "data": [
                _lead(id="kept", add_time="2026-06-17 09:00:01"),
                _lead(id="dropped", add_time="2026-06-17 08:59:59"),
            ],
            "additional_data": {"pagination": {"more_items_in_collection": False}},
        }]
        client = _FakeClient(pages, orgs={5: "Acme"}, notes={})
        cutoff = datetime(2026, 6, 17, 9, 0, 0, tzinfo=timezone.utc)
        leads = email_digest.list_leads_since(client, _settings(), cutoff)
        self.assertEqual({l.lead_id for l in leads}, {"kept"})


class TestRendering(unittest.TestCase):
    def _one(self):
        return email_digest.DigestLead(
            lead_id="uuid-1", title="Tempe retail tower",
            add_time="2026-06-17 09:00:00", org_name="Acme Holdings",
            city="Tempe", signal="lease", priority="high",
            summary="Leasing up fast.", value=120000, currency="USD",
            article_url="https://ex.com/a",
            contacts=["Jane Doe | Owner | jane@ex.com"],
            lead_url="https://acme.pipedrive.com/leads/inbox/uuid-1",
        )

    def test_html_includes_table_and_contacts(self):
        html = email_digest.render_html([self._one()], "today (2026-06-17)")
        self.assertIn("<table", html)
        self.assertIn("Tempe retail tower", html)
        self.assertIn("Jane Doe | Owner | jane@ex.com", html)
        self.assertIn("$120,000", html)
        self.assertIn('href="https://ex.com/a"', html)

    def test_html_escapes_titles(self):
        ld = self._one()
        ld.title = "Tower <script>alert(1)</script>"
        ld.article_url = None
        html = email_digest.render_html([ld], "today")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_text_lists_all_contacts(self):
        ld = self._one()
        ld.contacts = ["Jane | Owner", "Bob | GM"]
        text = email_digest.render_text([ld], "today")
        self.assertIn("Jane | Owner", text)
        self.assertIn("Bob | GM", text)
        self.assertIn("1 new lead(s)", text)


class TestSend(unittest.TestCase):
    def _leads(self):
        return [TestRendering()._one()]

    def test_raises_when_not_configured(self):
        msg = email_digest.build_message(self._leads(), "today",
                                         _settings(smtp_host=None, lead_digest_to=None))
        with self.assertRaises(email_digest.SmtpNotConfigured):
            email_digest.send_digest(msg, _settings(smtp_host=None))

    def test_starttls_path(self):
        s = _settings(smtp_host="smtp.x.com", smtp_port=587, smtp_user="u",
                      smtp_password="p", lead_digest_to="jw@x.com",
                      lead_digest_from="bot@x.com")
        msg = email_digest.build_message(self._leads(), "today", s)
        fake = MagicMock()
        with patch("pipeline.email_digest.smtplib.SMTP") as smtp_cls:
            smtp_cls.return_value.__enter__.return_value = fake
            email_digest.send_digest(msg, s)
        fake.starttls.assert_called_once()
        fake.login.assert_called_once_with("u", "p")
        fake.send_message.assert_called_once()

    def test_ssl_path_for_465(self):
        s = _settings(smtp_host="smtp.x.com", smtp_port=465,
                      lead_digest_to="jw@x.com", lead_digest_from="bot@x.com")
        msg = email_digest.build_message(self._leads(), "today", s)
        fake = MagicMock()
        with patch("pipeline.email_digest.smtplib.SMTP_SSL") as smtp_cls:
            smtp_cls.return_value.__enter__.return_value = fake
            email_digest.send_digest(msg, s)
        fake.send_message.assert_called_once()
        fake.starttls.assert_not_called()


class TestListRawLeadsSince(unittest.TestCase):
    def test_paginates_and_filters_by_add_time(self):
        from datetime import datetime, timezone
        from unittest import mock
        from pipeline import email_digest, config
        import os

        config.settings.cache_clear()
        os.environ.setdefault("PIPEDRIVE_API_TOKEN", "t")
        os.environ.setdefault("PIPEDRIVE_DOMAIN", "d")
        os.environ.setdefault("PIPEDRIVE_FIELD_ARTICLE_URL", "f")
        settings = config.settings()

        page = {"data": [
            {"id": "old", "add_time": "2026-05-01 00:00:00"},
            {"id": "new", "add_time": "2026-06-01 00:00:00"},
        ], "additional_data": {"pagination": {"more_items_in_collection": False}}}
        http = mock.Mock()
        http.get.return_value.json.return_value = page
        http.get.return_value.raise_for_status.return_value = None

        since = datetime(2026, 5, 29, tzinfo=timezone.utc)
        out = email_digest.list_raw_leads_since(http, settings, since)
        self.assertEqual([l["id"] for l in out], ["new"])
        config.settings.cache_clear()


class TestParseNoteStripsHtml(unittest.TestCase):
    def test_br_space_variant_and_tags_stripped(self):
        from pipeline import email_digest
        note = (
            "Plaza did things. SkySong 2.<br />\n"
            'Source: <a href="http://x">x</a><br />\n'
            "Signal: lease | City: Scottsdale | Priority: high<br />"
        )
        meta = email_digest._parse_note(note)
        self.assertEqual(meta["summary"], "Plaza did things. SkySong 2.")
        self.assertEqual(meta["priority"], "high")
        self.assertEqual(meta["city"], "Scottsdale")
        self.assertEqual(meta["signal"], "lease")
        # no angle-bracket tags survive in any extracted value
        self.assertNotIn("<", "".join(str(v) for v in meta.values()))


class TestPhoneRendering(unittest.TestCase):
    def test_tel_href_normalizes(self):
        from pipeline import email_digest as e
        self.assertEqual(e._tel_href("(602) 354-3105"), "+16023543105")
        self.assertEqual(e._tel_href("+1 602 282 6269"), "+16022826269")

    def test_linkify_phones_html_links_phone_not_email(self):
        from pipeline import email_digest as e
        html = e._linkify_phones_html("Sally | Pres | sally@x.com | 602.295.1128")
        self.assertIn('href="tel:+16022951128"', html)
        self.assertIn("sally@x.com", html)          # email preserved
        self.assertNotIn('tel:sally', html)         # email not linked

    def test_primary_person_phones_appended_and_labeled(self):
        from pipeline import email_digest as e
        ld = e.DigestLead(
            lead_id="1", title="t", add_time="", org_name=None, city=None,
            signal=None, priority=None, summary=None, value=None, currency=None,
            article_url=None,
            contacts=["Ernie Jackson | Dir | ernie@x.com"],  # no phone in string
            lead_url="u",
            primary_phones=[("work", "623-344-4544"), ("mobile", "602-488-9312")],
        )
        html = e._render_contacts_html(ld)
        self.assertIn('href="tel:+16233444544"', html)   # work
        self.assertIn('href="tel:+16024889312"', html)   # mobile
        self.assertIn("Work:", html)
        self.assertIn("Mobile:", html)

    def test_primary_phone_dedup_against_string(self):
        from pipeline import email_digest as e
        ld = e.DigestLead(
            lead_id="1", title="t", add_time="", org_name=None, city=None,
            signal=None, priority=None, summary=None, value=None, currency=None,
            article_url=None,
            contacts=["Sally | Pres | sally@x.com | (602) 354-3105"],
            lead_url="u",
            primary_phones=[("work", "602-354-3105")],   # same number, diff format
        )
        html = e._render_contacts_html(ld)
        self.assertEqual(html.count('href="tel:+16023543105"'), 1)  # not duplicated

    def test_fetch_person_phones_parses_labels(self):
        from unittest import mock
        from pipeline import email_digest as e
        http = mock.Mock()
        http.get.return_value.json.return_value = {"data": {"phone": [
            {"label": "work", "value": "623-344-4544"},
            {"label": "mobile", "value": "602-488-9312"},
            {"label": "", "value": ""},   # empty skipped
        ]}}
        http.get.return_value.raise_for_status.return_value = None
        self.assertEqual(e._fetch_person_phones(http, 15413),
                         [("work", "623-344-4544"), ("mobile", "602-488-9312")])


if __name__ == "__main__":
    unittest.main()
