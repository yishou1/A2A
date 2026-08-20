"""知识驱动 Semantic Communication Model：语义通信压缩。"""

from __future__ import annotations

import json
from typing import Any

from agent.skills.base import AlgorithmBackend


class KnowledgeSemanticCommModel(AlgorithmBackend[dict[str, Any]]):
    name = "Knowledge-Semantic-Comm"

    def run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        perception = inputs.get("perception", {})
        cognition = inputs.get("cognition", {})
        if self.use_mock:
            return self._mock_compress(perception, cognition)
        return self._infer(perception, cognition)

    def _mock_compress(
        self, perception: dict[str, Any], cognition: dict[str, Any]
    ) -> dict[str, Any]:
        return self._compress(perception, cognition)

    def _infer(self, perception: dict[str, Any], cognition: dict[str, Any]) -> dict[str, Any]:
        from agent.inference.semantic_comm import compress_intelligence

        return compress_intelligence(perception, cognition, self.config)

    def _compress(self, perception: dict[str, Any], cognition: dict[str, Any]) -> dict[str, Any]:
        raw_size = len(json.dumps({"p": perception, "c": cognition}, ensure_ascii=False))
        targets: list[dict[str, Any]] = []
        class_by_id = {c["target_id"]: c for c in cognition.get("classifications", [])}
        threat_by_id = {t["target_id"]: t for t in cognition.get("threats", [])}
        entity_by_target = {
            e.get("entity_id", "").replace("ENT-", ""): e for e in cognition.get("entities", [])
        }

        for det in perception.get("detections", []):
            tid = det.get("track_id") or "unknown"
            cls = class_by_id.get(tid, {})
            threat = threat_by_id.get(tid, {})
            label = cls.get("label")
            affiliation = cls.get("affiliation")
            if affiliation is None and label:
                affiliation = (
                    "red" if label == "hostile" else "blue" if label == "friendly" else "unknown"
                )
            ent = entity_by_target.get(tid, {})
            targets.append(
                {
                    "track_id": tid,
                    "class": det.get("class_name"),
                    "label": label,
                    "affiliation": affiliation,
                    "threat_level": threat.get("threat_level") if isinstance(threat, dict) else None,
                    "geo": det.get("geo"),
                    "damage_score": det.get("damage_score"),
                    "confidence": det.get("confidence"),
                    "knowledge_ref": ent.get("entity_id"),
                }
            )

        high_threat = sum(1 for t in targets if t.get("threat_level") == "high")
        summary_parts = [f"目标数={len(targets)}", f"高威胁={high_threat}"]
        semantic_vector = self._pool_vector(cognition.get("embeddings", {}))

        compressed = {
            "summary": "；".join(summary_parts),
            "targets": targets,
            "semantic_vector": semantic_vector,
            "knowledge_graph": {
                "nodes": cognition.get("entities", []),
                "edges": [
                    r
                    for e in cognition.get("entities", [])
                    for r in e.get("relations", [])
                ],
            },
        }
        comp_size = len(json.dumps(compressed, ensure_ascii=False))
        compressed["compression_ratio"] = round(raw_size / max(comp_size, 1), 2)
        return compressed

    @staticmethod
    def _pool_vector(embeddings: dict[str, list[float]]) -> list[float]:
        if not embeddings:
            return []
        dim = len(next(iter(embeddings.values())))
        n = len(embeddings)
        return [sum(v[i] for v in embeddings.values()) / n for i in range(dim)]
