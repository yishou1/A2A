"""信息共享与通信技能：知识驱动语义通信 + MARL 动态路由。"""

from __future__ import annotations

from typing import Any

from agent.models.schemas import CognitionOutput, PerceptionOutput, SemanticIntelligencePacket
from agent.skills.base import subskill_config
from agent.skills.communication.knowledge_semantic_comm import KnowledgeSemanticCommModel
from agent.skills.communication.marl_dynamic_router import MARLDynamicRouter


class CommunicationSkill:
    def __init__(self, *, use_mock: bool = True, config: dict[str, Any] | None = None):
        cfg = config or {}
        self.compression = KnowledgeSemanticCommModel(
            use_mock=use_mock, config=subskill_config(cfg, "knowledge_semantic_comm")
        )
        self.router = MARLDynamicRouter(
            use_mock=use_mock, config=subskill_config(cfg, "marl_dynamic_router")
        )

    def execute(
        self,
        mission_id: str,
        perception: PerceptionOutput,
        cognition: CognitionOutput,
        *,
        subscriber_agents: list[str] | None = None,
        jamming_level: float = 0.0,
    ) -> SemanticIntelligencePacket:
        p_dump = perception.model_dump()
        c_dump = cognition.model_dump()

        compressed = self.compression.run({"perception": p_dump, "cognition": c_dump})
        trace = {self.compression.name: f"ratio={compressed.get('compression_ratio', 1)}"}

        routing = self.router.run(
            {
                "packet": compressed,
                "subscriber_agents": subscriber_agents or [],
                "jamming_level": jamming_level,
            }
        )
        trace[self.router.name] = f"{len(routing.get('routes', []))} routes"

        return SemanticIntelligencePacket(
            mission_id=mission_id,
            summary=compressed["summary"],
            targets=compressed.get("targets", []),
            semantic_vector=compressed.get("semantic_vector", []),
            knowledge_graph=compressed.get("knowledge_graph", {}),
            routing=routing,
            provenance={
                "perception": perception.algorithm_trace,
                "cognition": cognition.algorithm_trace,
                "communication": trace,
            },
            raw_compression_ratio=float(compressed.get("compression_ratio", 1.0)),
            task_schedule=perception.task_schedule,
        )
