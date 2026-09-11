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
        story = engine.scenario_story if active else build_internal_story(scenario)
        branch = (
            (str(engine.clock.get("scenario_branch") or "") or None)
            if active
            else (str(scenario.get("default_branch") or "") or None)
        )
        temporal_story = project_story_at_time(
            story,
            elapsed,
            branch=branch,
        )
        excluded = {
            "threats", "events", "timeline", "media_cues", "asset_routes", "asset_route_modes",
            "asset_motion_windows", "threat_observation_windows", "asset_task_schedule",
            "capture_plans", "asset_capabilities", "asset_profiles",
            "agent_plan", "media_sources", "description", "operator_brief",
            "demo_checkpoints", "fault_injections", "engagement_policy",
        }
        public_scenario = {k: v for k, v in scenario.items() if k not in excluded}
        # This schedule deliberately carries only operational function IDs,
        # stage and simulation time.  It gives the operator a stable 28-point
        # progress plan without exposing future target/media narrative.
        function_schedule = []
        timeline = [cue for cue in scenario.get("timeline") or [] if isinstance(cue, dict)]
        checkpoint_times = sorted(
            float(item.get("min_elapsed_sec", 0) or 0)
            for item in scenario.get("demo_checkpoints") or []
            if isinstance(item, dict)
        )
        runtime_triggers = scenario.get("function_runtime_triggers") or {}
        for cue_index, cue in enumerate(timeline):
            function_ids = list(cue.get("function_ids") or [])
            if not function_ids:
                continue
            cue_at = float(cue.get("at_sec", 0) or 0)
            next_at = float(timeline[cue_index + 1].get("at_sec", cue_at + 600) or cue_at + 600) \
                if cue_index + 1 < len(timeline) else cue_at + 600
            next_checkpoint = next((value for value in checkpoint_times if value > cue_at), None)
            deadline = min(next_at, next_checkpoint) if next_checkpoint is not None else next_at
            # Finish the ordered visual sequence before the next workflow
            # checkpoint.  Otherwise the director pauses for analysis while
            # later cards in the same cue still incorrectly read "pending".
            step_sec = min(90.0, max(1.0, (deadline - cue_at) / max(len(function_ids), 1)))
            for sequence_index, function_id in enumerate(function_ids):
                function_schedule.append({
                    "cue_id": cue.get("cue_id"),
                    "at_sec": cue_at + sequence_index * step_sec,
                    "phase": cue.get("phase"),
                    "function_ids": [function_id],
                    "sequence_index": sequence_index,
                    "runtime_trigger": runtime_triggers.get(function_id),
                })
        public_scenario["function_point_schedule"] = function_schedule
        public_scenario.update(temporal_story)
        public_scenario["visibility"] = "operator-visible"
        return ok(public_scenario)


__all__ = ["register_scenario_routes"]
