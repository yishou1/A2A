"""Scenario catalog and public scenario-detail routes."""

from __future__ import annotations

from typing import Any

from amos_platform.api.dependencies import get_engine
from amos_platform.api.responses import err, ok
from amos_platform.data.scenario_repository import get_scenario, list_scenarios
from amos_platform.frontend_state.temporal_story import build_internal_story, project_story_at_time


def register_scenario_routes(bp: Any) -> None:
    """Register operator-visible scenario catalog routes."""

    @bp.route("/api/v1/scenarios", methods=["GET"])
    def scenarios_list():
        return ok({"scenarios": list_scenarios()})

    @bp.route("/api/v1/scenarios/<scenario_id>", methods=["GET"])
    def scenario_detail(scenario_id: str):
        scenario = get_scenario(scenario_id)
        if not scenario:
            return err(404, f"scenario not found: {scenario_id}"), 404
        engine = get_engine()
        active = engine.clock.get("scenario_id") == scenario_id
        elapsed = float(engine.clock.get("elapsed_sec", 0) or 0) if active else -1.0
        temporal_story = project_story_at_time(build_internal_story(scenario), elapsed)
        excluded = {
            "threats", "events", "timeline", "media_cues", "asset_routes", "asset_route_modes",
            "asset_motion_windows", "threat_observation_windows", "asset_task_schedule",
            "capture_plans", "asset_capabilities", "asset_profiles",
            "agent_plan", "media_sources", "description", "operator_brief",
            "demo_checkpoints", "fault_injections", "engagement_policy",
        }
        public_scenario = {k: v for k, v in scenario.items() if k not in excluded}
        public_scenario.update(temporal_story)
        public_scenario["visibility"] = "operator-visible"
        return ok(public_scenario)


__all__ = ["register_scenario_routes"]
