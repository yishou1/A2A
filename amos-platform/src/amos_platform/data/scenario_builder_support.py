"""Small shared helpers for the three formal demonstration scenarios."""

from __future__ import annotations

from typing import Any

from amos_platform.data.scenario_capabilities import functional_agents


MEDIA_PRODUCT_TYPES = {
    "raw_sensor_frame",
    "derived_sensor_product",
    "command_product",
    "external_precollected",
}
CAPABILITY_SOURCE_KINDS = {
    "asset",
    "external_source",
    "simulation_processor",
    "command_system",
}


REQUIRED_BACKEND_ROLES: tuple[dict[str, Any], ...] = (
    {"role": "Commander", "required": True, "instance_policy": "single", "orchestration_only": True},
    {"role": "Recon", "required": True, "instance_policy": "at_least_1", "functional_agent_ids": ["A1"]},
    {"role": "Tactical Intelligence", "required": True, "instance_policy": "single", "functional_agent_ids": ["A1"]},
    {"role": "Track Threat", "required": True, "instance_policy": "single", "functional_agent_ids": ["A2"]},
    {"role": "Task Scheduling & Resource Allocation", "required": True, "instance_policy": "single", "functional_agent_ids": ["A3"]},
    {"role": "Decision Planning", "required": True, "instance_policy": "single", "functional_agent_ids": ["A4"]},
    {"role": "Compliance Authorization", "required": True, "instance_policy": "single", "functional_agent_ids": ["A5"]},
    {"role": "Closed Loop", "required": True, "instance_policy": "single", "functional_agent_ids": ["A6"]},
)


def scenario_agents() -> list[dict[str, Any]]:
    return functional_agents()


def required_backend_roles() -> list[dict[str, Any]]:
    return [dict(item) for item in REQUIRED_BACKEND_ROLES]


def _asset_record(raw: Any) -> dict[str, Any]:
    return raw.to_dict() if hasattr(raw, "to_dict") else dict(raw)


def physical_devices(
    assets: list[Any],
    extra_devices: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
) -> list[dict[str, Any]]:
    """Expose physical carriers that can host compute and Agent deployments."""
    devices = []
    for raw in assets:
        asset = _asset_record(raw)
        device_id = str(asset.get("asset_id") or asset.get("id") or "")
        devices.append({
            "device_id": device_id,
            "name": str(asset.get("role") or device_id or "未命名设备"),
            "device_type": str(asset.get("domain") or "platform"),
            "status": str(asset.get("status") or "unknown"),
            "is_simulated": True,
            "asset_ref": device_id,
        })
    devices.extend(dict(item, is_simulated=item.get("is_simulated", True)) for item in extra_devices)
    return devices


def compute_node(
    node_id: str,
    name: str,
    host_device_id: str,
    *,
    host_device_type: str,
    compute_type: str,
    status: str = "online",
    cpu: str = "",
    accelerator: str = "",
    memory_gb: int | None = None,
    network: str = "",
) -> dict[str, Any]:
    node = {
        "node_id": node_id,
        "name": name,
        "host_device_id": host_device_id,
        "host_device_type": host_device_type,
        "compute_type": compute_type,
        "status": status,
    }
    if cpu:
        node["cpu"] = cpu
    if accelerator:
        node["accelerator"] = accelerator
    if memory_gb is not None:
        node["memory_gb"] = memory_gb
    if network:
        node["network"] = network
    return node


def agent_deployment(
    agent_id: str,
    compute_node_id: str,
    *,
    deployment_id: str | None = None,
    runtime_status: str = "planned",
    roles: list[str] | tuple[str, ...] = (),
    notes: str = "",
) -> dict[str, Any]:
    result = {
        "deployment_id": deployment_id or f"{agent_id}@{compute_node_id}",
        "agent_id": agent_id,
        "compute_node_id": compute_node_id,
        "runtime_status": runtime_status,
        "roles": list(roles),
    }
    if notes:
        result["notes"] = notes
    return result


def bind_media_consumers(
    media_cues: list[dict[str, Any]],
    timeline: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach planned backend consumers without claiming they executed.

    The field names deliberately avoid the A2A reserved ``agent*`` prefix.
    Runtime trace remains the only source of execution/verification status.
    """
    consumers: dict[str, dict[str, set[str]]] = {}
    for cue in timeline:
        for media_id in cue.get("media_ids") or []:
            entry = consumers.setdefault(
                str(media_id),
                {"roles": set(), "models": set()},
            )
            entry["roles"].update(str(value) for value in cue.get("functional_agent_ids") or [])
            entry["models"].update(str(value) for value in cue.get("model_requirement_ids") or [])
    result = []
    for raw in media_cues:
        item = dict(raw)
        consumer = consumers.get(str(item.get("media_id")), {"roles": set(), "models": set()})
        item["consumer_context"] = {
            "planned": True,
            "functional_role_ids": sorted(consumer["roles"]),
            "model_requirement_ids": sorted(consumer["models"]),
            "execution_evidence": "backend_trace_required",
        }
        result.append(item)
    return result


def media_record(
    root: str,
    media_id: str,
    filename: str,
    *,
    at_sec: float,
    phase: str,
    title: str,
    caption: str,
    sensor_id: str,
    checksum: str,
    capture_id: str,
    product_type: str,
    branch_ids: list[str] | tuple[str, ...] = ("*",),
    modality: str = "eo_ir",
    mime_type: str = "image/png",
) -> dict[str, Any]:
    if product_type not in MEDIA_PRODUCT_TYPES:
        raise ValueError(f"unsupported media product_type: {product_type}")
    if not capture_id:
        raise ValueError("capture_id is required")
    generated = mime_type == "image/svg+xml"
    uri = f"{root}/{filename}"
    return {
        "media_id": media_id,
        "uri": uri,
        "media_uri": uri,
        "mime_type": mime_type,
        "media_type": mime_type,
        "kind": "telemetry" if generated else "image",
        "modality": modality,
        "pipeline_role": "cognition_context",
        "text": caption,
        "checksum": checksum,
        "phase": phase,
        "at_sec": at_sec,
        "title": title,
        "caption": caption,
        "sensor_id": sensor_id,
        "capture_id": capture_id,
        "product_type": product_type,
        "branch_ids": list(branch_ids),
        "source_mode": "code_generated_simulation" if generated else "ai_generated_simulation",
        "provenance": {
            "mode": "simulation",
            "generator": "amos_svg_evidence" if generated else "openai_imagegen",
            "simulated": True,
        },
    }


def sensor_capability(
    capability_id: str,
    platform_id: str,
    sensor_instance_id: str,
    *,
    sensor_type: str,
    modalities: list[str] | tuple[str, ...],
    parameters: dict[str, Any],
    source_kind: str = "asset",
    configured_sensor: str | None = None,
) -> dict[str, Any]:
    """Declare one concrete sensor or processing capability instance."""
    if source_kind not in CAPABILITY_SOURCE_KINDS:
        raise ValueError(f"unsupported capability source_kind: {source_kind}")
    record = {
        "capability_id": capability_id,
        "platform_id": platform_id,
        "sensor_instance_id": sensor_instance_id,
        "sensor_type": sensor_type,
        "modalities": list(modalities),
        "source_kind": source_kind,
        "parameters": dict(parameters),
    }
    if configured_sensor:
        record["configured_sensor"] = configured_sensor
    return record


def sensor_task(
    task_id: str,
    capability: dict[str, Any],
    *,
    task_type: str,
    start_sec: float,
    end_sec: float,
    target_refs: list[str] | tuple[str, ...] = (),
    branch_ids: list[str] | tuple[str, ...] = ("*",),
) -> dict[str, Any]:
    """Schedule a bounded acquisition, derivation, or command-product task."""
    return {
        "task_id": task_id,
        "platform_id": capability["platform_id"],
        "capability_id": capability["capability_id"],
        "task_type": task_type,
        "window": {"start_sec": start_sec, "end_sec": end_sec},
        "target_refs": list(target_refs),
        "branch_ids": list(branch_ids),
    }


def capture_plan(
    capture_id: str,
    media_id: str,
    capability: dict[str, Any],
    task: dict[str, Any],
    *,
    product_type: str,
    at_sec: float,
    target_refs: list[str] | tuple[str, ...] = (),
    parameters: dict[str, Any],
    branch_ids: list[str] | tuple[str, ...] = ("*",),
) -> dict[str, Any]:
    """Bind one media product to the capability and task that can produce it."""
    if product_type not in MEDIA_PRODUCT_TYPES:
        raise ValueError(f"unsupported capture product_type: {product_type}")
    return {
        "capture_id": capture_id,
        "media_id": media_id,
        "product_type": product_type,
        "platform_id": capability["platform_id"],
        "sensor_instance_id": capability["sensor_instance_id"],
        "capability_id": capability["capability_id"],
        "required_task_id": task["task_id"],
        "at_sec": at_sec,
        "target_refs": list(target_refs),
        "capture_parameters": dict(parameters),
        "branch_ids": list(branch_ids),
    }


def asset_profiles(assets: list[Any]) -> list[dict[str, Any]]:
    """Build lightweight, explicitly simulated capability profiles for all assets."""
    profiles = []
    for raw in assets:
        asset = raw.to_dict() if hasattr(raw, "to_dict") else dict(raw)
        platform_id = str(asset.get("asset_id") or asset.get("id") or "")
        domain = str(asset.get("domain") or "ground")
        role = str(asset.get("role") or "")
        speed = float(asset.get("speed_kts", 0) or 0)
        position = asset.get("position") or {}
        if domain == "space":
            constraint = "orbital_ground_track"
            turn_rate = 0.15
            link_range_km = 2000
        elif domain == "air":
            constraint = "air_corridor"
            turn_rate = 3.0
            link_range_km = 180
        elif domain == "maritime":
            constraint = "sea_operating_area"
            turn_rate = 0.8
            link_range_km = 90
        else:
            constraint = "fixed_site" if speed <= 0 else "road_network"
            turn_rate = 12.0 if speed > 0 else 0.0
            link_range_km = 45
        sensor_list = list(asset.get("sensors") or [])
        sensors = set(sensor_list)
        is_relay = "中继" in role or "DATALINK" in sensors
        is_command = "指挥" in role or "C2" in platform_id
        is_satcom = domain == "space" or "SATCOM" in sensors
        communications = [{
            "band": "SATCOM" if is_satcom else "SIM-LINK-L",
            # SATCOM range models slant-range access to relay spacecraft, not
            # a local line-of-sight radio.  The previous 2,000 km cap made a
            # geosynchronous relay impossible even when both endpoints
            # explicitly carried SATCOM terminals.
            "range_km": 45000 if is_satcom else (max(link_range_km, 220) if is_relay else link_range_km),
            "bandwidth_mbps": 24 if is_relay or is_command else 8,
            "role": "relay" if is_relay else ("network_hub" if is_command else "mesh_member"),
            "simulation_value": True,
        }]
        supported_tasks = [f"sense:{sensor}" for sensor in sensor_list]
        if speed > 0:
            supported_tasks.append(f"move:{domain}")
        if asset.get("weapons"):
            supported_tasks.append("simulate_action")
        profiles.append({
            "platform_id": platform_id,
            "motion": {
                "cruise_speed_kts": speed,
                "max_speed_kts": round(speed * 1.25, 1) if speed > 0 else 0,
                "max_turn_rate_dps": turn_rate,
                "altitude_ft": float(position.get("alt_ft", 0) or 0),
                "movement_constraint": constraint,
                "simulation_value": True,
            },
            "endurance": {
                "nominal_hr": float(asset.get("endurance_hr", 0) or 0),
                "basis": "scenario_simulation",
            },
            "communications": communications,
            "supported_tasks": supported_tasks or ["status_reporting"],
            "operational_state_model": {
                "states": ["staged", "active", "holding", "degraded", "unavailable"],
                "initial_state": str(asset.get("status") or "active"),
                "health_fields": sorted(str(key) for key in (asset.get("health") or {})),
                "transition_source": "simulation_clock_or_fault_injection",
            },
        })
    return profiles


__all__ = [
    "CAPABILITY_SOURCE_KINDS",
    "MEDIA_PRODUCT_TYPES",
    "capture_plan",
    "bind_media_consumers",
    "asset_profiles",
    "media_record",
    "required_backend_roles",
    "scenario_agents",
    "sensor_capability",
    "sensor_task",
]
