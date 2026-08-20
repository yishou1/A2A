"""Runtime media acquisition and evidence-chain capture.

Scenario ``capture_plans`` are private scheduling intent.  This module turns a
plan into a single immutable public capture record only when the current
platform, task and sensor evidence supports it.  Truth identifiers are used
inside the evaluator and are never copied into public records.
"""

from __future__ import annotations

from copy import deepcopy
import math
from typing import Any

from amos_platform.domain.policies.visibility import sanitize_media_payload
from amos_platform.sensors.coverage import (
    SENSOR_COVERAGE,
    bearing_deg,
    distance_nm,
    elevation_angle_deg,
    normalize_sensor_name,
    slant_range_nm,
)


PUBLIC_MEDIA_KEYS = {
    "uri", "media_uri", "mime_type", "media_type", "kind", "modality",
    "pipeline_role", "text", "checksum", "phase", "title", "caption",
    "source_mode", "provenance", "sensor_id",
    "consumer_context",
}
ACTIVE_PLATFORM_STATES = {"active", "operational", "holding"}
ACTIVE_TASK_STATES = {"active", "running", "executing", "completed", "done", "scheduled"}
SENSOR_PRODUCT_TYPES = {"sensor", "image", "imagery", "raw", "observation"}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _angle_delta(first: float, second: float) -> float:
    return abs((first - second + 180.0) % 360.0 - 180.0)


def _position(entity: dict[str, Any]) -> dict[str, float]:
    raw = entity.get("position") if isinstance(entity.get("position"), dict) else entity
    return {
        "lat": _number(raw.get("lat")),
        "lng": _number(raw.get("lng", raw.get("lon"))),
        "alt_ft": _number(raw.get("alt_ft", raw.get("altitude_ft"))),
    }


def _sensor_token(value: Any) -> str:
    normalized = normalize_sensor_name(str(value or ""))
    return "".join(character for character in normalized.casefold() if character.isalnum())


def _sensor_matches(expected: Any, observed: Any) -> bool:
    left = _sensor_token(expected)
    right = _sensor_token(observed)
    return bool(left and right and (left in right or right in left))


def _sensor_name(plan: dict[str, Any]) -> str:
    instance = str(plan.get("sensor_instance_id") or "")
    configured = plan.get("configured_sensor")
    configured_name = configured if isinstance(configured, str) else None
    return str(configured_name or plan.get("sensor_type") or instance.rpartition("/")[2] or instance)


def _sensor_config(plan: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for key in ("sensor", "sensor_config", "camera", "capture_parameters"):
        value = plan.get(key)
        if isinstance(value, dict):
            merged.update(value)
    sensor_name = _sensor_name(plan)
    model = SENSOR_COVERAGE.get(normalize_sensor_name(sensor_name), {})
    aliases = {
        "effective_range_nm": "range_nm",
        "horizontal_fov_deg": "fov_deg",
        "resolution_px": "resolution",
        "look_angle_deg": "depression_angle_deg",
    }
    for source, target in aliases.items():
        if merged.get(target) is None and merged.get(source) is not None:
            merged[target] = merged[source]
    for key in ("range_nm", "fov_deg"):
        if merged.get(key) is None and model.get(key) is not None:
            merged[key] = model[key]
    resolution = merged.get("resolution")
    if (
        merged.get("vertical_fov_deg") is None
        and merged.get("fov_deg") is not None
        and isinstance(resolution, (list, tuple))
        and len(resolution) == 2
        and _number(resolution[0]) > 0
        and _number(resolution[1]) > 0
    ):
        horizontal = math.radians(_number(merged["fov_deg"]))
        aspect = _number(resolution[1]) / _number(resolution[0])
        merged["vertical_fov_deg"] = math.degrees(
            2.0 * math.atan(math.tan(horizontal / 2.0) * aspect),
        )
    return merged


def _product_type(plan: dict[str, Any]) -> str:
    value = str(plan.get("product_type") or "sensor").strip().casefold()
    aliases = {
        "external_precollected": "external",
        "derived_sensor_product": "derived",
        "command_product": "command",
        "raw_sensor_frame": "sensor",
    }
    if value in aliases:
        return aliases[value]
    if value in {"external", "derived", "command"}:
        return value
    return "sensor" if value in SENSOR_PRODUCT_TYPES else "sensor"


class MediaCaptureRuntime:
    """Evaluate private plans and retain immutable, public-safe captures."""

    def __init__(self) -> None:
        self.reset([], [])

    def reset(
        self,
        capture_plans: list[dict[str, Any]] | None,
        media_cues: list[dict[str, Any]] | None,
        *,
        task_windows: Any = None,
        asset_capabilities: list[dict[str, Any]] | None = None,
        default_branch: str = "standard",
    ) -> None:
        self._media = {
            str(item.get("media_id")): deepcopy(item)
            for item in media_cues or []
            if isinstance(item, dict) and item.get("media_id")
        }
        raw_plans = [deepcopy(item) for item in capture_plans or [] if isinstance(item, dict)]
        # Compatibility for legacy/custom scenarios. Formal v2 scenarios carry
        # explicit capture plans and therefore never use this timed fallback.
        if not raw_plans:
            raw_plans = [
                {
                    "capture_id": f"CAP-{media_id}",
                    "media_id": media_id,
                    "product_type": "external",
                    "at_sec": cue.get("at_sec", 0),
                    "legacy_timed_source": True,
                }
                for media_id, cue in self._media.items()
            ]
        self._plans = raw_plans
        self._task_windows = deepcopy(task_windows or {})
        self._capabilities = {
            str(item.get("capability_id")): deepcopy(item)
            for item in asset_capabilities or []
            if isinstance(item, dict) and item.get("capability_id")
        }
        self._default_branch = str(default_branch or "standard")
        self._captures: dict[str, dict[str, Any]] = {}
        self._capture_order: list[str] = []

    @property
    def captured_media_ids(self) -> set[str]:
        return set(self._captures)

    def public_captures(self) -> list[dict[str, Any]]:
        return [deepcopy(self._captures[media_id]) for media_id in self._capture_order]

    def _task_window(self, plan: dict[str, Any]) -> dict[str, Any]:
        embedded = plan.get("task_window")
        if isinstance(embedded, dict):
            return embedded
        task_id = str(plan.get("required_task_id") or "")
        if isinstance(self._task_windows, dict):
            candidate = self._task_windows.get(task_id)
            return candidate if isinstance(candidate, dict) else {}
        for candidate in self._task_windows if isinstance(self._task_windows, list) else []:
            if isinstance(candidate, dict) and str(candidate.get("task_id") or candidate.get("id") or "") == task_id:
                window = candidate.get("window")
                return window if isinstance(window, dict) else candidate
        return {}

    def _task_ready(
        self,
        plan: dict[str, Any],
        tasks: list[dict[str, Any]],
        elapsed: float,
        branch: str,
    ) -> bool:
        task_id = str(plan.get("required_task_id") or "")
        window = self._task_window(plan)
        start = _number(
            window.get("start_sec", plan.get("window_start_sec", plan.get("at_sec", 0))),
        )
        end_raw = window.get("end_sec", plan.get("window_end_sec"))
        if elapsed < start or (end_raw is not None and elapsed > _number(end_raw)):
            return False
        if not task_id:
            return True
        matching = [
            task for task in tasks
            if isinstance(task, dict)
            and str(task.get("task_id") or task.get("id") or "") == task_id
        ]
        if matching:
            return any(str(task.get("status") or "active").casefold() in ACTIVE_TASK_STATES for task in matching)
        # A declared scenario task window is itself the runtime schedule.  A
        # bare required_task_id without either a task or a window is not proof.
        scheduled = None
        for candidate in self._task_windows if isinstance(self._task_windows, list) else []:
            if isinstance(candidate, dict) and str(candidate.get("task_id") or candidate.get("id") or "") == task_id:
                scheduled = candidate
                break
        if not scheduled:
            return False
        if not self._branch_ready(scheduled, branch):
            return False
        for key in ("platform_id", "capability_id"):
            if scheduled.get(key) and plan.get(key) and str(scheduled[key]) != str(plan[key]):
                return False
        return bool(window)

    @staticmethod
    def _branch_ready(plan: dict[str, Any], branch: str) -> bool:
        branch_ids = {str(value) for value in plan.get("branch_ids") or [] if value}
        return not branch_ids or "*" in branch_ids or branch in branch_ids

    def _plan_with_capability(self, plan: dict[str, Any]) -> dict[str, Any]:
        capability = self._capabilities.get(str(plan.get("capability_id") or "")) or {}
        merged = deepcopy(capability)
        parameters = capability.get("parameters")
        if isinstance(parameters, dict):
            merged["sensor_config"] = deepcopy(parameters)
        merged.update(plan)
        if capability.get("configured_sensor") is not None:
            merged["configured_sensor"] = capability.get("configured_sensor")
        return merged

    @staticmethod
    def _command_ready(
        plan: dict[str, Any],
        tasks: list[dict[str, Any]],
        story: dict[str, Any],
    ) -> bool:
        analysis = story.get("agent_analysis") or {}
        return (
            analysis.get("source") == "commander_workflow"
            and str(analysis.get("status") or "completed").casefold() == "completed"
        )

    @staticmethod
    def _track_links(tracks: dict[str, Any], observation_ids: set[str]) -> list[str]:
        result: list[str] = []
        for track_id, track in tracks.items():
            refs = getattr(track, "source_refs", None)
            if refs is None and isinstance(track, dict):
                refs = track.get("source_refs")
            if any(
                isinstance(ref, dict) and str(ref.get("observation_id") or "") in observation_ids
                for ref in refs or []
            ):
                result.append(str(getattr(track, "id", None) or (track.get("id") if isinstance(track, dict) else None) or track_id))
        return sorted(set(result))

    def _sensor_evidence(
        self,
        plan: dict[str, Any],
        *,
        assets: dict[str, dict[str, Any]],
        threats: dict[str, dict[str, Any]],
        observation_batch: dict[str, Any],
        truth_associations: list[dict[str, Any]],
        sensor_fusion: Any,
        tracks: dict[str, Any],
    ) -> tuple[dict[str, Any], list[str], list[str]] | None:
        platform_id = str(plan.get("platform_id") or "")
        platform = assets.get(platform_id)
        if not platform or str(platform.get("status") or "").casefold() not in ACTIVE_PLATFORM_STATES:
            return None
        if plan.get("configured_sensor") is False:
            return None
        sensor_name = _sensor_name(plan)
        if not sensor_name or not any(_sensor_matches(sensor_name, item) for item in platform.get("sensors") or []):
            return None

        associations_by_observation: dict[str, str] = {}
        for association in truth_associations or []:
            if not isinstance(association, dict) or not association.get("observation_id"):
                continue
            associations_by_observation[str(association["observation_id"])] = str(association.get("truth_id") or "")
        targets = {str(value) for value in plan.get("target_refs") or [] if value}
        config = _sensor_config(plan)
        required_damage_state = str(config.get("required_damage_state") or "").strip().casefold()
        if required_damage_state and any(
            str((threats.get(truth_id) or {}).get("damage_state") or "").strip().casefold()
            != required_damage_state
            for truth_id in targets
        ):
            return None
        platform_position = _position(platform)
        heading = _number(platform.get("heading_deg", platform.get("heading")))
        if config.get("gimbal_azimuth_deg") is not None:
            boresight = _number(config.get("gimbal_azimuth_deg")) % 360.0
        elif config.get("point_at_target") and targets:
            target = threats.get(sorted(targets)[0])
            if target:
                target_position = _position(target)
                boresight = bearing_deg(
                    platform_position["lat"], platform_position["lng"],
                    target_position["lat"], target_position["lng"],
                )
            else:
                boresight = heading
        else:
            boresight = (heading + _number(config.get("boresight_offset_deg", config.get("azimuth_offset_deg")))) % 360.0
        config["_resolved_azimuth_deg"] = boresight
        configured_depression = config.get("depression_angle_deg")
        resolved_depression = (
            _number(configured_depression)
            if configured_depression is not None
            else None
        )
        if config.get("point_at_target") and targets:
            pointing_target = threats.get(sorted(targets)[0])
            if pointing_target:
                pointing_position = _position(pointing_target)
                pointing_ground_range = distance_nm(
                    platform_position["lat"], platform_position["lng"],
                    pointing_position["lat"], pointing_position["lng"],
                )
                resolved_depression = -elevation_angle_deg(
                    pointing_ground_range,
                    platform_position["alt_ft"],
                    pointing_position["alt_ft"],
                )
        config["_commanded_depression_angle_deg"] = configured_depression
        config["_resolved_depression_angle_deg"] = resolved_depression
        max_range = _number(config.get("range_nm"), float("inf"))
        fov = _number(config.get("fov_deg"), 360.0)
        vertical_fov = _number(config.get("vertical_fov_deg"), 180.0)

        observations = []
        for item in observation_batch.get("observations") or []:
            if not isinstance(item, dict) or str(item.get("asset_id") or "") != platform_id:
                continue
            if not _sensor_matches(sensor_name, item.get("sensor_id") or item.get("modality")):
                continue
            observation_id = str(item.get("observation_id") or "")
            if targets and associations_by_observation.get(observation_id) not in targets:
                continue
            observations.append(item)

        # An explicitly scheduled area-surveillance frame is valid evidence of
        # where the sensor looked, even when it contains no detected contact.
        # This is distinct from silently treating a missed target as evidence.
        if not observations and not targets and config.get("allow_empty_frame"):
            return config, [], []

        # A gimballed capture can legitimately point away from platform
        # heading. If the ordinary surveillance pass did not produce that
        # look, form a deterministic capture observation after applying the
        # same range/FOV and observation-window checks.
        empty_frame_geometry_valid = False
        if not observations and targets and (
            config.get("point_at_target") or config.get("gimbal_azimuth_deg") is not None
        ):
            elapsed = _number(observation_batch.get("sim_time"))
            for target_index, truth_id in enumerate(sorted(targets)):
                target = threats.get(truth_id)
                if not target:
                    continue
                target_position = _position(target)
                actual_range = distance_nm(
                    platform_position["lat"], platform_position["lng"],
                    target_position["lat"], target_position["lng"],
                )
                actual_slant_range = slant_range_nm(
                    actual_range,
                    platform_position["alt_ft"],
                    target_position["alt_ft"],
                )
                actual_elevation = elevation_angle_deg(
                    actual_range,
                    platform_position["alt_ft"],
                    target_position["alt_ft"],
                )
                actual_bearing = bearing_deg(
                    platform_position["lat"], platform_position["lng"],
                    target_position["lat"], target_position["lng"],
                )
                if actual_slant_range > max_range or (
                    fov < 360 and _angle_delta(actual_bearing, boresight) > fov / 2.0
                ) or (
                    resolved_depression is not None
                    and vertical_fov < 180
                    and abs((-actual_elevation) - resolved_depression) > vertical_fov / 2.0
                ):
                    continue
                window = target.get("_observation_window") or {}
                if elapsed < _number(window.get("start_sec")):
                    continue
                if window.get("end_sec") is not None and elapsed >= _number(window.get("end_sec")):
                    continue
                empty_frame_geometry_valid = True
                tick_id = int(observation_batch.get("tick_id", 0) or 0)
                observation_id = (
                    f"OBS-CAP-{str(plan.get('capture_id') or 'capture')}-"
                    f"{tick_id:06d}-{target_index:02d}"
                )
                confidence = max(0.1, min(0.99, 1.0 - actual_slant_range / max(max_range, 1.0) * 0.5))
                observation = {
                    "observation_id": observation_id,
                    "sensor_id": str(plan.get("sensor_instance_id") or sensor_name),
                    "asset_id": platform_id,
                    "sim_time": elapsed,
                    "modality": (plan.get("modalities") or [sensor_name])[0],
                    "bearing_deg": round(actual_bearing, 2),
                    "range_nm": round(actual_range, 2),
                    "slant_range_nm": round(actual_slant_range, 2),
                    "elevation_deg": round(actual_elevation, 2),
                    "domain_hint": target.get("domain"),
                    "range_rate_kts": round(_number(target.get("speed_kts")) - _number(platform.get("speed_kts")), 2),
                    "snr_db": round(30 * confidence, 2),
                    "covariance": {
                        "bearing_deg": round(max(0.5, 5 * (1 - confidence)), 2),
                        "range_nm": round(max(0.1, actual_range * 0.03), 2),
                    },
                    "confidence": round(confidence, 2),
                    "quality": "nominal" if confidence >= 0.5 else "low",
                    "source": "media_capture_runtime",
                    "visibility": "agent-visible:observation",
                }
                observation_batch.setdefault("observations", []).append(observation)
                association = {
                    "truth_id": truth_id,
                    "observation_id": observation_id,
                    "association_type": "sensor_capture",
                    "confidence": round(confidence, 2),
                    "visibility": "truth/internal",
                }
                truth_associations.append(association)
                associations_by_observation[observation_id] = truth_id
                # A directed post-strike sensor can still observe a disabled
                # target. Keep that evidence in the capture batch without
                # recreating the neutralized target as an active fused track.
                if not target.get("neutralized"):
                    fusion_event = sensor_fusion.update_from_observation(observation, observation_batch)
                    if fusion_event:
                        sensor_fusion.events.append(fusion_event)
                observations.append(observation)
        if not observations and not empty_frame_geometry_valid:
            return None
        supported: list[dict[str, Any]] = []
        for observation in observations:
            truth_id = associations_by_observation.get(str(observation.get("observation_id") or ""))
            target = threats.get(truth_id or "")
            if target:
                target_position = _position(target)
                actual_range = distance_nm(
                    platform_position["lat"], platform_position["lng"],
                    target_position["lat"], target_position["lng"],
                )
                actual_slant_range = slant_range_nm(
                    actual_range,
                    platform_position["alt_ft"],
                    target_position["alt_ft"],
                )
                actual_elevation = elevation_angle_deg(
                    actual_range,
                    platform_position["alt_ft"],
                    target_position["alt_ft"],
                )
                actual_bearing = bearing_deg(
                    platform_position["lat"], platform_position["lng"],
                    target_position["lat"], target_position["lng"],
                )
            else:
                actual_range = _number(observation.get("range_nm"), float("inf"))
                actual_slant_range = _number(observation.get("slant_range_nm"), actual_range)
                actual_elevation = _number(observation.get("elevation_deg"))
                actual_bearing = _number(observation.get("bearing_deg"))
            if actual_slant_range > max_range:
                continue
            if fov < 360 and _angle_delta(actual_bearing, boresight) > fov / 2.0:
                continue
            if (
                resolved_depression is not None
                and vertical_fov < 180
                and abs((-actual_elevation) - resolved_depression) > vertical_fov / 2.0
            ):
                continue
            supported.append(observation)
        if not supported and empty_frame_geometry_valid:
            return config, [], []
        if not supported:
            return None
        observation_ids = sorted({str(item["observation_id"]) for item in supported if item.get("observation_id")})
        return config, observation_ids, self._track_links(tracks, set(observation_ids))

    def _derived_evidence(
        self,
        plan: dict[str, Any],
        *,
        assets: dict[str, dict[str, Any]],
        observation_batch: dict[str, Any],
        truth_associations: list[dict[str, Any]],
        observation_history: list[dict[str, Any]] | None,
        truth_association_history: list[dict[str, Any]] | None,
        tracks: dict[str, Any],
    ) -> tuple[list[str], list[str]] | None:
        source_kind = str(plan.get("source_kind") or "asset").casefold()
        parameters = (
            plan.get("capture_parameters")
            if isinstance(plan.get("capture_parameters"), dict)
            else {}
        )
        data_source = str(parameters.get("data_source") or "sensor_observations").casefold()
        platform_id = str(plan.get("platform_id") or "")
        if platform_id and source_kind == "asset":
            platform = assets.get(platform_id)
            if not platform or str(platform.get("status") or "").casefold() not in ACTIVE_PLATFORM_STATES:
                return None
        required_captures = {str(value) for value in plan.get("source_capture_ids") or [] if value}
        captured_ids = {str(item.get("capture_id")) for item in self._captures.values()}
        if required_captures and not required_captures.issubset(captured_ids):
            return None
        sensor_name = _sensor_name(plan)
        sensor_config = _sensor_config(plan)
        source_sensors = [sensor_name, *[
            str(value)
            for value in sensor_config.get("source_sensors") or []
            if value
        ]]
        targets = {str(value) for value in plan.get("target_refs") or [] if value}
        current_time = _number(observation_batch.get("sim_time"))
        observation_window_sec = _number(parameters.get("observation_window_sec"), 0.0)
        if observation_window_sec > 0:
            cutoff = current_time - observation_window_sec
            candidate_observations = [
                item for item in observation_history or []
                if cutoff <= _number(item.get("sim_time")) <= current_time
            ]
            association_source = [
                item for item in truth_association_history or []
                if cutoff <= _number(item.get("sim_time")) <= current_time
            ]
        else:
            candidate_observations = list(observation_batch.get("observations") or [])
            association_source = list(truth_associations or [])
        truth_by_observation = {
            str(item.get("observation_id")): str(item.get("truth_id") or "")
            for item in association_source
            if isinstance(item, dict) and item.get("observation_id")
        }
        selected_observations = []
        for item in candidate_observations:
            if not isinstance(item, dict) or not item.get("observation_id"):
                continue
            if source_kind == "asset":
                if platform_id and str(item.get("asset_id") or "") != platform_id:
                    continue
                if source_sensors and not any(
                    _sensor_matches(expected, item.get("sensor_id") or item.get("modality"))
                    for expected in source_sensors
                ):
                    continue
            observation_id = str(item["observation_id"])
            if targets and truth_by_observation.get(observation_id) not in targets:
                continue
            selected_observations.append(item)
        observation_ids = sorted({str(item["observation_id"]) for item in selected_observations})
        track_ids = self._track_links(tracks, set(observation_ids))
        requires_observation = bool(parameters.get(
            "requires_observation",
            bool(targets) or data_source == "sensor_observations",
        ))
        if source_kind == "asset" and sensor_name and requires_observation and not observation_ids:
            return None
        if targets and not observation_ids:
            return None
        state_sources = {"asset_state", "network", "task_state", "track_fusion"}
        if not (
            required_captures
            or platform_id
            or observation_ids
            or track_ids
            or source_kind in {"simulation_processor", "command_system"}
            or data_source in state_sources
        ):
            return None
        return observation_ids, track_ids

    def _freeze_capture(
        self,
        plan: dict[str, Any],
        cue: dict[str, Any],
        *,
        elapsed: float,
        tick_id: int,
        run_id: str,
        platform: dict[str, Any] | None,
        config: dict[str, Any],
        observation_ids: list[str],
        track_ids: list[str],
        source_kind: str,
        product_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        capture_id = str(plan.get("capture_id") or f"CAP-{cue['media_id']}")
        record = {
            key: deepcopy(value)
            for key, value in cue.items()
            if key in PUBLIC_MEDIA_KEYS
        }
        record.update({
            "capture_id": capture_id,
            "media_id": str(cue["media_id"]),
            "product_type": str(plan.get("product_type") or "raw_sensor_frame"),
            "at_sec": round(elapsed, 3),
            "captured_at_sim_time": round(elapsed, 3),
            "captured_at_tick_id": int(tick_id),
            "run_id": run_id or None,
            "platform_id": str(plan.get("platform_id") or "") or None,
            "sensor_instance_id": str(plan.get("sensor_instance_id") or "") or None,
            "capability_id": str(plan.get("capability_id") or "") or None,
            "observation_ids": observation_ids,
            "track_ids": track_ids,
            "source_capture_ids": [str(value) for value in plan.get("source_capture_ids") or [] if value],
            "capture_provenance": {
                "source_kind": source_kind,
                "capability_source_kind": str(plan.get("source_kind") or "asset"),
                "simulated": True,
                "frozen": True,
            },
        })
        parameters = plan.get("capture_parameters")
        if isinstance(parameters, dict):
            record["capture_parameters"] = deepcopy(parameters)
        if plan.get("dynamic_uri"):
            record["dynamic_uri"] = str(plan["dynamic_uri"])
            record["uri"] = str(plan["dynamic_uri"])
            record["media_uri"] = str(plan["dynamic_uri"])
        if plan.get("dynamic_checksum"):
            record["dynamic_checksum"] = str(plan["dynamic_checksum"])
            record["checksum"] = str(plan["dynamic_checksum"])
        if platform is not None:
            position = _position(platform)
            heading = _number(platform.get("heading_deg", platform.get("heading")))
            record["platform_pose"] = {
                "lat": round(position["lat"], 6),
                "lon": round(position["lng"], 6),
                "alt_ft": round(position["alt_ft"], 1),
                "heading_deg": round(heading, 2),
                "speed_kts": round(_number(platform.get("speed_kts")), 2),
            }
            record["sensor_pose"] = {
                "azimuth_deg": round(_number(config.get("_resolved_azimuth_deg"), heading) % 360.0, 2),
                "depression_angle_deg": (
                    round(_number(config.get("_resolved_depression_angle_deg")), 2)
                    if config.get("_resolved_depression_angle_deg") is not None
                    else None
                ),
                "planned_depression_angle_deg": config.get("_commanded_depression_angle_deg"),
            }
        public_sensor = {
            key: deepcopy(config.get(key))
            for key in (
                "range_nm", "fov_deg", "vertical_fov_deg", "resolution",
                "width_px", "height_px", "ground_sample_distance_m",
            )
            if config.get(key) is not None
        }
        if public_sensor:
            record["sensor_config"] = public_sensor
        if product_data:
            record["product_data"] = deepcopy(product_data)
        # The source state passed later to operator/agent sanitizers performs a
        # second leak check using actual truth terms.
        return sanitize_media_payload(record)

    @staticmethod
    def _derived_product_data(
        engine: Any,
        observation_ids: list[str],
        track_ids: list[str],
        plan: dict[str, Any],
    ) -> dict[str, Any]:
        observation_set = set(observation_ids)
        track_set = set(track_ids)
        observation_pool = {
            str(item.get("observation_id")): item
            for item in [
                *(getattr(engine.sensor_fusion, "observation_history", []) or []),
                *(engine.sensor_fusion.last_observation_batch.get("observations") or []),
            ]
            if isinstance(item, dict) and item.get("observation_id")
        }
        observations = [
            {
                key: deepcopy(item.get(key))
                for key in (
                    "observation_id", "sensor_id", "asset_id", "sim_time", "modality",
                    "domain_hint", "bearing_deg", "range_nm", "slant_range_nm",
                    "elevation_deg", "range_rate_kts", "snr_db",
                    "frequency_mhz", "power_dbm",
                    "covariance", "confidence", "quality", "capture_ids", "media_ids",
                )
                if item.get(key) is not None
            }
            for item in observation_pool.values()
            if isinstance(item, dict) and str(item.get("observation_id") or "") in observation_set
        ]
        tracks = []
        for track_id, track in engine.sensor_fusion.tracks.items():
            public_id = str(getattr(track, "id", None) or track_id)
            if public_id not in track_set:
                continue
            refs = getattr(track, "source_refs", []) or []
            tracks.append({
                "track_id": public_id,
                "lat": round(_number(getattr(track, "lat", None)), 6),
                "lon": round(_number(getattr(track, "lng", None)), 6),
                "confidence": round(_number(getattr(track, "confidence", None)), 3),
                "domain_hint": getattr(track, "domain_hint", None),
                "source_observation_ids": sorted({
                    str(ref["observation_id"])
                    for ref in refs
                    if isinstance(ref, dict) and ref.get("observation_id")
                }),
            })
        assets = []
        for asset_id, asset in engine.assets.items():
            position = _position(asset)
            assets.append({
                "platform_id": str(asset_id),
                "status": asset.get("status"),
                "position": {
                    "lat": round(position["lat"], 6),
                    "lon": round(position["lng"], 6),
                    "alt_ft": round(position["alt_ft"], 1),
                },
                "heading_deg": round(_number(asset.get("heading_deg", asset.get("heading"))), 2),
                "speed_kts": round(_number(asset.get("speed_kts")), 2),
                "sensors": list(asset.get("sensors") or []),
                "health": {
                    key: deepcopy(value)
                    for key, value in (asset.get("health") or {}).items()
                    if key in {"battery_pct", "fuel_pct", "comms_strength", "gps_fix"}
                },
            })
        tasks = [
            {
                key: deepcopy(task.get(key))
                for key in ("task_id", "platform_id", "capability_id", "task_type", "status", "window")
                if task.get(key) is not None
            }
            for task in engine.tasks
            if isinstance(task, dict)
        ]
        parameters = (
            plan.get("capture_parameters")
            if isinstance(plan.get("capture_parameters"), dict)
            else {}
        )
        data_source = str(parameters.get("data_source") or "sensor_observations")
        result: dict[str, Any] = {
            "renderer_type": parameters.get("renderer_type"),
            "data_source": data_source,
            "frozen_at_sim_time": round(_number(engine.clock.get("elapsed_sec")), 3),
        }
        observation_window_sec = _number(parameters.get("observation_window_sec"), 0.0)
        if observation_window_sec > 0:
            frozen_at = _number(engine.clock.get("elapsed_sec"))
            result["observation_window"] = {
                "start_sec": round(max(0.0, frozen_at - observation_window_sec), 3),
                "end_sec": round(frozen_at, 3),
                "observation_count": len(observations),
            }
        if data_source in {"sensor_observations", "track_fusion"}:
            result["observations"] = observations
            result["tracks"] = tracks
        if data_source == "network":
            topology = deepcopy(engine.mesh.get_topology())
            monitor_platform = str(plan.get("platform_id") or "")
            window_sec = _number(parameters.get("observation_window_sec"), 0.0)
            if monitor_platform in engine.assets:
                topology["link_records"] = [
                    link for link in topology.get("link_records") or []
                    if monitor_platform in {str(link.get("from") or ""), str(link.get("to") or "")}
                ]
                history = []
                cutoff = _number(engine.clock.get("elapsed_sec")) - window_sec if window_sec > 0 else float("-inf")
                for sample in topology.get("history_records") or []:
                    if _number(sample.get("sim_time")) < cutoff:
                        continue
                    links = [
                        link for link in sample.get("links") or []
                        if monitor_platform in {str(link.get("from") or ""), str(link.get("to") or "")}
                    ]
                    history.append({"sim_time": sample.get("sim_time"), "links": links})
                topology["history_records"] = history
                topology["monitor_platform_id"] = monitor_platform
            result["network"] = topology
            result["assets"] = assets
        if data_source == "asset_state":
            result["assets"] = assets
            result["tasks"] = tasks
            result["network"] = deepcopy(engine.mesh.get_topology())
        if data_source == "task_state":
            result["tasks"] = tasks
            result["assets"] = assets
            analysis = engine.scenario_story.get("agent_analysis") or {}
            if isinstance(analysis, dict):
                result["workflow"] = {
                    key: deepcopy(analysis.get(key))
                    for key in (
                        "source", "status", "workflow_id", "applied_assessment_count",
                        "source_run_id", "source_snapshot_sequence", "source_simulation_time_sec",
                    )
                    if analysis.get(key) is not None
                }
        return {key: value for key, value in result.items() if value is not None}

    def evaluate(self, engine: Any) -> list[dict[str, Any]]:
        """Capture every newly satisfied due plan and inject its media refs."""
        elapsed = _number(engine.clock.get("elapsed_sec"))
        branch = str(engine.clock.get("scenario_branch") or self._default_branch)
        batch = engine.sensor_fusion.last_observation_batch
        tracks = engine.sensor_fusion.tracks
        new_records: list[dict[str, Any]] = []
        for plan in self._plans:
            plan = self._plan_with_capability(plan)
            media_id = str(plan.get("media_id") or "")
            if not media_id or media_id in self._captures:
                continue
            cue = self._media.get(media_id)
            if not cue:
                continue
            due_at = _number(plan.get("at_sec", cue.get("at_sec", 0)))
            if elapsed < due_at or not self._branch_ready(plan, branch):
                continue
            if not self._task_ready(plan, engine.tasks, elapsed, branch):
                continue
            product_type = _product_type(plan)
            platform = engine.assets.get(str(plan.get("platform_id") or ""))
            config = _sensor_config(plan)
            observation_ids: list[str] = []
            track_ids: list[str] = []
            product_data: dict[str, Any] | None = None
            if product_type == "sensor":
                evidence = self._sensor_evidence(
                    plan,
                    assets=engine.assets,
                    threats=engine.threats,
                    observation_batch=batch,
                    truth_associations=engine.sensor_fusion.truth_associations,
                    sensor_fusion=engine.sensor_fusion,
                    tracks=tracks,
                )
                if evidence is None:
                    continue
                config, observation_ids, track_ids = evidence
                source_kind = "sensor_observation"
                product_data = self._derived_product_data(
                    engine,
                    observation_ids,
                    track_ids,
                    plan,
                )
            elif product_type == "derived":
                evidence = self._derived_evidence(
                    plan,
                    assets=engine.assets,
                    observation_batch=batch,
                    truth_associations=engine.sensor_fusion.truth_associations,
                    observation_history=getattr(engine.sensor_fusion, "observation_history", []),
                    truth_association_history=getattr(
                        engine.sensor_fusion,
                        "truth_association_history",
                        [],
                    ),
                    tracks=tracks,
                )
                if evidence is None:
                    continue
                observation_ids, track_ids = evidence
                source_kind = "derived_current_state"
                product_data = self._derived_product_data(
                    engine,
                    observation_ids,
                    track_ids,
                    plan,
                )
            elif product_type == "command":
                if not self._command_ready(plan, engine.tasks, engine.scenario_story):
                    continue
                source_kind = "commander_workflow"
                product_data = self._derived_product_data(
                    engine,
                    observation_ids,
                    track_ids,
                    plan,
                )
            else:
                source_kind = "external_precollected"

            record = self._freeze_capture(
                plan,
                cue,
                elapsed=elapsed,
                tick_id=int(batch.get("tick_id", 0) or 0),
                run_id=str(engine.clock.get("run_id") or ""),
                platform=platform if product_type in {"sensor", "derived"} else None,
                config=config,
                observation_ids=observation_ids,
                track_ids=track_ids,
                source_kind=source_kind,
                product_data=product_data,
            )
            if str(record.get("mime_type") or "").casefold() == "image/svg+xml":
                from amos_platform.media.evidence_products import get_evidence_product_service

                frozen_state = {
                    "clock": deepcopy(engine.clock),
                    "assets": deepcopy((product_data or {}).get("assets") or []),
                    "fused_tracks": deepcopy((product_data or {}).get("tracks") or []),
                    "network": deepcopy((product_data or {}).get("network") or {}),
                    "tasks": deepcopy((product_data or {}).get("tasks") or []),
                    "observations": deepcopy((product_data or {}).get("observations") or []),
                    "alerts": [],
                }
                frozen_product = get_evidence_product_service().freeze_capture_record(
                    record,
                    frozen_state,
                )
                if frozen_product:
                    record["dynamic_uri"] = frozen_product["uri"]
                    record["dynamic_checksum"] = frozen_product["checksum"]["value"]
                    record["uri"] = frozen_product["uri"]
                    record["media_uri"] = frozen_product["uri"]
                    record["checksum"] = frozen_product["checksum"]["value"]
            self._captures[media_id] = record
            self._capture_order.append(media_id)
            new_records.append(deepcopy(record))
            self._link_tracks(tracks, record)

        engine.scenario_story["captures"] = self.public_captures()
        self.inject_into_observation_batch(batch)
        return new_records

    def _link_tracks(self, tracks: dict[str, Any], capture: dict[str, Any]) -> None:
        observation_ids = set(capture.get("observation_ids") or [])
        for track in tracks.values():
            refs = getattr(track, "source_refs", None)
            history = getattr(track, "history_path", None)
            if refs is None and isinstance(track, dict):
                refs = track.get("source_refs")
                history = track.get("history_path")
            linked = False
            for ref in refs or []:
                if isinstance(ref, dict) and str(ref.get("observation_id") or "") in observation_ids:
                    ref["media_id"] = capture["media_id"]
                    ref["capture_id"] = capture["capture_id"]
                    linked = True
            if linked:
                for point in reversed(history or []):
                    point_ids = {str(value) for value in point.get("source_observation_ids") or []}
                    if point_ids & observation_ids:
                        point.setdefault("source_media_ids", []).append(capture["media_id"])
                        point.setdefault("source_capture_ids", []).append(capture["capture_id"])
                        break

    def inject_into_observation_batch(self, batch: dict[str, Any]) -> None:
        refs = []
        links_by_observation: dict[str, list[dict[str, str]]] = {}
        for capture in self.public_captures():
            ref = {
                key: deepcopy(capture.get(key))
                for key in (
                    "capture_id", "media_id", "uri", "mime_type", "checksum",
                    "product_type", "captured_at_sim_time", "platform_id",
                    "sensor_instance_id", "observation_ids", "track_ids",
                    "platform_pose", "sensor_pose", "sensor_config", "capture_provenance",
                    "product_data",
                )
                if capture.get(key) is not None
            }
            refs.append(ref)
            for observation_id in capture.get("observation_ids") or []:
                links_by_observation.setdefault(str(observation_id), []).append({
                    "capture_id": capture["capture_id"],
                    "media_id": capture["media_id"],
                })
        batch["media_refs"] = refs
        for observation in batch.get("observations") or []:
            links = links_by_observation.get(str(observation.get("observation_id") or ""), [])
            if links:
                observation["capture_ids"] = [item["capture_id"] for item in links]
                observation["media_ids"] = [item["media_id"] for item in links]


__all__ = ["MediaCaptureRuntime", "PUBLIC_MEDIA_KEYS"]
