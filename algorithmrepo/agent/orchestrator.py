"""战术情报智能体：串联三技能流水线。"""

from __future__ import annotations

from typing import Any

from agent.models.schemas import SemanticIntelligencePacket, SensorBatch
from agent.skills.cognition.skill import CognitionSkill
from agent.skills.communication.skill import CommunicationSkill
from agent.skills.perception.skill import PerceptionSkill
from tactical_intelligence_agent.batch_preparer import finalize_batch_inference, prepare_batch_for_inference


class TacticalIntelligenceAgent:
    """
    独立战术情报 Agent。

    输入：前端传感器原始数据批次
    输出：语义压缩情报包（供其他 Agent 订阅/拉取）
    """

    def __init__(self, *, use_mock: bool = True, config: dict[str, Any] | None = None):
        cfg = config or {}
        inference = cfg.get("inference") or {}

        def _merge(skill_cfg: dict[str, Any] | None) -> dict[str, Any]:
            return {**inference, **(skill_cfg or {})}

        self.perception = PerceptionSkill(
            use_mock=use_mock,
            config=_merge(cfg.get("perception")),
        )
        self.cognition = CognitionSkill(
            use_mock=use_mock,
            config=_merge(cfg.get("cognition")),
        )
        self.communication = CommunicationSkill(
            use_mock=use_mock,
            config=_merge(cfg.get("communication")),
        )
        self._track_state: dict[str, list[dict[str, Any]]] = {}
        self._config = cfg

    def process(self, batch: SensorBatch) -> SemanticIntelligencePacket:
        mission_id = batch.mission_id
        prior = self._track_state.get(mission_id, [])

        batch = prepare_batch_for_inference(batch)
        try:
            perception_out = self.perception.execute(batch, prior_tracks=prior)
            self._track_state[mission_id] = perception_out.tracks

            cognition_out = self.cognition.execute(batch, perception_out)

            jamming = float(batch.context.get("jamming_level", 0.0))
            subscribers = batch.context.get("subscriber_agents") or []

            packet = self.communication.execute(
                mission_id,
                perception_out,
                cognition_out,
                subscriber_agents=subscribers,
                jamming_level=jamming,
            )

            try:
                from tactical_intelligence_agent.artifact_publisher import publish_processed_artifacts

                packet.output_attachments = publish_processed_artifacts(
                    batch,
                    perception_out,
                    config=self._config,
                )
            except Exception:
                pass

            return packet
        finally:
            finalize_batch_inference()
