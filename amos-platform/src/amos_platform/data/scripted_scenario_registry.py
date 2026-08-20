"""Registration point for data-driven AMOS demonstration scenarios.

Keep scenario construction out of the repository/catalog module.  A new
scripted demo should provide one builder and be registered in this tuple; the
repository then applies the common normalization and validation contract.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from amos_platform.data.amphibious_landing_builder import build_amphibious_landing_scenario
from amos_platform.data.border_uav_evacuation_builder import build_border_uav_evacuation_scenario
from amos_platform.data.maritime_convoy_air_defense_builder import (
    build_maritime_convoy_air_defense_scenario,
)


ScenarioBuilder = Callable[[], dict[str, Any]]

SCRIPTED_SCENARIO_BUILDERS: tuple[ScenarioBuilder, ...] = (
    build_amphibious_landing_scenario,
    build_border_uav_evacuation_scenario,
    build_maritime_convoy_air_defense_scenario,
)


def build_registered_scripted_scenarios() -> dict[str, dict[str, Any]]:
    """Build registered scenarios and reject duplicate or missing IDs."""
    scenarios: dict[str, dict[str, Any]] = {}
    for builder in SCRIPTED_SCENARIO_BUILDERS:
        scenario = builder()
        scenario_id = str(scenario.get("id") or "")
        if not scenario_id:
            raise ValueError(f"scenario builder {builder.__name__} returned no id")
        if scenario_id in scenarios:
            raise ValueError(f"duplicate scripted scenario id: {scenario_id}")
        scenarios[scenario_id] = scenario
    return scenarios


__all__ = ["SCRIPTED_SCENARIO_BUILDERS", "build_registered_scripted_scenarios"]
