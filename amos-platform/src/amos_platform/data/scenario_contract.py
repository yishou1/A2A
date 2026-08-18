"""Validation contract for data-driven AMOS scenarios.

Scenario builders may contain complete future truth, but every definition must
be internally consistent so the causal projection layer can safely release it
over time and the Commander mapper can build stable current-time inputs.
"""

from __future__ import annotations

from typing import Any

from amos_platform.data.scenario_builder_support import (
    CAPABILITY_SOURCE_KINDS,
    MEDIA_PRODUCT_TYPES,
)
from amos_platform.data.scenario_capabilities import (
    FUNCTIONAL_AGENT_CATALOG,
    FUNCTION_POINT_CATALOG,
    MODEL_CATALOG,
)


class ScenarioContractError(ValueError):
    """Raised when a scenario cannot be safely loaded."""


def _identifier(item: dict[str, Any], *keys: str) -> str:
    for key in keys:
        if item.get(key):
            return str(item[key])
    return ""


def validate_scenario_definition(scenario: dict[str, Any]) -> list[str]:
    """Return contract violations without mutating the scenario."""
    issues: list[str] = []
    scenario_id = str(scenario.get("id") or "")
    if not scenario_id:
        issues.append("id is required")
    if not scenario.get("name"):
        issues.append(f"{scenario_id or '<scenario>'}: name is required")

    assets = [item for item in scenario.get("assets") or [] if isinstance(item, dict)]
    asset_ids = [_identifier(item, "asset_id", "id") for item in assets]
    if any(not value for value in asset_ids):
        issues.append(f"{scenario_id}: every asset requires asset_id/id")
    if len(set(asset_ids)) != len(asset_ids):
        issues.append(f"{scenario_id}: asset identifiers must be unique")

    if scenario.get("scenario_type") != "scripted_agent_demo":
        return issues

    if scenario.get("schema_version") != "amos.scenario.v2":
        issues.append(f"{scenario_id}: scripted scenario requires schema_version amos.scenario.v2")

    theater = scenario.get("theater") if isinstance(scenario.get("theater"), dict) else {}
    if not isinstance(theater.get("center"), dict) or not isinstance(theater.get("ao"), dict):
        issues.append(f"{scenario_id}: scripted scenario requires theater.center and theater.ao")
    if not scenario.get("operator_brief"):
        issues.append(f"{scenario_id}: scripted scenario requires a truth-free operator_brief")

    profiles = [
        item for item in scenario.get("asset_profiles") or [] if isinstance(item, dict)
    ]
    profile_ids = [_identifier(item, "platform_id", "asset_id", "id") for item in profiles]
    if set(profile_ids) != set(asset_ids) or len(profile_ids) != len(asset_ids):
        issues.append(f"{scenario_id}: asset_profiles must cover every asset exactly once")
    for profile in profiles:
        platform_id = _identifier(profile, "platform_id", "asset_id", "id") or "<profile>"
        motion = profile.get("motion") if isinstance(profile.get("motion"), dict) else {}
        communications = profile.get("communications")
        state_model = (
            profile.get("operational_state_model")
            if isinstance(profile.get("operational_state_model"), dict)
            else {}
        )
        try:
            cruise = float(motion["cruise_speed_kts"])
            maximum = float(motion["max_speed_kts"])
            turn_rate = float(motion["max_turn_rate_dps"])
            if cruise < 0 or maximum < cruise or turn_rate < 0 or not motion.get("movement_constraint"):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            issues.append(f"{scenario_id}: asset profile {platform_id} has invalid motion limits")
        if not isinstance(communications, list) or not communications:
            issues.append(f"{scenario_id}: asset profile {platform_id} requires communications")
        else:
            for link in communications:
                try:
                    if (
                        not isinstance(link, dict)
                        or not link.get("band")
                        or float(link["range_km"]) <= 0
                        or float(link["bandwidth_mbps"]) <= 0
                    ):
                        raise ValueError
                except (KeyError, TypeError, ValueError):
                    issues.append(f"{scenario_id}: asset profile {platform_id} has invalid communications")
                    break
        if not profile.get("supported_tasks"):
            issues.append(f"{scenario_id}: asset profile {platform_id} requires supported_tasks")
        states = {str(value) for value in state_model.get("states") or []}
        if not state_model.get("initial_state") or "active" not in states or "unavailable" not in states:
            issues.append(f"{scenario_id}: asset profile {platform_id} has invalid state model")

    try:
        raw_seed = scenario.get("default_seed")
        if isinstance(raw_seed, bool) or (
            isinstance(raw_seed, float) and not raw_seed.is_integer()
        ):
            raise ValueError
        default_seed = int(raw_seed)
        if default_seed < 0 or default_seed > 2**32 - 1:
            raise ValueError
    except (TypeError, ValueError):
        issues.append(f"{scenario_id}: default_seed must be an integer from 0 to 4294967295")

    supported_modes = [str(value) for value in scenario.get("supported_modes") or []]
    if not supported_modes or any(value not in {"integration", "demonstration"} for value in supported_modes):
        issues.append(f"{scenario_id}: supported_modes must contain integration and/or demonstration")

    timeline = [item for item in scenario.get("timeline") or [] if isinstance(item, dict)]
    media = [item for item in scenario.get("media_cues") or [] if isinstance(item, dict)]
    if not timeline:
        issues.append(f"{scenario_id}: scripted scenario requires timeline cues")
    if not media:
        issues.append(f"{scenario_id}: scripted scenario requires media_cues")

    cue_ids = [_identifier(item, "cue_id") for item in timeline]
    media_ids = [_identifier(item, "media_id") for item in media]
    if len(set(cue_ids)) != len(cue_ids) or any(not value for value in cue_ids):
        issues.append(f"{scenario_id}: cue_id values must be present and unique")
    if len(set(media_ids)) != len(media_ids) or any(not value for value in media_ids):
        issues.append(f"{scenario_id}: media_id values must be present and unique")

    timeline_times = [float(item.get("at_sec", 0) or 0) for item in timeline]
    media_times = [float(item.get("at_sec", 0) or 0) for item in media]
    controls = scenario.get("demo_controls") if isinstance(scenario.get("demo_controls"), dict) else {}
    try:
        duration_sec = float(controls.get("duration_sec", 0) or 0)
        if duration_sec <= 0:
            raise ValueError
    except (TypeError, ValueError):
        duration_sec = 0.0
        issues.append(f"{scenario_id}: demo_controls.duration_sec must be positive")
    if timeline_times != sorted(timeline_times):
        issues.append(f"{scenario_id}: timeline must be ordered by at_sec")
    if media_times != sorted(media_times):
        issues.append(f"{scenario_id}: media_cues must be ordered by at_sec")
    if any(value < 0 or (duration_sec and value > duration_sec) for value in timeline_times):
        issues.append(f"{scenario_id}: timeline at_sec must fit demo duration")
    if any(value < 0 or (duration_sec and value > duration_sec) for value in media_times):
        issues.append(f"{scenario_id}: media at_sec must fit demo duration")

    known_media = set(media_ids)
    for cue in timeline:
        unknown = set(str(value) for value in cue.get("media_ids") or []) - known_media
        if unknown:
            issues.append(f"{scenario_id}: cue {cue.get('cue_id')} references unknown media {sorted(unknown)}")
    for item in media:
        label = item.get("media_id") or "<media>"
        for key in (
            "uri", "checksum", "sensor_id", "modality", "mime_type",
            "capture_id", "product_type", "branch_ids",
        ):
            if not item.get(key):
                issues.append(f"{scenario_id}: media {label} requires {key}")
        if item.get("product_type") not in MEDIA_PRODUCT_TYPES:
            issues.append(f"{scenario_id}: media {label} has invalid product_type")

    routes = scenario.get("asset_routes") if isinstance(scenario.get("asset_routes"), dict) else {}
    unknown_route_assets = set(str(value) for value in routes) - set(asset_ids)
    if unknown_route_assets:
        issues.append(f"{scenario_id}: asset_routes references unknown assets {sorted(unknown_route_assets)}")
    route_modes = scenario.get("asset_route_modes") if isinstance(scenario.get("asset_route_modes"), dict) else {}
    unknown_mode_assets = set(str(value) for value in route_modes) - set(routes)
    if unknown_mode_assets:
        issues.append(
            f"{scenario_id}: asset_route_modes references assets without routes {sorted(unknown_mode_assets)}"
        )
    invalid_modes = {
        str(asset_id): str(mode)
        for asset_id, mode in route_modes.items()
        if mode not in {"hold", "loop"}
    }
    if invalid_modes:
        issues.append(f"{scenario_id}: asset_route_modes must use hold or loop {invalid_modes}")
    motion_windows = scenario.get("asset_motion_windows") if isinstance(scenario.get("asset_motion_windows"), dict) else {}
    unknown_window_assets = set(str(value) for value in motion_windows) - set(routes)
    if unknown_window_assets:
        issues.append(
            f"{scenario_id}: asset_motion_windows references assets without routes {sorted(unknown_window_assets)}"
        )
    for asset_id, window in motion_windows.items():
        if not isinstance(window, dict):
            issues.append(f"{scenario_id}: motion window for {asset_id} must be an object")
            continue
        try:
            start_sec = float(window.get("start_sec", 0) or 0)
            end_sec = None if window.get("end_sec") is None else float(window["end_sec"])
            if (
                start_sec < 0
                or (duration_sec and start_sec >= duration_sec)
                or (end_sec is not None and end_sec <= start_sec)
                or (end_sec is not None and duration_sec and end_sec > duration_sec)
            ):
                raise ValueError
        except (TypeError, ValueError):
            issues.append(f"{scenario_id}: invalid motion window for {asset_id}")

    threat_ids = {
        _identifier(item, "threat_id", "id")
        for item in scenario.get("threats") or [] if isinstance(item, dict)
    }
    observation_windows = (
        scenario.get("threat_observation_windows")
        if isinstance(scenario.get("threat_observation_windows"), dict) else {}
    )
    unknown_observation_targets = set(str(value) for value in observation_windows) - threat_ids
    if unknown_observation_targets:
        issues.append(
            f"{scenario_id}: threat_observation_windows references unknown threats "
            f"{sorted(unknown_observation_targets)}"
        )
    for threat_id, window in observation_windows.items():
        if not isinstance(window, dict):
            issues.append(f"{scenario_id}: observation window for {threat_id} must be an object")
            continue
        try:
            start_sec = float(window.get("start_sec", 0) or 0)
            end_sec = None if window.get("end_sec") is None else float(window["end_sec"])
            if (
                start_sec < 0
                or (duration_sec and start_sec > duration_sec)
                or (end_sec is not None and end_sec <= start_sec)
                or (end_sec is not None and duration_sec and end_sec > duration_sec)
            ):
                raise ValueError
        except (TypeError, ValueError):
            issues.append(f"{scenario_id}: invalid observation window for {threat_id}")

    required_agents = [item for item in scenario.get("required_agents") or [] if isinstance(item, dict)]
    if not required_agents:
        issues.append(f"{scenario_id}: required_agents must not be empty")
    agent_roles = [_identifier(item, "role") for item in required_agents]
    if any(not value for value in agent_roles) or len(set(agent_roles)) != len(agent_roles):
        issues.append(f"{scenario_id}: required agent roles must be present and unique")

    functional_agent_rows = [
        item for item in scenario.get("functional_agents") or [] if isinstance(item, dict)
    ]
    functional_agent_ids = [_identifier(item, "agent_id") for item in functional_agent_rows]
    expected_functional_agents = [str(item["agent_id"]) for item in FUNCTIONAL_AGENT_CATALOG]
    if functional_agent_ids != expected_functional_agents:
        issues.append(f"{scenario_id}: functional_agents must contain A1-A6 in catalog order")
    if any(item.get("role") == "commander" for item in functional_agent_rows):
        issues.append(f"{scenario_id}: Commander is an orchestrator, not a functional Agent")

    physical_devices = [item for item in scenario.get("physical_devices") or [] if isinstance(item, dict)]
    compute_nodes = [item for item in scenario.get("compute_nodes") or [] if isinstance(item, dict)]
    agent_deployments = [item for item in scenario.get("agent_deployments") or [] if isinstance(item, dict)]
    device_ids = [_identifier(item, "device_id") for item in physical_devices]
    compute_node_ids = [_identifier(item, "node_id") for item in compute_nodes]
    deployment_ids = [_identifier(item, "deployment_id") for item in agent_deployments]
    if physical_devices or compute_nodes or agent_deployments:
        if any(not value for value in device_ids) or len(set(device_ids)) != len(device_ids):
            issues.append(f"{scenario_id}: physical device identifiers must be present and unique")
        if any(not value for value in compute_node_ids) or len(set(compute_node_ids)) != len(compute_node_ids):
            issues.append(f"{scenario_id}: compute node identifiers must be present and unique")
        if any(not value for value in deployment_ids) or len(set(deployment_ids)) != len(deployment_ids):
            issues.append(f"{scenario_id}: agent deployment identifiers must be present and unique")
        unknown_compute_hosts = {
            str(item.get("host_device_id") or "")
            for item in compute_nodes
        } - set(device_ids)
        if unknown_compute_hosts:
            issues.append(f"{scenario_id}: compute_nodes reference unknown devices {sorted(unknown_compute_hosts)}")
        unknown_deployment_agents = {
            str(item.get("agent_id") or "")
            for item in agent_deployments
        } - set(functional_agent_ids)
        if unknown_deployment_agents:
            issues.append(f"{scenario_id}: agent_deployments reference unknown agents {sorted(unknown_deployment_agents)}")
        unknown_deployment_nodes = {
            str(item.get("compute_node_id") or "")
            for item in agent_deployments
        } - set(compute_node_ids)
        if unknown_deployment_nodes:
            issues.append(f"{scenario_id}: agent_deployments reference unknown compute nodes {sorted(unknown_deployment_nodes)}")

    algorithm_coverage = [item for item in scenario.get("algorithm_coverage") or [] if isinstance(item, dict)]
    algorithm_ids = [_identifier(item, "algorithm_id") for item in algorithm_coverage]
    catalog_algorithm_ids = {str(item["algorithm_id"]) for item in MODEL_CATALOG}
    if not algorithm_ids:
        issues.append(f"{scenario_id}: algorithm_coverage must not be empty")
    if any(not value for value in algorithm_ids) or len(set(algorithm_ids)) != len(algorithm_ids):
        issues.append(f"{scenario_id}: algorithm_id values must be present and unique")
    unknown_algorithms = set(algorithm_ids) - catalog_algorithm_ids
    if unknown_algorithms:
        issues.append(f"{scenario_id}: unknown algorithm declarations {sorted(unknown_algorithms)}")
    requirement_ids = [_identifier(item, "requirement_id") for item in algorithm_coverage]
    if any(not value for value in requirement_ids) or len(set(requirement_ids)) != len(requirement_ids):
        issues.append(f"{scenario_id}: model requirement_id values must be present and unique")
    known_agent_ids = set(expected_functional_agents)
    for model in algorithm_coverage:
        assigned = {str(value) for value in model.get("assigned_agents") or []}
        if not assigned or assigned - known_agent_ids:
            issues.append(
                f"{scenario_id}: model {model.get('requirement_id')} requires valid assigned_agents"
            )
        model_function_ids = {str(value) for value in model.get("function_points") or []}
        known_function_ids = {str(item["function_id"]) for item in FUNCTION_POINT_CATALOG}
        if not model_function_ids or model_function_ids - known_function_ids:
            issues.append(
                f"{scenario_id}: model {model.get('requirement_id')} requires valid function_points"
            )

    function_coverage = [item for item in scenario.get("function_point_coverage") or [] if isinstance(item, dict)]
    function_ids = [_identifier(item, "function_id") for item in function_coverage]
    catalog_function_ids = {str(item["function_id"]) for item in FUNCTION_POINT_CATALOG}
    if not function_ids:
        issues.append(f"{scenario_id}: function_point_coverage must not be empty")
    if any(not value for value in function_ids) or len(set(function_ids)) != len(function_ids):
        issues.append(f"{scenario_id}: function_id values must be present and unique")
    unknown_functions = set(function_ids) - catalog_function_ids
    if unknown_functions:
        issues.append(f"{scenario_id}: unknown function-point declarations {sorted(unknown_functions)}")

    checkpoints = [item for item in scenario.get("demo_checkpoints") or [] if isinstance(item, dict)]
    checkpoint_ids = [_identifier(item, "checkpoint_id") for item in checkpoints]
    checkpoint_times = [float(item.get("min_elapsed_sec", 0) or 0) for item in checkpoints]
    if not checkpoints:
        issues.append(f"{scenario_id}: demo_checkpoints must not be empty")
    if any(not value for value in checkpoint_ids) or len(set(checkpoint_ids)) != len(checkpoint_ids):
        issues.append(f"{scenario_id}: checkpoint_id values must be present and unique")
    if checkpoint_times != sorted(checkpoint_times):
        issues.append(f"{scenario_id}: demo_checkpoints must be ordered by min_elapsed_sec")
    if any(value < 0 or (duration_sec and value > duration_sec) for value in checkpoint_times):
        issues.append(f"{scenario_id}: checkpoint times must fit demo duration")
    for checkpoint in checkpoints:
        conditions = checkpoint.get("conditions") if isinstance(checkpoint.get("conditions"), dict) else {}
        unknown = set(str(value) for value in conditions.get("media_ids_released") or []) - known_media
        if unknown:
            issues.append(
                f"{scenario_id}: checkpoint {checkpoint.get('checkpoint_id')} references unknown media {sorted(unknown)}"
            )

    branches = [item for item in scenario.get("expected_branches") or [] if isinstance(item, dict)]
    branch_ids = [_identifier(item, "branch_id") for item in branches]
    if not branches:
        issues.append(f"{scenario_id}: expected_branches must not be empty")
    if any(not value for value in branch_ids) or len(set(branch_ids)) != len(branch_ids):
        issues.append(f"{scenario_id}: branch_id values must be present and unique")
    if str(scenario.get("default_branch") or "") not in set(branch_ids):
        issues.append(f"{scenario_id}: default_branch must reference expected_branches")

    known_branches = set(branch_ids)
    valid_branch_refs = known_branches | {"*"}
    for collection_name, collection in (
        ("timeline", timeline),
        ("media_cues", media),
        ("demo_checkpoints", checkpoints),
    ):
        for item in collection:
            refs = {str(value) for value in item.get("branch_ids") or ["*"]}
            if not refs or refs - valid_branch_refs or ("*" in refs and len(refs) > 1):
                issues.append(
                    f"{scenario_id}: {collection_name} item "
                    f"{_identifier(item, 'cue_id', 'media_id', 'checkpoint_id')} has invalid branch_ids"
                )

    asset_sensor_map = {
        _identifier(asset, "asset_id", "id"): {str(value) for value in asset.get("sensors") or []}
        for asset in assets
    }
    capabilities = [
        item for item in scenario.get("asset_capabilities") or [] if isinstance(item, dict)
    ]
    capability_ids = [_identifier(item, "capability_id") for item in capabilities]
    if not capabilities:
        issues.append(f"{scenario_id}: asset_capabilities must not be empty")
    if any(not value for value in capability_ids) or len(set(capability_ids)) != len(capability_ids):
        issues.append(f"{scenario_id}: capability_id values must be present and unique")
    capabilities_by_id = {
        str(item.get("capability_id")): item for item in capabilities if item.get("capability_id")
    }
    for capability in capabilities:
        capability_id = capability.get("capability_id") or "<capability>"
        platform_id = str(capability.get("platform_id") or "")
        source_kind = str(capability.get("source_kind") or "")
        if source_kind not in CAPABILITY_SOURCE_KINDS:
            issues.append(f"{scenario_id}: capability {capability_id} has invalid source_kind")
        if not platform_id or not capability.get("sensor_instance_id") or not capability.get("sensor_type"):
            issues.append(f"{scenario_id}: capability {capability_id} requires platform/sensor/type")
        if not capability.get("modalities") or not isinstance(capability.get("parameters"), dict):
            issues.append(f"{scenario_id}: capability {capability_id} requires modalities and parameters")
        if source_kind == "asset":
            if platform_id not in set(asset_ids):
                issues.append(f"{scenario_id}: capability {capability_id} references unknown asset {platform_id}")
            configured_sensor = str(capability.get("configured_sensor") or "")
            if not configured_sensor or configured_sensor not in asset_sensor_map.get(platform_id, set()):
                issues.append(
                    f"{scenario_id}: capability {capability_id} configured_sensor is not declared by {platform_id}"
                )
            source_sensors = {
                str(value)
                for value in (capability.get("parameters") or {}).get("source_sensors") or []
                if value
            }
            if source_sensors - asset_sensor_map.get(platform_id, set()):
                issues.append(
                    f"{scenario_id}: capability {capability_id} source_sensors are not declared by {platform_id}"
                )

    tasks = [
        item for item in scenario.get("asset_task_schedule") or [] if isinstance(item, dict)
    ]
    task_ids = [_identifier(item, "task_id") for item in tasks]
    if not tasks:
        issues.append(f"{scenario_id}: asset_task_schedule must not be empty")
    if any(not value for value in task_ids) or len(set(task_ids)) != len(task_ids):
        issues.append(f"{scenario_id}: task_id values must be present and unique")
    tasks_by_id = {str(item.get("task_id")): item for item in tasks if item.get("task_id")}
    for task in tasks:
        task_id = task.get("task_id") or "<task>"
        capability = capabilities_by_id.get(str(task.get("capability_id") or ""))
        if capability is None:
            issues.append(f"{scenario_id}: task {task_id} references unknown capability")
        elif str(task.get("platform_id") or "") != str(capability.get("platform_id") or ""):
            issues.append(f"{scenario_id}: task {task_id} platform does not match capability")
        if task.get("task_type") not in {"capture", "derive", "ingest", "command_product"}:
            issues.append(f"{scenario_id}: task {task_id} has invalid task_type")
        window = task.get("window") if isinstance(task.get("window"), dict) else {}
        try:
            start = float(window["start_sec"])
            end = float(window["end_sec"])
            if start < 0 or end <= start or (duration_sec and end > duration_sec):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            issues.append(f"{scenario_id}: task {task_id} has invalid window")
        target_refs = {str(value) for value in task.get("target_refs") or []}
        if target_refs - threat_ids:
            issues.append(f"{scenario_id}: task {task_id} references unknown targets {sorted(target_refs - threat_ids)}")
        task_branches = {str(value) for value in task.get("branch_ids") or []}
        if not task_branches or task_branches - valid_branch_refs or ("*" in task_branches and len(task_branches) > 1):
            issues.append(f"{scenario_id}: task {task_id} has invalid branch_ids")

    captures = [item for item in scenario.get("capture_plans") or [] if isinstance(item, dict)]
    capture_ids = [_identifier(item, "capture_id") for item in captures]
    capture_media_ids = [_identifier(item, "media_id") for item in captures]
    if not captures:
        issues.append(f"{scenario_id}: capture_plans must not be empty")
    if any(not value for value in capture_ids) or len(set(capture_ids)) != len(capture_ids):
        issues.append(f"{scenario_id}: capture_id values must be present and unique")
    if set(capture_media_ids) != known_media or len(capture_media_ids) != len(media_ids):
        issues.append(f"{scenario_id}: capture_plans must cover every media exactly once")
    media_by_id = {str(item.get("media_id")): item for item in media if item.get("media_id")}
    checkpoint_time = {
        str(item.get("checkpoint_id")): float(item.get("min_elapsed_sec", 0) or 0)
        for item in checkpoints
    }
    branch_fault_type = {
        "communication_degraded": "communication_degradation",
        "agent_failure": "agent_unavailable",
        "low_score_replan": "low_assessment_score",
        "resource_unavailable": "resource_unavailable",
        "compliance_rejected": "compliance_rejected",
    }
    captures_by_media = {
        str(item.get("media_id")): item for item in captures if item.get("media_id")
    }
    for capture in captures:
        capture_id = capture.get("capture_id") or "<capture>"
        media_item = media_by_id.get(str(capture.get("media_id") or ""))
        capability = capabilities_by_id.get(str(capture.get("capability_id") or ""))
        task = tasks_by_id.get(str(capture.get("required_task_id") or ""))
        if media_item is None or capability is None or task is None:
            issues.append(f"{scenario_id}: capture {capture_id} has unresolved media/capability/task")
            continue
        if capture_id != media_item.get("capture_id"):
            issues.append(f"{scenario_id}: capture {capture_id} does not match media capture_id")
        if capture.get("product_type") != media_item.get("product_type"):
            issues.append(f"{scenario_id}: capture {capture_id} product_type does not match media")
        if capture.get("capability_id") != task.get("capability_id"):
            issues.append(f"{scenario_id}: capture {capture_id} task capability mismatch")
        expected_sensor_id = f"{capability.get('platform_id')}/{capability.get('sensor_instance_id')}"
        if media_item.get("sensor_id") != expected_sensor_id:
            issues.append(f"{scenario_id}: capture {capture_id} sensor does not match media sensor_id")
        if capture.get("platform_id") != capability.get("platform_id") or capture.get("sensor_instance_id") != capability.get("sensor_instance_id"):
            issues.append(f"{scenario_id}: capture {capture_id} platform/sensor mismatch")
        if media_item.get("modality") not in set(capability.get("modalities") or []):
            issues.append(f"{scenario_id}: capture {capture_id} modality is not supported")
        try:
            at_sec = float(capture.get("at_sec"))
            window = task["window"]
            if (
                at_sec != float(media_item.get("at_sec"))
                or at_sec < float(window["start_sec"])
                or at_sec > float(window["end_sec"])
            ):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            issues.append(f"{scenario_id}: capture {capture_id} time is outside its task/media")
            at_sec = -1
        capture_targets = {str(value) for value in capture.get("target_refs") or []}
        task_targets = {str(value) for value in task.get("target_refs") or []}
        if capture_targets - threat_ids or not capture_targets.issubset(task_targets):
            issues.append(f"{scenario_id}: capture {capture_id} has invalid target_refs")
        capture_branches = {str(value) for value in capture.get("branch_ids") or []}
        media_branches = {str(value) for value in media_item.get("branch_ids") or []}
        task_branches = {str(value) for value in task.get("branch_ids") or []}
        if capture_branches != media_branches or not capture_branches or (
            "*" not in task_branches and not capture_branches.issubset(task_branches)
        ):
            issues.append(f"{scenario_id}: capture {capture_id} branch constraints mismatch")
        if not isinstance(capture.get("capture_parameters"), dict) or not capture.get("capture_parameters"):
            issues.append(f"{scenario_id}: capture {capture_id} requires capture_parameters")
        capture_parameters = capture.get("capture_parameters") or {}
        subject_assets = {
            str(value) for value in capture_parameters.get("subject_asset_ids") or [] if value
        }
        if subject_assets - set(asset_ids):
            issues.append(f"{scenario_id}: capture {capture_id} references unknown subject assets")
        reference_media_id = str(capture_parameters.get("reference_media_id") or "")
        if reference_media_id:
            reference = captures_by_media.get(reference_media_id)
            if reference is None or float(reference.get("at_sec", float("inf"))) >= at_sec:
                issues.append(f"{scenario_id}: capture {capture_id} has invalid reference_media_id")
            elif (
                capture_parameters.get("registration_group")
                != (reference.get("capture_parameters") or {}).get("registration_group")
            ):
                issues.append(f"{scenario_id}: capture {capture_id} registration group mismatch")
        if (
            capture.get("product_type") == "raw_sensor_frame"
            and capability.get("sensor_type") in {"electro_optical", "infrared"}
            and not capture_parameters.get("point_at_target")
            and capture_parameters.get("gimbal_azimuth_deg") is None
        ):
            issues.append(f"{scenario_id}: capture {capture_id} requires a gimbal pointing declaration")
        source_kind = capability.get("source_kind")
        expected_source = {
            "raw_sensor_frame": "asset",
            "external_precollected": "external_source",
            "command_product": "command_system",
        }.get(str(capture.get("product_type") or ""))
        if expected_source and source_kind != expected_source:
            issues.append(f"{scenario_id}: capture {capture_id} source_kind conflicts with product_type")
        if (
            capture.get("product_type") == "derived_sensor_product"
            and source_kind not in {"asset", "simulation_processor"}
        ):
            issues.append(f"{scenario_id}: capture {capture_id} derived source is not observable")
        for branch_id in capture_branches - {"*"}:
            fault_type = branch_fault_type.get(branch_id)
            if not fault_type:
                continue
            fault_times = [
                checkpoint_time.get(str(fault.get("at_checkpoint") or ""), float("inf"))
                for fault in scenario.get("fault_injections") or []
                if fault.get("type") == fault_type
            ]
            if not fault_times or min(fault_times) >= at_sec:
                issues.append(f"{scenario_id}: capture {capture_id} branch fault must precede capture")

    known_checkpoints = set(checkpoint_ids)
    for fault in scenario.get("fault_injections") or []:
        if not isinstance(fault, dict):
            continue
        checkpoint_id = str(fault.get("at_checkpoint") or "")
        if checkpoint_id and checkpoint_id not in known_checkpoints:
            issues.append(
                f"{scenario_id}: fault {fault.get('fault_id')} references unknown checkpoint {checkpoint_id}"
            )
    return issues


def validate_scenario_or_raise(scenario: dict[str, Any]) -> dict[str, Any]:
    issues = validate_scenario_definition(scenario)
    if issues:
        raise ScenarioContractError("; ".join(issues))
    return scenario


__all__ = ["ScenarioContractError", "validate_scenario_definition", "validate_scenario_or_raise"]
