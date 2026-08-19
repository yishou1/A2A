"""信息共享与通信技能：知识驱动语义通信 + MARL 动态路由。"""

from __future__ import annotations

from typing import Any

from agent.algorithm_library.factory import create_marl_router, create_semantic_comm
from agent.algorithm_library.planner_runtime import AlgorithmPlan
from agent.models.schemas import CognitionOutput, PerceptionOutput, SemanticIntelligencePacket
from agent.track_packet import CONSUMER_GUIDE, PACKET_SCHEMA_VERSION


def _algorithm_id(backend: Any) -> str:
    return str(getattr(backend, "algorithm_id", None) or getattr(backend, "name", "algorithm"))


def _planned_inputs(base: dict[str, Any], plan: AlgorithmPlan | None, algorithm_id: str) -> dict[str, Any]:
    if plan is None:
        return base
    enriched = dict(base)
    enriched["params"] = plan.params_for(algorithm_id)
    for call in plan.algorithm_calls:
        if call.algorithm_id == algorithm_id:
            enriched["_algorithm_reason"] = call.reason
            break
    return enriched


def _run_and_record(backend: Any, inputs: dict[str, Any]) -> tuple[Any, dict[str, Any] | None]:
    if hasattr(backend, "last_invocation"):
        backend.last_invocation = None
    output = backend.run(inputs)
    invocation = getattr(backend, "last_invocation", None)
    if isinstance(invocation, dict):
        return output, dict(invocation)
    return output, None


class CommunicationSkill:
    def __init__(self, *, use_mock: bool = False, config: dict[str, Any] | None = None):
        cfg = config or {}
        self.compression = create_semantic_comm(use_mock=use_mock, config=cfg)
        self.router = create_marl_router(use_mock=use_mock, config=cfg)

    @staticmethod
    def _targets_from_perception(perception: PerceptionOutput) -> list[dict[str, Any]]:
        targets: list[dict[str, Any]] = []
        for det in perception.detections or []:
            geo = det.geo.model_dump() if hasattr(det.geo, "model_dump") else det.geo
            targets.append(
                {
                    "track_id": det.track_id,
                    "class": det.class_name,
                    "confidence": det.confidence,
                    "geo": geo,
                    "damage_score": det.damage_score,
                }
            )
        return targets

    @staticmethod
    def _enrich_targets_from_cognition(
        targets: list[dict[str, Any]],
        cognition: CognitionOutput,
    ) -> list[dict[str, Any]]:
        classifications = {
            str(item.get("target_id")): item
            for item in getattr(cognition, "classifications", [])
            if isinstance(item, dict) and item.get("target_id")
        }
        threats = {
            str(getattr(item, "target_id", "")): item
            for item in getattr(cognition, "threats", [])
            if getattr(item, "target_id", None)
        }
        for target in targets:
            track_id = str(target.get("track_id") or "")
            classification = classifications.get(track_id)
            if classification:
                target["class"] = classification.get("object_class") or target.get("class")
                target["label"] = classification.get("label")
                target["affiliation"] = classification.get("affiliation")
            threat = threats.get(track_id)
            if threat is not None:
                target["threat_level"] = threat.threat_level
        return targets

    def execute(
        self,
        mission_id: str,
        perception: PerceptionOutput,
        cognition: CognitionOutput,
        *,
        subscriber_agents: list[str] | None = None,
        jamming_level: float = 0.0,
        plan: AlgorithmPlan | None = None,
    ) -> SemanticIntelligencePacket:
        def enabled(aid: str) -> bool:
            return plan is None or plan.is_enabled(aid)

        p_dump = perception.model_dump(exclude={"algorithm_calls", "algorithm_invocations"})
        c_dump = cognition.model_dump(exclude={"algorithm_calls", "algorithm_invocations"})
        trace: dict[str, str] = {}
        invocations: list[dict[str, Any]] = [
            *list(perception.algorithm_invocations or []),
            *list(cognition.algorithm_invocations or []),
        ]

        if enabled("knowledge_semantic_comm"):
            compressed, invocation = _run_and_record(
                self.compression,
                _planned_inputs(
                    {"perception": p_dump, "cognition": c_dump},
                    plan,
                    _algorithm_id(self.compression),
                ),
            )
            if invocation:
                invocations.append(invocation)
            if not isinstance(compressed, dict):
                compressed = {}
            compressed.setdefault("targets", self._targets_from_perception(perception))
            compressed.setdefault("target_source", "knowledge_semantic_comm")
            compressed.setdefault("target_source_label", "model_output")
            compressed.setdefault("target_source_detail", "semantic_compression")
            trace[self.compression.name] = f"ratio={compressed.get('compression_ratio', 1)}"
        else:
            targets = self._targets_from_perception(perception)
            compressed = {
                "summary": f"tracks={len(perception.tracks)}; targets={len(targets)}; source=perception_detections",
                "targets": targets,
                "semantic_vector": [],
                "knowledge_graph": {},
                "compression_ratio": 1.0,
                "target_source": "perception_detections",
                "target_source_label": "passthrough",
                "target_source_detail": "communication_compression_skipped",
            }
            trace[self.compression.name] = "skipped->perception_passthrough"

        compressed["targets"] = self._enrich_targets_from_cognition(
            list(compressed.get("targets") or []),
            cognition,
        )

        if enabled("marl_dynamic_router"):
            routing, invocation = _run_and_record(
                self.router,
                _planned_inputs(
                    {
                        "packet": compressed,
                        "subscriber_agents": subscriber_agents or [],
                        "jamming_level": jamming_level,
                    },
                    plan,
                    _algorithm_id(self.router),
                ),
            )
            if invocation:
                invocations.append(invocation)
            if not isinstance(routing, dict):
                routing = {}
            trace[self.router.name] = f"{len(routing.get('routes', []))} routes"
        else:
            routing = {"routes": list(subscriber_agents or []), "skipped": True}
            trace[self.router.name] = "skipped"

        return SemanticIntelligencePacket(
            schema_version=PACKET_SCHEMA_VERSION,
            mission_id=mission_id,
            summary=str(compressed.get("summary") or ""),
            tracks=list(perception.tracks or []),
            targets=list(compressed.get("targets") or []),
            semantic_vector=list(compressed.get("semantic_vector") or []),
            knowledge_graph=dict(compressed.get("knowledge_graph") or {}),
            routing=routing,
            provenance={
                "perception": perception.algorithm_trace,
                "cognition": cognition.algorithm_trace,
                "communication": trace,
                "algorithm_calls": [
                    {
                        key: value
                        for key, value in invocation.items()
                        if key not in {"input", "output", "usage"}
                    }
                    for invocation in invocations
                ],
                "algorithm_invocations": invocations,
                "targets": {
                    "source": compressed.get("target_source"),
                    "source_label": compressed.get("target_source_label"),
                    "source_detail": compressed.get("target_source_detail"),
                    "count": len(compressed.get("targets") or []),
                },
            },
            raw_compression_ratio=float(compressed.get("compression_ratio", 1.0)),
            task_schedule=perception.task_schedule,
            consumer_guide=dict(CONSUMER_GUIDE),
        )
