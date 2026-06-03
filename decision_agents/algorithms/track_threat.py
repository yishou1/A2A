"""Lightweight track generation and threat-ranking algorithms."""

from __future__ import annotations

import math
import os
import re

from collections import defaultdict
from datetime import datetime, timezone

from decision_agents.algorithms.registry import (
    AlgorithmSpec,
    UnknownAlgorithmError,
    missing_required_fields,
    select_algorithm,
)
from decision_agents.algorithms.onnx_adapter import OnnxAlgorithmSpec, run_onnx_or_fallback
from decision_agents.schemas import (
    AgentRequest,
    AgentResponse,
    Observation,
    RiskAssessment,
    Track,
)


ASSOCIATION_DISTANCE_THRESHOLD = 8.0
MONITORED_ORIGIN_DISTANCE = 20.0
HIGH_SPEED_THRESHOLD = 8.0
MEDIUM_SPEED_THRESHOLD = 3.5
NAUTICAL_MILES_PER_DEGREE = 60.0
AIR_HIGH_SPEED_THRESHOLD = 400.0
AIR_MEDIUM_SPEED_THRESHOLD = 180.0
SURFACE_HIGH_SPEED_THRESHOLD = 25.0
SURFACE_MEDIUM_SPEED_THRESHOLD = 10.0


def _small_track_threat(request: AgentRequest) -> dict:
    tracks = build_tracks(request.observations)
    risk_assessments = rank_threats(tracks, request.agent_profile.risk_policy)
    return {
        "tracks": [track.model_dump(mode="json") for track in tracks],
        "risk_assessments": [
            assessment.model_dump(mode="json") for assessment in risk_assessments
        ],
        "method": "spatiotemporal_association_linear_threat_score",
        "parameters": {
            "association_distance_threshold": ASSOCIATION_DISTANCE_THRESHOLD,
            "monitored_origin_distance": MONITORED_ORIGIN_DISTANCE,
            "high_speed_threshold": HIGH_SPEED_THRESHOLD,
            "medium_speed_threshold": MEDIUM_SPEED_THRESHOLD,
        },
    }


def _medium_track_threat(request: AgentRequest) -> dict:
    tracks = build_tracks_medium(request.observations)
    risk_assessments = rank_threats(tracks, request.agent_profile.risk_policy)
    return {
        "tracks": [track.model_dump(mode="json") for track in tracks],
        "risk_assessments": [
            assessment.model_dump(mode="json") for assessment in risk_assessments
        ],
        "method": "dynamic_spatiotemporal_gate_confidence_fusion",
        "parameters": {
            "base_association_distance_threshold": ASSOCIATION_DISTANCE_THRESHOLD,
            "monitored_origin_distance": MONITORED_ORIGIN_DISTANCE,
            "high_speed_threshold": HIGH_SPEED_THRESHOLD,
            "medium_speed_threshold": MEDIUM_SPEED_THRESHOLD,
        },
    }


def _large_track_threat(request: AgentRequest) -> dict:
    tracks, track_metadata = build_tracks_large(request.observations)
    risk_assessments = rank_threats_large(tracks, request.agent_profile.risk_policy)
    return {
        "tracks": [track.model_dump(mode="json") for track in tracks],
        "risk_assessments": [
            assessment.model_dump(mode="json") for assessment in risk_assessments
        ],
        "track_metadata": track_metadata,
        "method": "source_aware_dynamic_gate_motion_quality_fusion",
        "parameters": {
            "position_unit": "nautical_mile",
            "time_unit": "hour",
            "speed_unit": "knot",
            "air_high_speed_threshold": AIR_HIGH_SPEED_THRESHOLD,
            "air_medium_speed_threshold": AIR_MEDIUM_SPEED_THRESHOLD,
            "surface_high_speed_threshold": SURFACE_HIGH_SPEED_THRESHOLD,
            "surface_medium_speed_threshold": SURFACE_MEDIUM_SPEED_THRESHOLD,
        },
    }


def _onnx_track_threat(request: AgentRequest) -> dict:
    spec = OnnxAlgorithmSpec(
        model_path=os.getenv("TRACK_THREAT_ONNX_MODEL", "models/track_threat.onnx"),
        input_names=("input",),
        output_names=(),
        preprocess_fn=lambda item: {"input": item.model_dump_json()},
        postprocess_fn=lambda outputs, item: {
            "onnx_outputs": [
                output.tolist() if hasattr(output, "tolist") else output
                for output in outputs
            ],
            "method": "onnx_track_threat",
        },
        fallback_algorithm_id="track_threat_large",
        fallback_run_fn=_large_track_threat,
        metadata={"category": "track_threat"},
    )
    return run_onnx_or_fallback(request, spec)


ALGORITHMS = [
    AlgorithmSpec(
        algorithm_id="track_threat_small",
        category="track_threat",
        parameter_size="small",
        required_fields=("observations",),
        run_fn=_small_track_threat,
    ),
    AlgorithmSpec(
        algorithm_id="track_threat_medium",
        category="track_threat",
        parameter_size="medium",
        required_fields=("observations",),
        run_fn=_medium_track_threat,
    ),
    AlgorithmSpec(
        algorithm_id="track_threat_large",
        category="track_threat",
        parameter_size="large",
        required_fields=("observations",),
        run_fn=_large_track_threat,
    ),
    AlgorithmSpec(
        algorithm_id="track_threat_onnx",
        category="track_threat",
        parameter_size="large",
        required_fields=("observations",),
        run_fn=_onnx_track_threat,
    ),
]


def run_track_threat(request: AgentRequest) -> AgentResponse:
    try:
        algorithm = select_algorithm(request, ALGORITHMS)
    except UnknownAlgorithmError as exc:
        return AgentResponse(
            status="error",
            agent="track_threat_agent",
            result={"available_algorithms": exc.available_algorithms},
            summary=str(exc),
            warnings=[f"unknown_algorithm:{exc.algorithm_id}"],
        )
    missing = missing_required_fields(request, algorithm.required_fields)
    if missing:
        return AgentResponse(
            status="input_required",
            agent="track_threat_agent",
            selected_algorithms=[algorithm.algorithm_id],
            summary="Missing required fields for track and threat analysis.",
            warnings=[f"missing:{field}" for field in missing],
        )
    result = algorithm.run_fn(request)
    track_count = len(result.get("tracks", []))
    risk_count = len(result.get("risk_assessments", []))
    selected_algorithms = [algorithm.algorithm_id]
    warnings = []
    onnx_info = result.get("onnx", {})
    if onnx_info.get("fallback"):
        fallback_algorithm_id = onnx_info.get("fallback_algorithm_id")
        if fallback_algorithm_id:
            selected_algorithms.append(fallback_algorithm_id)
        warnings.append(f"onnx_fallback:{onnx_info.get('reason', 'unavailable')}")
    return AgentResponse(
        agent="track_threat_agent",
        selected_algorithms=selected_algorithms,
        result=result,
        summary=(
            f"Generated {track_count} track(s) and {risk_count} threat ranking "
            "record(s) with deterministic lightweight algorithms."
        ),
        warnings=warnings,
    )


def build_tracks(observations: list[Observation]) -> list[Track]:
    observations = _normalize_observations(observations)
    grouped = _group_observations(observations)
    tracks = []
    for index, group in enumerate(grouped, start=1):
        ordered = sorted(group, key=lambda item: _timestamp_value(item.timestamp))
        track_id = _track_id(index, ordered)
        object_type = _dominant_object_type(ordered)
        confidence = sum(item.confidence for item in ordered) / len(ordered)
        last = ordered[-1]
        velocity_x, velocity_y, speed, heading = _motion(ordered)
        tracks.append(
            Track(
                id=track_id,
                source_observations=[item.id for item in ordered],
                object_type=object_type,
                start_time=ordered[0].timestamp,
                end_time=last.timestamp,
                last_position=_last_position(last),
                velocity=_velocity(velocity_x, velocity_y, speed, heading),
                speed=speed,
                heading=heading,
                trend=_trend(speed, heading, len(ordered)),
                confidence=round(confidence, 3),
            )
        )
    return tracks


def build_tracks_medium(observations: list[Observation]) -> list[Track]:
    observations = _normalize_observations(observations)
    groups: list[list[Observation]] = []
    for observation in sorted(observations, key=lambda item: _timestamp_value(item.timestamp)):
        group = _best_medium_group(observation, groups)
        if group is None:
            groups.append([observation])
        else:
            group.append(observation)

    tracks = []
    for index, group in enumerate(groups, start=1):
        ordered = sorted(group, key=lambda item: _timestamp_value(item.timestamp))
        track_id = _track_id(index, ordered)
        object_type = _dominant_object_type(ordered)
        last = ordered[-1]
        velocity_x, velocity_y, speed, heading = _motion(ordered)
        tracks.append(
            Track(
                id=track_id,
                source_observations=[item.id for item in ordered],
                object_type=object_type,
                start_time=ordered[0].timestamp,
                end_time=last.timestamp,
                last_position=_last_position(last),
                velocity=_velocity(velocity_x, velocity_y, speed, heading),
                speed=speed,
                heading=heading,
                trend=_trend(speed, heading, len(ordered)),
                confidence=_fused_confidence(ordered),
            )
        )
    return tracks


def build_tracks_large(observations: list[Observation]) -> tuple[list[Track], dict[str, dict]]:
    observations = _normalize_observations(observations)
    groups: list[list[Observation]] = []
    for observation in sorted(observations, key=lambda item: _timestamp_value(item.timestamp)):
        group = _best_large_group(observation, groups)
        if group is None:
            groups.append([observation])
        else:
            group.append(observation)

    tracks = []
    metadata = {}
    for index, group in enumerate(groups, start=1):
        ordered = sorted(group, key=lambda item: _timestamp_value(item.timestamp))
        track_id = _track_id(index, ordered)
        object_type = _dominant_object_type(ordered)
        last = ordered[-1]
        velocity_x, velocity_y, speed, heading = _motion(ordered, source_aware=True)
        confidence, confidence_factors = _large_fused_confidence(ordered)
        tracks.append(
            Track(
                id=track_id,
                source_observations=[item.id for item in ordered],
                object_type=object_type,
                start_time=ordered[0].timestamp,
                end_time=last.timestamp,
                last_position=_last_position(last),
                velocity=_velocity(velocity_x, velocity_y, speed, heading),
                speed=speed,
                heading=heading,
                trend=_trend(speed, heading, len(ordered)),
                confidence=confidence,
            )
        )
        metadata[track_id] = _track_metadata(ordered, confidence_factors)
    return tracks, metadata


def rank_threats(tracks: list[Track], risk_policy: str) -> list[RiskAssessment]:
    scored = []
    for track in tracks:
        score, probability, rules, rationale = _threat_score(track, risk_policy)
        scored.append((score, track, probability, rules, rationale))

    scored.sort(key=lambda item: (-item[0], item[1].id))
    assessments = []
    for priority, (score, track, probability, rules, rationale) in enumerate(
        scored, start=1
    ):
        assessments.append(
            RiskAssessment(
                track_id=track.id,
                priority=priority,
                risk=_risk_level(score),
                threat_score=score,
                probability=probability,
                rationale=rationale,
                triggered_rules=rules,
            )
        )
    return assessments


def rank_threats_large(tracks: list[Track], risk_policy: str) -> list[RiskAssessment]:
    scored = []
    for track in tracks:
        score, probability, rules, rationale = _threat_score_large(track, risk_policy)
        scored.append((score, track, probability, rules, rationale))

    scored.sort(key=lambda item: (-item[0], item[1].id))
    assessments = []
    for priority, (score, track, probability, rules, rationale) in enumerate(
        scored, start=1
    ):
        assessments.append(
            RiskAssessment(
                track_id=track.id,
                priority=priority,
                risk=_risk_level(score),
                threat_score=score,
                probability=probability,
                rationale=rationale,
                triggered_rules=rules,
            )
        )
    return assessments


def _best_medium_group(
    observation: Observation,
    groups: list[list[Observation]],
) -> list[Observation] | None:
    best_group = None
    best_score = float("inf")
    for group in groups:
        if observation.target_hint and group[0].target_hint == observation.target_hint:
            return group
        predicted_x, predicted_y, gate = _predicted_gate(group, observation)
        distance = math.dist((_x(observation), _y(observation)), (predicted_x, predicted_y))
        if distance <= gate and distance < best_score:
            best_score = distance
            best_group = group
    return best_group


def _best_large_group(
    observation: Observation,
    groups: list[list[Observation]],
) -> list[Observation] | None:
    best_group = None
    best_score = float("inf")
    for group in groups:
        if observation.target_hint and group[0].target_hint == observation.target_hint:
            return group
        predicted_x, predicted_y, gate = _source_aware_predicted_gate(group, observation)
        distance = math.dist((_x(observation), _y(observation)), (predicted_x, predicted_y))
        heading_penalty = _heading_penalty(group, observation)
        reliability_penalty = (1.0 - observation.source_reliability) * 0.2
        score = (distance / max(gate, 0.001)) + heading_penalty + reliability_penalty
        if distance <= gate and score < best_score:
            best_score = score
            best_group = group
    return best_group


def _predicted_gate(
    group: list[Observation],
    observation: Observation,
) -> tuple[float, float, float]:
    ordered = sorted(group, key=lambda item: _timestamp_value(item.timestamp))
    last = ordered[-1]
    if len(ordered) < 2:
        dt = max(_timestamp_value(observation.timestamp) - _timestamp_value(last.timestamp), 0.0)
        speed = last.speed_hint or 0.0
        heading = last.heading_hint or 0.0
        predicted_x = _x(last) + speed * math.cos(math.radians(heading)) * dt
        predicted_y = _y(last) + speed * math.sin(math.radians(heading)) * dt
    else:
        previous = ordered[-2]
        base_dt = max(_timestamp_value(last.timestamp) - _timestamp_value(previous.timestamp), 1e-6)
        vx = (_x(last) - _x(previous)) / base_dt
        vy = (_y(last) - _y(previous)) / base_dt
        dt = max(_timestamp_value(observation.timestamp) - _timestamp_value(last.timestamp), 0.0)
        speed = math.hypot(vx, vy)
        predicted_x = _x(last) + vx * dt
        predicted_y = _y(last) + vy * dt

    confidence = max(min(last.confidence, 1.0), 0.0)
    uncertainty = (1.0 - confidence) * 4.0
    gate = ASSOCIATION_DISTANCE_THRESHOLD + speed * 0.5 + uncertainty
    return predicted_x, predicted_y, gate


def _source_aware_predicted_gate(
    group: list[Observation],
    observation: Observation,
) -> tuple[float, float, float]:
    predicted_x, predicted_y, gate = _predicted_gate(group, observation)
    ordered = sorted(group, key=lambda item: _timestamp_value(item.timestamp))
    last = ordered[-1]
    dt = max(_timestamp_value(observation.timestamp) - _timestamp_value(last.timestamp), 0.0)
    source_format = observation.source_format or last.source_format or ""
    object_type = observation.object_type or last.object_type
    base_gate = 20.0 if object_type == "air_track" or "adsb" in source_format else 5.0
    hinted_speed = observation.speed_hint or last.speed_hint or 0.0
    confidence = max(min((observation.confidence + last.confidence) / 2.0, 1.0), 0.0)
    reliability = max(min((observation.source_reliability + last.source_reliability) / 2.0, 1.0), 0.0)
    uncertainty = (1.0 - confidence * reliability) * 6.0
    source_gate = base_gate + hinted_speed * max(dt, 0.05) * 0.2 + uncertainty
    return predicted_x, predicted_y, max(gate, source_gate)


def _fused_confidence(observations: list[Observation]) -> float:
    miss_probability = 1.0
    sensors = set()
    for observation in observations:
        reliability = float(
            observation.features.get("source_reliability", observation.source_reliability)
        )
        sensor_id = observation.features.get("sensor_id", observation.sensor_id)
        if sensor_id:
            sensors.add(str(sensor_id))
        effective_confidence = max(0.0, min(observation.confidence * reliability, 0.99))
        miss_probability *= 1.0 - effective_confidence
    fused = 1.0 - miss_probability
    diversity_bonus = min(len(sensors), 3) * 0.02
    return round(min(fused + diversity_bonus, 1.0), 3)


def _large_fused_confidence(observations: list[Observation]) -> tuple[float, dict[str, float]]:
    miss_probability = 1.0
    sensors = set()
    source_formats = set()
    reliability_sum = 0.0
    for observation in observations:
        reliability = float(
            observation.features.get("source_reliability", observation.source_reliability)
        )
        reliability_sum += reliability
        sensor_id = observation.features.get("sensor_id", observation.sensor_id)
        if sensor_id:
            sensors.add(str(sensor_id))
        if observation.source_format:
            source_formats.add(observation.source_format)
        effective_confidence = max(0.0, min(observation.confidence * reliability, 0.99))
        miss_probability *= 1.0 - effective_confidence

    noisy_or = 1.0 - miss_probability
    sensor_bonus = min(len(sensors), 4) * 0.025
    source_bonus = min(len(source_formats), 3) * 0.015
    consistency = _motion_consistency(observations)
    reliability_avg = reliability_sum / max(len(observations), 1)
    fused = (noisy_or + sensor_bonus + source_bonus) * consistency
    confidence = round(max(0.0, min(fused, 1.0)), 3)
    return confidence, {
        "noisy_or": round(noisy_or, 3),
        "sensor_bonus": round(sensor_bonus, 3),
        "source_bonus": round(source_bonus, 3),
        "motion_consistency": round(consistency, 3),
        "average_source_reliability": round(reliability_avg, 3),
    }


def _motion_consistency(observations: list[Observation]) -> float:
    if len(observations) < 2:
        return 1.0
    _, _, speed, heading = _motion(observations)
    speed_hints = [item.speed_hint for item in observations if item.speed_hint is not None]
    heading_hints = [item.heading_hint for item in observations if item.heading_hint is not None]
    speed_score = 1.0
    heading_score = 1.0
    if speed_hints:
        hinted_speed = sum(speed_hints) / len(speed_hints)
        speed_delta = abs(speed - hinted_speed) / max(hinted_speed, 1.0)
        speed_score = max(0.65, 1.0 - min(speed_delta, 1.0) * 0.25)
    if heading_hints:
        hinted_heading = _mean_heading(heading_hints)
        heading_delta = _angular_difference(heading, hinted_heading)
        heading_score = max(0.7, 1.0 - min(heading_delta / 180.0, 1.0) * 0.2)
    return round(speed_score * heading_score, 3)


def _group_observations(observations: list[Observation]) -> list[list[Observation]]:
    hinted: dict[str, list[Observation]] = defaultdict(list)
    unhinted = []
    for observation in observations:
        if observation.target_hint:
            hinted[observation.target_hint].append(observation)
        else:
            unhinted.append(observation)

    groups = list(hinted.values())
    for observation in sorted(unhinted, key=lambda item: _timestamp_value(item.timestamp)):
        group = _nearest_group(observation, groups)
        if group is None:
            groups.append([observation])
        else:
            group.append(observation)
    return groups


def _nearest_group(
    observation: Observation,
    groups: list[list[Observation]],
) -> list[Observation] | None:
    best_group = None
    best_distance = float("inf")
    for group in groups:
        last = sorted(group, key=lambda item: _timestamp_value(item.timestamp))[-1]
        distance = math.dist((_x(observation), _y(observation)), (_x(last), _y(last)))
        if distance < best_distance:
            best_distance = distance
            best_group = group
    if best_distance <= ASSOCIATION_DISTANCE_THRESHOLD:
        return best_group
    return None


def _track_id(index: int, observations: list[Observation]) -> str:
    hint = observations[0].target_hint
    if hint:
        safe_hint = re.sub(r"[^A-Za-z0-9_-]+", "-", hint).strip("-").upper()
        return f"TRK-{safe_hint}"
    return f"TRK-{index:03d}"


def _motion(
    observations: list[Observation],
    source_aware: bool = False,
) -> tuple[float, float, float, float]:
    if len(observations) < 2:
        item = observations[-1]
        speed = item.speed_hint or 0.0
        heading = item.heading_hint or 0.0
        vx = speed * math.cos(math.radians(heading))
        vy = speed * math.sin(math.radians(heading))
        return round(vx, 3), round(vy, 3), round(speed, 3), round(heading, 1)

    first = observations[0]
    last = observations[-1]
    delta_t = max(_timestamp_value(last.timestamp) - _timestamp_value(first.timestamp), 1e-6)
    vx = (_x(last) - _x(first)) / delta_t
    vy = (_y(last) - _y(first)) / delta_t
    speed = math.hypot(vx, vy)
    heading = (math.degrees(math.atan2(vy, vx)) + 360.0) % 360.0
    if source_aware:
        speed_hints = [item.speed_hint for item in observations if item.speed_hint is not None]
        heading_hints = [item.heading_hint for item in observations if item.heading_hint is not None]
        if speed_hints:
            hinted_speed = sum(speed_hints) / len(speed_hints)
            speed = (speed * 0.6) + (hinted_speed * 0.4)
        if heading_hints:
            hinted_heading = _mean_heading(heading_hints)
            heading = _blend_heading(heading, hinted_heading, 0.4)
        vx = speed * math.cos(math.radians(heading))
        vy = speed * math.sin(math.radians(heading))
    return round(vx, 3), round(vy, 3), round(speed, 3), round(heading, 1)


def _threat_score(track: Track, risk_policy: str) -> tuple[float, float, list[str], str]:
    rules = []
    speed_component = min(track.speed / HIGH_SPEED_THRESHOLD, 1.0) * 35.0
    confidence_component = track.confidence * 25.0
    proximity = math.dist(
        (
            track.last_position.get("x", 0.0),
            track.last_position.get("y", 0.0),
        ),
        (0.0, 0.0),
    )
    proximity_component = max(0.0, (MONITORED_ORIGIN_DISTANCE - proximity)) / (
        MONITORED_ORIGIN_DISTANCE
    ) * 25.0
    type_component = 0.0

    if track.object_type in {"air_track", "missile", "fast_boat"}:
        type_component += 10.0
        rules.append(f"type_watch:{track.object_type}")
    if track.speed >= HIGH_SPEED_THRESHOLD:
        rules.append("high_speed")
    elif track.speed >= MEDIUM_SPEED_THRESHOLD:
        rules.append("medium_speed")
    if proximity <= MONITORED_ORIGIN_DISTANCE:
        rules.append("inside_monitored_radius")

    policy_component = 5.0 if risk_policy == "conservative" else 0.0
    raw_score = (
        speed_component
        + confidence_component
        + proximity_component
        + type_component
        + policy_component
    )
    score = round(max(0.0, min(raw_score, 100.0)), 2)
    probability = round(1.0 / (1.0 + math.exp(-(score - 50.0) / 12.0)), 3)
    rationale = (
        f"score={score}, speed={track.speed}, confidence={track.confidence}, "
        f"distance_to_origin={round(proximity, 2)}, rules={rules or ['none']}"
    )
    return score, probability, rules, rationale


def _threat_score_large(track: Track, risk_policy: str) -> tuple[float, float, list[str], str]:
    rules = []
    medium_threshold, high_threshold = _speed_thresholds_for_track(track)
    speed_component = min(track.speed / high_threshold, 1.0) * 35.0
    confidence_component = track.confidence * 25.0
    proximity = math.dist(
        (
            track.last_position.get("x", 0.0),
            track.last_position.get("y", 0.0),
        ),
        (0.0, 0.0),
    )
    proximity_component = max(0.0, (MONITORED_ORIGIN_DISTANCE - proximity)) / (
        MONITORED_ORIGIN_DISTANCE
    ) * 20.0
    type_component = 0.0

    if track.object_type == "air_track":
        type_component += 8.0
        rules.append("type_watch:air_track")
    elif track.object_type in {"fast_boat", "tanker", "cargo"}:
        type_component += 5.0
        rules.append(f"type_watch:{track.object_type}")
    if track.speed >= high_threshold:
        rules.append("high_speed")
    elif track.speed >= medium_threshold:
        rules.append("medium_speed")
    if proximity <= MONITORED_ORIGIN_DISTANCE:
        rules.append("inside_monitored_radius")

    policy_component = 5.0 if risk_policy == "conservative" else 0.0
    raw_score = (
        speed_component
        + confidence_component
        + proximity_component
        + type_component
        + policy_component
    )
    score = round(max(0.0, min(raw_score, 100.0)), 2)
    probability = round(1.0 / (1.0 + math.exp(-(score - 50.0) / 12.0)), 3)
    rationale = (
        f"score={score}, speed_knots={track.speed}, confidence={track.confidence}, "
        f"thresholds=({medium_threshold}, {high_threshold}), "
        f"distance_to_origin={round(proximity, 2)}, rules={rules or ['none']}"
    )
    return score, probability, rules, rationale


def _speed_thresholds_for_track(track: Track) -> tuple[float, float]:
    if track.object_type == "air_track":
        return AIR_MEDIUM_SPEED_THRESHOLD, AIR_HIGH_SPEED_THRESHOLD
    return SURFACE_MEDIUM_SPEED_THRESHOLD, SURFACE_HIGH_SPEED_THRESHOLD


def _risk_level(score: float) -> str:
    if score >= 70.0:
        return "high"
    if score >= 40.0:
        return "medium"
    return "low"


def _trend(speed: float, heading: float, observation_count: int) -> str:
    if observation_count < 2 and speed <= 0.0:
        return "insufficient observations"
    direction = _heading_direction(heading)
    if speed >= HIGH_SPEED_THRESHOLD:
        return f"fast movement toward {direction}"
    if speed >= MEDIUM_SPEED_THRESHOLD:
        return f"steady movement toward {direction}"
    if speed > 0.0:
        return f"slow movement toward {direction}"
    return "stationary or unresolved"


def _heading_direction(heading: float) -> str:
    directions = [
        "east",
        "northeast",
        "north",
        "northwest",
        "west",
        "southwest",
        "south",
        "southeast",
    ]
    index = int(((heading + 22.5) % 360.0) / 45.0)
    return directions[index]


def _dominant_object_type(observations: list[Observation]) -> str:
    counts: dict[str, int] = defaultdict(int)
    for observation in observations:
        counts[observation.object_type] += 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


def _timestamp_value(timestamp: str) -> float:
    parsed = _parse_timestamp(timestamp)
    if parsed is not None:
        value, unit = parsed
        return value / 3600.0 if unit == "seconds" else value
    match = re.search(r"([+-]?\d+(?:\.\d+)?)", timestamp)
    if match:
        return float(match.group(1))
    return 0.0


def _normalize_observations(observations: list[Observation]) -> list[Observation]:
    if not observations:
        return []
    origin = _normalization_origin(observations)
    normalized = []
    for observation in observations:
        updates = {}
        if observation.x is None or observation.y is None:
            if (
                origin["latitude"] is not None
                and origin["longitude"] is not None
                and observation.latitude is not None
                and observation.longitude is not None
            ):
                x, y = _project_to_nautical_miles(
                    observation.latitude,
                    observation.longitude,
                    origin["latitude"],
                    origin["longitude"],
                )
                updates["x"] = round(x, 4)
                updates["y"] = round(y, 4)
            else:
                updates["x"] = observation.x or 0.0
                updates["y"] = observation.y or 0.0
        parsed = _parse_timestamp(observation.timestamp)
        if parsed is not None:
            value, unit = parsed
            if unit == "seconds":
                first_second = origin["time_seconds"]
                if first_second is None:
                    normalized_hours = 0.0
                else:
                    normalized_hours = (value - first_second) / 3600.0
            else:
                normalized_hours = value
            updates["timestamp"] = f"T+{normalized_hours:.4f}"
            features = dict(observation.features)
            features.setdefault("original_timestamp", observation.timestamp)
            features["normalized_time_hours"] = round(normalized_hours, 6)
            updates["features"] = features
        normalized.append(observation.model_copy(update=updates))
    return normalized


def _normalization_origin(observations: list[Observation]) -> dict[str, float | None]:
    latitude = None
    longitude = None
    time_seconds = None
    for observation in observations:
        if latitude is None and observation.latitude is not None and observation.longitude is not None:
            latitude = observation.latitude
            longitude = observation.longitude
        parsed = _parse_timestamp(observation.timestamp)
        if time_seconds is None and parsed is not None and parsed[1] == "seconds":
            time_seconds = parsed[0]
        if latitude is not None and time_seconds is not None:
            break
    return {
        "latitude": latitude,
        "longitude": longitude,
        "time_seconds": time_seconds,
    }


def _parse_timestamp(timestamp: str) -> tuple[float, str] | None:
    text = str(timestamp).strip()
    match = re.fullmatch(r"T\s*([+-])?\s*(\d+(?:\.\d+)?)(?::(\d+(?:\.\d+)?))?", text)
    if match:
        sign = -1.0 if match.group(1) == "-" else 1.0
        hours = float(match.group(2))
        minutes = float(match.group(3) or 0.0)
        return sign * (hours + minutes / 60.0), "hours"
    try:
        number = float(text)
    except ValueError:
        number = None
    if number is not None:
        if abs(number) > 100000.0:
            return number, "seconds"
        return number, "hours"

    candidate = text.replace("Z", "+00:00")
    formats = (
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
    )
    for fmt in formats:
        try:
            dt = datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
            return dt.timestamp(), "seconds"
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp(), "seconds"


def _project_to_nautical_miles(
    latitude: float,
    longitude: float,
    origin_lat: float,
    origin_lon: float,
) -> tuple[float, float]:
    mean_lat = math.radians((latitude + origin_lat) / 2.0)
    x = (longitude - origin_lon) * math.cos(mean_lat) * NAUTICAL_MILES_PER_DEGREE
    y = (latitude - origin_lat) * NAUTICAL_MILES_PER_DEGREE
    return x, y


def _x(observation: Observation) -> float:
    return float(observation.x or 0.0)


def _y(observation: Observation) -> float:
    return float(observation.y or 0.0)


def _last_position(observation: Observation) -> dict[str, float]:
    position = {
        "x": round(_x(observation), 4),
        "y": round(_y(observation), 4),
    }
    if observation.latitude is not None:
        position["latitude"] = observation.latitude
    if observation.longitude is not None:
        position["longitude"] = observation.longitude
    if observation.altitude is not None:
        position["altitude"] = observation.altitude
    return position


def _velocity(
    velocity_x: float,
    velocity_y: float,
    speed: float,
    heading: float,
) -> dict[str, float]:
    return {
        "vx": velocity_x,
        "vy": velocity_y,
        "vx_nm_per_hour": velocity_x,
        "vy_nm_per_hour": velocity_y,
        "speed_knots": speed,
        "heading_deg": heading,
    }


def _heading_penalty(group: list[Observation], observation: Observation) -> float:
    heading = observation.heading_hint
    group_headings = [item.heading_hint for item in group if item.heading_hint is not None]
    if heading is None or not group_headings:
        return 0.0
    return _angular_difference(heading, _mean_heading(group_headings)) / 180.0 * 0.35


def _mean_heading(headings: list[float]) -> float:
    sin_sum = sum(math.sin(math.radians(heading)) for heading in headings)
    cos_sum = sum(math.cos(math.radians(heading)) for heading in headings)
    if sin_sum == 0.0 and cos_sum == 0.0:
        return 0.0
    return (math.degrees(math.atan2(sin_sum, cos_sum)) + 360.0) % 360.0


def _blend_heading(base_heading: float, hinted_heading: float, hint_weight: float) -> float:
    base_weight = 1.0 - hint_weight
    sin_sum = (
        math.sin(math.radians(base_heading)) * base_weight
        + math.sin(math.radians(hinted_heading)) * hint_weight
    )
    cos_sum = (
        math.cos(math.radians(base_heading)) * base_weight
        + math.cos(math.radians(hinted_heading)) * hint_weight
    )
    return (math.degrees(math.atan2(sin_sum, cos_sum)) + 360.0) % 360.0


def _angular_difference(first: float, second: float) -> float:
    return abs((first - second + 180.0) % 360.0 - 180.0)


def _track_metadata(
    observations: list[Observation],
    confidence_factors: dict[str, float],
) -> dict:
    timestamps = [_timestamp_value(item.timestamp) for item in observations]
    source_formats = sorted({item.source_format for item in observations if item.source_format})
    sensors = sorted(
        {
            str(item.features.get("sensor_id", item.sensor_id))
            for item in observations
            if item.features.get("sensor_id", item.sensor_id)
        }
    )
    return {
        "source_formats": source_formats,
        "sensor_count": len(sensors),
        "sensors": sensors,
        "time_span_hours": round(max(timestamps) - min(timestamps), 4) if timestamps else 0.0,
        "position_unit": "nautical_mile",
        "speed_unit": "knot",
        "confidence_factors": confidence_factors,
    }
