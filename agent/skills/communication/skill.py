"""信息共享与通信技能：知识驱动语义通信 + MARL 动态路由。"""

from __future__ import annotations

from typing import Any

from agent.algorithm_library.factory import create_marl_router, create_semantic_comm
from agent.algorithm_library.planner_runtime import AlgorithmPlan
from agent.models.schemas import CognitionOutput, PerceptionOutput, SemanticIntelligencePacket
from agent.track_packet import CONSUMER_GUIDE, PACKET_SCHEMA_VERSION


class CommunicationSkill:
    def __init__(self, *, use_mock: bool = False, config: dict[str, Any] | None = None):
        cfg = config or {}
        self.compression = create_semantic_comm(use_mock=use_mock, config=cfg)
        self.router = create_marl_router(use_mock=use_mock, config=cfg)

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

        p_dump = perception.model_dump()
        c_dump = cognition.model_dump()
        trace: dict[str, str] = {}

        if enabled("knowledge_semantic_comm"):
            compressed = self.compression.run({"perception": p_dump, "cognition": c_dump})
            if not isinstance(compressed, dict):
                compressed = {}
            trace[self.compression.name] = f"ratio={compressed.get('compression_ratio', 1)}"
        else:
            compressed = {
                "summary": f"tracks={len(perception.tracks)}; targets fallback",
                "targets": [
                    {
                        "track_id": d.track_id,
                        "class": d.class_name,
                        "confidence": d.confidence,
                        "geo": d.geo.model_dump() if hasattr(d.geo, "model_dump") else d.geo,
                        "damage_score": d.damage_score,
                    }
                    for d in perception.detections
                ],
                "semantic_vector": [],
                "knowledge_graph": {},
                "compression_ratio": 1.0,
            }
            trace[self.compression.name] = "skipped->targets_fallback"

        if enabled("marl_dynamic_router"):
            routing = self.router.run(
                {
                    "packet": compressed,
                    "subscriber_agents": subscriber_agents or [],
                    "jamming_level": jamming_level,
                }
            )
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
            },
            raw_compression_ratio=float(compressed.get("compression_ratio", 1.0)),
            task_schedule=perception.task_schedule,
            consumer_guide=dict(CONSUMER_GUIDE),
        )
