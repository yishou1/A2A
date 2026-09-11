"""Validated catalog for AMOS demonstration scenarios."""

from __future__ import annotations

from typing import Any

from amos_platform.data.scenario_contract import validate_scenario_or_raise
from amos_platform.data.scripted_scenario_registry import build_registered_scripted_scenarios
from amos_platform.domain.models import AssetSnapshot, ThreatSnapshot
from amos_platform.domain.models.asset import WeaponSpec


OPERATOR_SCENARIO_IDS = (
    "maritime-convoy-air-defense",
    "coastal-joint-recon-strike",
    "air-space-sea-carrier-strike",
)
WEAPON_CATALOG = {
    "舰载对陆巡航导弹": WeaponSpec(
        weapon_id="SIM-LACM-01",
        weapon_type="舰载对陆巡航导弹",
        category="anti-ground",
        range_nm=120,
        speed_kts=600,
        p_kill=0.76,
        warhead="simulation",
        guidance="terrain_contour_satellite",
    ),
    "舰载反舰导弹": WeaponSpec(
        weapon_id="SIM-ASHM-01",
        weapon_type="舰载反舰导弹",
        category="anti-surface",
        range_nm=80,
        speed_kts=620,
        p_kill=0.78,
        warhead="simulation",
        guidance="active_radar",
    ),
    "空地防区外弹药": WeaponSpec(
        weapon_id="SIM-AGM-01",
        weapon_type="空地防区外弹药",
        category="anti-ground",
        range_nm=60,
        speed_kts=540,
        p_kill=0.82,
        warhead="simulation",
        guidance="multi_mode",
    ),
    "无人机协同攻击弹药": WeaponSpec(
        weapon_id="SIM-SWARM-01",
        weapon_type="无人机协同攻击弹药",
        category="anti-ground",
        range_nm=30,
        speed_kts=240,
        p_kill=0.68,
        warhead="simulation",
        guidance="cooperative_network",
    ),
    "舰载无人机空地导弹": WeaponSpec(
        weapon_id="SIM-CARRIER-AGM-01",
        weapon_type="舰载无人机空地导弹",
        category="anti-ground",
        range_nm=70,
        speed_kts=520,
        p_kill=0.82,
        warhead="simulation",
        guidance="multi_mode_standoff",
    ),
    "巡飞攻击载荷": WeaponSpec(
        weapon_id="SIM-LOITER-01",
        weapon_type="巡飞攻击载荷",
        category="anti-ground",
        range_nm=45,
        speed_kts=150,
        p_kill=0.72,
        warhead="simulation",
        guidance="operator_authorized_eo_terminal",
    ),
}


def _normalize(scenario: dict[str, Any]) -> dict[str, Any]:
    """Convert builder dataclasses to the JSON-ready scenario contract."""
    result = dict(scenario)
    result["assets"] = [
        item.to_dict() if isinstance(item, AssetSnapshot) else dict(item)
        for item in scenario.get("assets") or []
    ]
    result["threats"] = [
        item.to_dict() if isinstance(item, ThreatSnapshot) else dict(item)
        for item in scenario.get("threats") or []
    ]
    return validate_scenario_or_raise(result)


def _catalog() -> dict[str, dict[str, Any]]:
    return {
        scenario_id: _normalize(scenario)
        for scenario_id, scenario in build_registered_scripted_scenarios().items()
    }


def list_scenarios() -> list[dict[str, Any]]:
    """Return operator-safe summaries for configured scenarios."""
    return [
        {
            "schema_version": scenario.get("schema_version", "amos.scenario.v2"),
            "id": scenario["id"],
            "name": scenario["name"],
            "description": scenario.get("operator_brief", ""),
            "asset_count": len(scenario.get("assets") or []),
            "scenario_type": scenario.get("scenario_type", "scripted_agent_demo"),
            "default_seed": scenario.get("default_seed"),
            "supported_modes": list(scenario.get("supported_modes") or []),
            "recommended_speed": (scenario.get("demo_controls") or {}).get("recommended_speed", 1),
            "duration_sec": (scenario.get("demo_controls") or {}).get("duration_sec", 0),
            "required_agent_count": sum(
                1 for item in scenario.get("required_agents") or [] if item.get("required")
            ),
            "functional_agent_count": len(scenario.get("functional_agents") or []),
            "algorithm_count": len(scenario.get("algorithm_coverage") or []),
            "core_algorithm_count": sum(
                1 for item in scenario.get("algorithm_coverage") or [] if item.get("tier") == "core"
            ),
            "engineering_model_count": sum(
                1 for item in scenario.get("algorithm_coverage") or [] if item.get("tier") == "engineering"
            ),
            "function_point_count": len(scenario.get("function_point_coverage") or []),
            "expected_branches": [
                {key: item.get(key) for key in ("branch_id", "name", "description")}
                for item in scenario.get("expected_branches") or []
            ],
        }
        for scenario_id, scenario in _catalog().items()
        if scenario_id in OPERATOR_SCENARIO_IDS
    ]


def get_scenario(scenario_id: str) -> dict[str, Any] | None:
    """Return a fresh normalized scenario definition by ID."""
    return _catalog().get(scenario_id)


def get_weapon_spec(weapon_name: str) -> WeaponSpec | None:
    """Return a weapon specification by configured name or stable ID."""
    direct = WEAPON_CATALOG.get(str(weapon_name))
    if direct is not None:
        return direct
    normalized = str(weapon_name).strip().casefold()
    return next(
        (
            spec for spec in WEAPON_CATALOG.values()
            if normalized in {spec.weapon_id.casefold(), spec.weapon_type.casefold()}
        ),
        None,
    )


__all__ = ["get_scenario", "get_weapon_spec", "list_scenarios"]
