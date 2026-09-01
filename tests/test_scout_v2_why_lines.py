from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scout"))

from v2.contracts import LeadEvent, Organization  # noqa: E402
from v2.why_lines import WhyLineContractError, parse_why_lines  # noqa: E402
from v2.outreach import ROLE_AUTO_SEND_THRESHOLD, score_recipient_role  # noqa: E402


def _event():
    return LeadEvent(
        lead_event_id="event-1",
        run_id="run-1",
        organization_id="org-1",
        primary_candidate_id="candidate-1",
        supporting_candidate_ids=["candidate-1"],
        event="Purchased Esplanade III",
        location="Phoenix, Arizona",
        priority="high",
    )


def test_daily_why_line_uses_the_approved_linkedin_template():
    event = _event()
    organizations = {
        "org-1": Organization(
            organization_id="org-1",
            canonical_name="Southwest Value Partners",
        )
    }
    payload = {
        "event-1": {
            "template_key": "acquisition",
            "slots": {
                "company": "Southwest Value Partners",
                "property": "Esplanade III",
                "location": "Phoenix",
            },
        }
    }
    rendered = parse_why_lines(json.dumps(payload), [event], organizations)["event-1"]
    assert rendered["status"] == "valid"
    assert rendered["why_line"] == (
        "Hi [first name] just wanted to reach out since I saw on the news that "
        "Southwest Value Partners took ownership of Esplanade III in Phoenix. Is "
        "there any chance we could stay in touch regarding your future janitorial needs?"
    )


def test_daily_why_line_fails_closed_on_unknown_company_or_missing_id():
    event = _event()
    organizations = {
        "org-1": Organization(
            organization_id="org-1",
            canonical_name="Southwest Value Partners",
        )
    }
    bad_company = {
        "event-1": {
            "template_key": "acquisition",
            "slots": {
                "company": "Different Buyer",
                "property": "Esplanade III",
                "location": "Phoenix",
            },
        }
    }
    with pytest.raises(WhyLineContractError, match="company"):
        parse_why_lines(json.dumps(bad_company), [event], organizations)
    with pytest.raises(WhyLineContractError, match="IDs"):
        parse_why_lines("{}", [event], organizations)


@pytest.mark.parametrize(
    ("title", "scope", "expected"),
    [
        ("Director of Facilities", "Arizona portfolio", 105),
        ("Regional Operations Manager", "Phoenix", 95),
        ("President", "", 70),
        ("Director of Development", "", 50),
        ("Marketing Manager", "", 0),
    ],
)
def test_recipient_role_policy_is_explicit_and_conservative(title, scope, expected):
    score, _ = score_recipient_role(title, scope)
    assert score == expected
    assert (score >= ROLE_AUTO_SEND_THRESHOLD) == (expected >= 70)
