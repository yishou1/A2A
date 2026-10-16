"""Scenario catalog and public scenario-detail routes."""

from __future__ import annotations

from typing import Any

from amos_platform.api.dependencies import get_engine
from amos_platform.api.responses import err, ok
from amos_platform.data.operational_catalog import operational_functions
from amos_platform.data.scenario_repository import OPERATOR_SCENARIO_IDS, get_scenario, list_scenarios
from amos_platform.frontend_state.temporal_story import build_internal_story, project_story_at_time


_PRIMARY_FUNCTION_POINTS = {
    # The first script is the reference end-to-end convoy and air-defence chain.
    "maritime-convoy-air-defense": {f"KC-{index:02d}" for index in range(1, 29)},
    # The second script puts the emphasis on multi-domain sensing, fusion and
    # coordinated command; strike re-tasking remains a supporting capability.
    "coastal-joint-recon-strike": {
        "KC-01", "KC-04", "KC-05", "KC-06", "KC-07", "KC-08", "KC-09", "KC-10",
        "KC-11", "KC-12", "KC-13", "KC-14", "KC-15", "KC-16", "KC-20", "KC-21",
        "KC-22", "KC-23", "KC-24", "KC-25", "KC-27", "KC-28",
    },
    # The third script puts the emphasis on target-specific allocation,
    # wave-2 re-attack and resource recovery.
    "air-space-sea-carrier-strike": {
        "KC-01", "KC-02", "KC-04", "KC-05", "KC-06", "KC-07", "KC-08", "KC-09", "KC-10",
        "KC-11", "KC-12", "KC-13", "KC-14", "KC-15", "KC-16", "KC-17", "KC-18", "KC-19",
        "KC-20", "KC-21", "KC-22", "KC-23", "KC-24", "KC-25", "KC-26", "KC-27", "KC-28",
    },
}


def register_scenario_routes(bp: Any) -> None:
    """Register operator-visible scenario catalog routes."""

    @bp.route("/api/v1/scenarios", methods=["GET"])
    def scenarios_list():
        return ok({"scenarios": list_scenarios()})

    @bp.route("/api/v1/scenarios/coverage", methods=["GET"])
    def scenarios_coverage():
        """Return design-time coverage for the operator comparison view.

        This is intentionally independent of a submitted run.  Runtime
        verification is added by the workflow-view projection in the UI.
        """
        catalog = {item["function_id"]: item for item in operational_functions()}
        scenarios = []
        by_function = {function_id: [] for function_id in catalog}
        for scenario_id in OPERATOR_SCENARIO_IDS:
            scenario = get_scenario(scenario_id)
            if not scenario:
                continue
            conditional = {str(item) for item in scenario.get("conditional_function_points") or []}
            timeline_rows = [item for item in scenario.get("timeline") or [] if isinstance(item, dict)]
            timeline_primary_ids = {
                str(function_id)
                for row in timeline_rows
                for function_id in row.get("function_ids") or []
                if str(function_id) in catalog
            }
            primary_ids = _PRIMARY_FUNCTION_POINTS.get(scenario_id, timeline_primary_ids)
            checkpoint_by_function = {}
            checkpoints = [item for item in scenario.get("demo_checkpoints") or [] if isinstance(item, dict)]
            for row in timeline_rows:
                next_checkpoint = next(
                    (checkpoint for checkpoint in checkpoints
                     if float(checkpoint.get("min_elapsed_sec", 0) or 0) >= float(row.get("at_sec", 0) or 0)),
                    None,
                )
                for function_id in row.get("function_ids") or []:
                    checkpoint_by_function.setdefault(str(function_id), []).append(
                        (next_checkpoint or {}).get("checkpoint_id")
                    )
            declared = {
                str(item.get("function_id") or item.get("function_point_id"))
                for item in scenario.get("function_point_coverage") or []
                if item.get("function_id") or item.get("function_point_id")
            }
            rows = []
            for function_id in catalog:
                if function_id not in declared:
                    kind = "not_covered"
                elif function_id in conditional:
                    kind = "conditional"
                elif function_id in primary_ids:
                    kind = "primary"
                else:
                    kind = "supporting"
                row = {
                    "function_point_id": function_id,
                    "name": catalog[function_id]["name"],
                    "chinese_name": catalog[function_id]["chinese_name"],
                    "english_name": catalog[function_id]["english_name"],
                    "ooda_phase": catalog[function_id]["ooda_phase"],
                    "f2t2ea_stage": catalog[function_id]["f2t2ea_stage"],
                    "coverage_kind": kind,
                    "planned": kind != "not_covered",
                    "conditional": kind == "conditional",
                    "checkpoints": list(dict.fromkeys(filter(None, checkpoint_by_function.get(function_id, [])))),
                }
                rows.append(row)
                by_function[function_id].append({
                    "scenario_id": scenario_id,
                    "coverage_kind": kind,
                    "checkpoints": row["checkpoints"],
                })
            scenarios.append({
                "id": scenario_id,
                "name": scenario.get("name", scenario_id),
                "description": scenario.get("operator_brief", ""),
                "workflow_chain_id": f"{scenario_id}.commander-workflow",
                "workflow_chain_label": f"{scenario.get('name', scenario_id)}专用 A1-A6 指挥工作流链",
                "function_points": rows,
            })
        items = []
        for function_id, definition in catalog.items():
            coverage = by_function[function_id]
            items.append({
                "function_point_id": function_id,
                "name": definition["name"],
                "chinese_name": definition["chinese_name"],
                "english_name": definition["english_name"],
                "ooda_phase": definition["ooda_phase"],
                "f2t2ea_stage": definition["f2t2ea_stage"],
                "scenarios": coverage,
                "covered_scenario_count": sum(item["coverage_kind"] != "not_covered" for item in coverage),
            })
        return ok({
            "schema_version": "amos.scenario-coverage.v1",
            "total_function_points": len(items),
            "scenarios": scenarios,
            "items": items,
        })

    @bp.route("/api/v1/scenarios/<scenario_id>", methods=["GET"])
    def scenario_detail(scenario_id: str):
        scenario = get_scenario(scenario_id)
        if not scenario:
            return err(404, f"scenario not found: {scenario_id}"), 404
        engine = get_engine()
        active = engine.clock.get("scenario_id") == scenario_id
        elapsed = float(engine.clock.get("scenario_elapsed_sec", engine.clock.get("elapsed_sec", 0)) or 0) if active else -1.0
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
            capture_elapsed_sec=(float(engine.clock.get("elapsed_sec", 0) or 0) if active else -1.0),
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
