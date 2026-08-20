"""External Agent perception result models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from amos_platform.domain.policies.visibility import sanitize_agent_payload


@dataclass(slots=True)
class AgentClassification:
    label: str
    confidence: float | None = None
    source: str = "agent_inferred"

    def to_dict(self) -> dict[str, Any]:
        return sanitize_agent_payload({
            key: value for key, value in asdict(self).items() if value is not None
        })


@dataclass(slots=True)
class AgentDetection:
    detection_id: str
    position: dict[str, float] | None = None
    confidence: float | None = None
    classification: AgentClassification | dict[str, Any] | None = None
    source: str = "agent_inferred"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return sanitize_agent_payload({
            key: value for key, value in data.items() if value is not None
        })


@dataclass(slots=True)
class PerceptionResult:
    detections: list[dict[str, Any]] = field(default_factory=list)
    tracks: list[dict[str, Any]] = field(default_factory=list)
    summary: str = ""
    source: str = "agent"
    fallback: bool = False

    def to_dict(self) -> dict[str, Any]:
        return sanitize_agent_payload(asdict(self))


@dataclass(slots=True)
class PlatformPerceptionResult:
    detection_id: str
    source_observation_id: str = ""
    source_media_id: str = ""
    label: str = "UNKNOWN"
    confidence: float = 0.0
    bbox: dict[str, Any] | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    explanation: str = ""
    visibility: str = "agent-derived"

    def to_dict(self) -> dict[str, Any]:
        return sanitize_agent_payload({
            key: value for key, value in asdict(self).items() if value is not None
        })


@dataclass(slots=True)
class TruthAssociation:
    truth_id: str
    observation_id: str = ""
    media_id: str = ""
    track_id: str = ""
    association_type: str = "observation"
    confidence: float = 1.0
    visibility: str = "admin-only"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


__all__ = [
    "AgentClassification",
    "AgentDetection",
    "PerceptionResult",
    "PlatformPerceptionResult",
    "TruthAssociation",
]
