from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "relative_path",
    [
        "config/pipedrive_automations.yaml",
        "config/pipedrive_automations.yaml.example",
    ],
)
def test_deal_automation_uses_deal_level_suppression_state(
    relative_path: str,
) -> None:
    spec = yaml.safe_load(ROOT.joinpath(relative_path).read_text())
    workflow = next(
        item
        for item in spec["automations"]
        if item["name"] == "Aether — qualified reply follow-ups"
    )

    assert workflow["trigger"]["entity"] == "deal"
    assert "Aether Outreach State does not equal suppressed" in workflow["guards"]
    assert all("Aether Suppressed" not in guard for guard in workflow["guards"])
