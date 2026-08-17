"""认知识别技能：ImageBind → Multimodal Mamba → SupCon+Meta → SynapseRAG。"""

from __future__ import annotations

from typing import Any

from agent.algorithm_library.factory import (
    create_imagebind_encoder,
    create_mamba_fusion,
    create_supcon_classifier,
    create_synapse_rag,
)
from agent.algorithm_library.planner_runtime import AlgorithmPlan
from agent.models.schemas import CognitionOutput, PerceptionOutput, SensorBatch, ThreatAssessment

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

    @staticmethod
    def _apply_observable_classification_evidence(
        batch: SensorBatch,
        classifications: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        checkpoint_id = str(batch.context.get("director_checkpoint_id") or "")
        phase = str(batch.context.get("director_phase") or "").upper()
        # FIND produces contacts only. FIX and later phases may identify them
        # from current, auditable observables even when a legacy checkpoint is
        # still named MAR-CP-PERCEPTION.
        if phase == "FIND" or (not phase and checkpoint_id in {"", "MAR-CP-PERCEPTION"}):
            return classifications
        features = batch.context.get("observable_track_features") or {}
        if not isinstance(features, dict):
            return classifications
        by_id = {
            str(item.get("target_id")): item
            for item in classifications
            if isinstance(item, dict) and item.get("target_id")
        }
        for target_id, observable in features.items():
            if not isinstance(observable, dict):
                continue
            if str(observable.get("domain_hint") or "").casefold() not in {
                "maritime", "surface", "sea"
            }:
                continue
            try:
                speed_kts = float(observable.get("speed_kts"))
            except (TypeError, ValueError):
                continue
            sources = {
                str(source).casefold()
                for source in observable.get("sensor_sources") or []
            }
            classification = by_id.get(str(target_id))
            if classification is None:
                classification = {"target_id": str(target_id), "confidence": 0.5}
                classifications.append(classification)
                by_id[str(target_id)] = classification
            prior_classification = str(observable.get("prior_classification") or "").casefold()
            if prior_classification == "fishing_vessel":
                classification.update({
                    "label": "neutral",
                    "affiliation": "unknown",
                    "object_class": "fishing_vessel",
                    "confidence": max(float(classification.get("confidence", 0.5)), 0.9),
                    "classification_basis": "prior_verified_backend_classification",
                })
            elif prior_classification == "fast_attack_craft":
                classification.update({
                    "label": "hostile",
                    "affiliation": "red",
                    "object_class": "fast_attack_craft",
                    "confidence": max(float(classification.get("confidence", 0.5)), 0.85),
                    "classification_basis": "prior_verified_backend_classification",
                })
            elif observable.get("ais_observed") is True and speed_kts <= 12.0:
                classification.update({
                    "label": "neutral",
                    "affiliation": "unknown",
                    "object_class": "fishing_vessel",
                    "confidence": max(float(classification.get("confidence", 0.5)), 0.9),
                    "classification_basis": "ais_correlated_low_speed_surface_contact",
                })
            elif (
                observable.get("ais_observed") is not True
                and speed_kts >= 18.0
                and sources.intersection({"eo/ir", "eo-ir", "elint", "esm"})
            ):
                classification.update({
                    "label": "hostile",
                    "affiliation": "red",
                    "object_class": "fast_attack_craft",
                    "confidence": max(float(classification.get("confidence", 0.5)), 0.85),
                    "classification_basis": "non_ais_high_speed_multisensor_surface_contact",
                })
        return classifications

    def __init__(self, *, use_mock: bool = True, config: dict[str, Any] | None = None):
        cfg = config or {}
        self.encoder = create_imagebind_encoder(use_mock=use_mock, config=cfg)
        self.fusion = create_mamba_fusion(use_mock=use_mock, config=cfg)
        self.classifier = create_supcon_classifier(use_mock=use_mock, config=cfg)
        self.rag = create_synapse_rag(use_mock=use_mock, config=cfg)

    def execute(
        self,
        batch: SensorBatch,
        perception: PerceptionOutput,
        *,
        plan: AlgorithmPlan | None = None,
    ) -> CognitionOutput:
        def enabled(aid: str) -> bool:
            return plan is None or plan.is_enabled(aid)

        frame_dicts = [f.model_dump(mode="json") for f in batch.frames]
        trace: dict[str, str] = {}

        if enabled("imagebind_multimodal_encoder"):
            embeddings = self.encoder.run({"frames": frame_dicts})
            if not isinstance(embeddings, dict):
                embeddings = {}
            trace[self.encoder.name] = f"{len(embeddings)} modality embeddings"
        else:
            embeddings = {}
            trace[self.encoder.name] = "skipped"

        if enabled("multimodal_mamba_fusion"):
            fusion_out = self.fusion.run({"embeddings": embeddings, "tracks": perception.tracks})
            if not isinstance(fusion_out, dict):
                fusion_out = {}
            fused = fusion_out.get("fused_embeddings", {})
            trace[self.fusion.name] = f"seq_len={fusion_out.get('sequence_length', 0)}"
        else:
            fused = {}
            trace[self.fusion.name] = "skipped"

        if enabled("supcon_meta_classifier"):
            support_shots = batch.context.get("support_shots") or []
            classifications = self.classifier.run(
                {"fused_embeddings": fused, "support_shots": support_shots}
            )
            if isinstance(classifications, dict):
                classifications = classifications.get("classifications") or []
            if not isinstance(classifications, list):
                classifications = []
            classifications = self._apply_simulation_force_prior(batch, classifications)
            trace[self.classifier.name] = f"{len(classifications)} classifications"
        else:
            classifications = [
                {
                    "target_id": t.get("track_id"),
                    "label": "unknown",
                    "confidence": float(t.get("confidence", 0.5) or 0.5),
                    "affiliation": "unknown",
                }
                for t in perception.tracks
                if isinstance(t, dict) and t.get("track_id")
            ]
            trace[self.classifier.name] = "skipped->track_fallback"

        # Observable safety evidence is independent of the optional learned
        # classifier selected by the LLM planner.
        classifications = self._apply_observable_classification_evidence(
            batch,
            classifications,
        )

        if enabled("synapse_rag_retriever"):
            rag_out = self.rag.run(
                {
                    "classifications": classifications,
                    "knowledge_base": batch.context.get("knowledge_base", []),
                    "query": batch.context.get("rag_query", "战场目标实体与威胁关联"),
                }
            )
            if not isinstance(rag_out, dict):
                rag_out = {}
            trace[self.rag.name] = rag_out.get("agent_notes", "ok")
        else:
            rag_out = {"entities": [], "rag_context": ""}
            trace[self.rag.name] = "skipped"

        threats: list[ThreatAssessment] = []
        for cls in classifications:
            if not isinstance(cls, dict):
                continue
            label = cls.get("label", "unknown")
            level, score = _THREAT_MAP.get(label, ("medium", 0.5))
            tid = cls.get("target_id")
            if not tid:
                continue
            threats.append(
                ThreatAssessment(
                    target_id=str(tid),
                    threat_level=level,
                    threat_score=score * float(cls.get("confidence", 0.5)),
                    rationale=f"SupCon+Meta label={label}; SynapseRAG aligned.",
                )
            )

        return CognitionOutput(
            embeddings=embeddings if isinstance(embeddings, dict) else {},
            classifications=classifications,
            threats=threats,
            entities=rag_out.get("entities", []),
            rag_context=rag_out.get("rag_context", ""),
            algorithm_trace=trace,
        )
