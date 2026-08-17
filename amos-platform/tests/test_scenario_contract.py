from __future__ import annotations

import pytest

from amos_platform.data.scenario_contract import ScenarioContractError, validate_scenario_or_raise


def test_scenario_contract_rejects_duplicate_asset_ids() -> None:
    with pytest.raises(ScenarioContractError, match="asset identifiers must be unique"):
        validate_scenario_or_raise({
            "id": "duplicate-assets",
            "name": "duplicate assets",
            "assets": [{"id": "A-1"}, {"asset_id": "A-1"}],
        })


def test_scripted_scenario_contract_rejects_future_definition_errors() -> None:
    with pytest.raises(ScenarioContractError) as raised:
        validate_scenario_or_raise({
            "id": "broken-script",
            "name": "broken script",
            "scenario_type": "scripted_agent_demo",
            "operator_brief": "current facts only",
            "theater": {"center": {}, "ao": {}},
            "assets": [{"id": "A-1"}],
            "timeline": [
                {"cue_id": "C-2", "at_sec": 20, "media_ids": ["missing"]},
                {"cue_id": "C-1", "at_sec": 10},
            ],
            "media_cues": [{"media_id": "M-1", "at_sec": 0}],
            "asset_routes": {"UNKNOWN-ASSET": []},
        })

    message = str(raised.value)
    assert "timeline must be ordered" in message
    assert "references unknown media" in message
    assert "asset_routes references unknown assets" in message
    assert "requires checksum" in message
