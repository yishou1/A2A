"""Agent-visible observation domain models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from amos_platform.domain.policies.visibility import sanitize_agent_payload


@dataclass(slots=True)
class SensorObservation:
    observation_id: str
    sensor_id: str
    asset_id: str
    sim_time: float
    modality: str
    bearing_deg: float
    range_nm: float
    slant_range_nm: float | None = None
    elevation_deg: float | None = None
    domain_hint: str | None = None
    range_rate_kts: float = 0.0
    snr_db: float = 0.0
    covariance: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.5
    quality: str = "nominal"
    source: str = "sensor_simulator"
    visibility: str = "agent-visible:observation"

    def to_dict(self) -> dict[str, Any]:
        return sanitize_agent_payload({
            key: value for key, value in asdict(self).items() if value is not None
        })


@dataclass(slots=True)
class ObservationBatch:
    observations: list[dict[str, Any]]
    batch_id: str = ""
    trace_id: str = ""
    run_id: str = ""
    scenario_id: str = ""
    tick_id: int = 0
    sim_time: float = 0.0
    observer_scope: dict[str, Any] = field(default_factory=dict)
    own_asset_poses: list[dict[str, Any]] = field(default_factory=list)
    media_refs: list[dict[str, Any]] = field(default_factory=list)
    visibility: str = "agent-visible"

    def to_dict(self) -> dict[str, Any]:
        return sanitize_agent_payload(asdict(self))


@dataclass(slots=True)
class RadarObservation:
    observation_id: str
    position: dict[str, float]
    confidence: float | None = None
    sensor_sources: list[str] = field(default_factory=list)
    observation_type: str = "radar_detection"
    visibility: str = "agent-visible"

    def to_dict(self) -> dict[str, Any]:
        return sanitize_agent_payload({
            key: value for key, value in asdict(self).items() if value is not None
        })


@dataclass(slots=True)
class BearingObservation:
    observation_id: str
    bearing_deg: float
    sensor_id: str
    confidence: float | None = None
    observation_type: str = "bearing"
    visibility: str = "agent-visible"

    def to_dict(self) -> dict[str, Any]:
        return sanitize_agent_payload({
            key: value for key, value in asdict(self).items() if value is not None
        })


@dataclass(slots=True)
class ImageFrameObservation:
    observation_id: str
    media_id: str
    frame_id: str
    sensor_id: str
    observation_type: str = "image_frame"
    visibility: str = "agent-visible"

    def to_dict(self) -> dict[str, Any]:
        return sanitize_agent_payload(asdict(self))


@dataclass(slots=True)
class VideoClipObservation:
    observation_id: str
    media_id: str
    clip_id: str
    sensor_id: str
    duration_sec: float | None = None
    observation_type: str = "video_clip"
    visibility: str = "agent-visible"

    def to_dict(self) -> dict[str, Any]:
        return sanitize_agent_payload({
            key: value for key, value in asdict(self).items() if value is not None
        })


ObservationPayload = dict[str, Any]

__all__ = [
    "BearingObservation",
    "ImageFrameObservation",
    "ObservationBatch",
    "ObservationPayload",
    "RadarObservation",
    "SensorObservation",
    "VideoClipObservation",
]
