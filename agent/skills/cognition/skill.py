"""认知识别技能：ImageBind → Multimodal Mamba → SupCon+Meta → SynapseRAG。"""

from __future__ import annotations

from typing import Any

from agent.models.schemas import CognitionOutput, PerceptionOutput, SensorBatch, ThreatAssessment
from agent.skills.cognition.imagebind_encoder import ImageBindEncoder
from agent.skills.cognition.multimodal_mamba import MultimodalMambaFusion
from agent.skills.cognition.supcon_meta_classifier import SupConMetaClassifier
from agent.skills.cognition.synapse_rag import SynapseRAG

_THREAT_MAP = {
    "hostile": ("high", 0.9),
    "unknown": ("medium", 0.55),
    "neutral": ("low", 0.25),
    "friendly": ("none", 0.05),
}


class CognitionSkill:
    @staticmethod
    def _apply_simulation_force_prior(
        batch: SensorBatch,
        classifications: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        prior = batch.context.get("simulation_force_prior") or {}
        n = int(prior.get("prefer_hostile_for_first_n_tracks", 0) or 0)
        if n <= 0 or not classifications:
            return classifications
        ordered = sorted(classifications, key=lambda c: str(c.get("target_id", "")))
        for i, cls in enumerate(ordered):
            if i < n:
                cls["label"] = "hostile"
                cls["confidence"] = max(float(cls.get("confidence", 0.5)), 0.75)
                cls["affiliation"] = "red"
            elif cls.get("affiliation") is None:
                cls["affiliation"] = "unknown"
        return classifications

    def __init__(self, *, use_mock: bool = True, config: dict[str, Any] | None = None):
        cfg = config or {}
        self.encoder = ImageBindEncoder(use_mock=use_mock, config=cfg.get("imagebind"))
        self.fusion = MultimodalMambaFusion(use_mock=use_mock, config=cfg.get("multimodal_mamba"))
        self.classifier = SupConMetaClassifier(use_mock=use_mock, config=cfg.get("supcon_meta"))
        self.rag = SynapseRAG(use_mock=use_mock, config=cfg.get("synapse_rag"))

    def execute(
        self,
        batch: SensorBatch,
        perception: PerceptionOutput,
    ) -> CognitionOutput:
        frame_dicts = [f.model_dump(mode="json") for f in batch.frames]
        embeddings = self.encoder.run({"frames": frame_dicts})
        trace = {self.encoder.name: f"{len(embeddings)} modality embeddings"}

        fusion_out = self.fusion.run(
            {
                "embeddings": embeddings,
                "tracks": perception.tracks,
            }
        )
        fused = fusion_out.get("fused_embeddings", {})
        trace[self.fusion.name] = f"seq_len={fusion_out.get('sequence_length', 0)}"

        support_shots = batch.context.get("support_shots") or []
        classifications = self.classifier.run(
            {"fused_embeddings": fused, "support_shots": support_shots}
        )
        classifications = self._apply_simulation_force_prior(batch, classifications)
        trace[self.classifier.name] = f"{len(classifications)} classifications"

        rag_out = self.rag.run(
            {
                "classifications": classifications,
                "knowledge_base": batch.context.get("knowledge_base", []),
                "query": batch.context.get("rag_query", "战场目标实体与威胁关联"),
            }
        )
        trace[self.rag.name] = rag_out.get("agent_notes", "ok")

        threats: list[ThreatAssessment] = []
        for cls in classifications:
            label = cls.get("label", "unknown")
            level, score = _THREAT_MAP.get(label, ("medium", 0.5))
            threats.append(
                ThreatAssessment(
                    target_id=cls["target_id"],
                    threat_level=level,
                    threat_score=score * float(cls.get("confidence", 0.5)),
                    rationale=f"SupCon+Meta label={label}; SynapseRAG aligned.",
                )
            )

        return CognitionOutput(
            embeddings=embeddings,
            classifications=classifications,
            threats=threats,
            entities=rag_out.get("entities", []),
            rag_context=rag_out.get("rag_context", ""),
            algorithm_trace=trace,
        )
