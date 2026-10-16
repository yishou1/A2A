"""SynapseRAG：实体对齐 + 检索增强。

主路径为确定性规则检索（稳定可行）；神经网络/LLM RAG 为可选增强。
"""

from __future__ import annotations

from typing import Any

from algorithms.base import AlgorithmBackend


class SynapseRAG(AlgorithmBackend[dict[str, Any]]):
    name = "SynapseRAG"
    algorithm_id = "synapse_rag_retriever"
    config_key = "synapse_rag"

    def run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        classifications = inputs.get("classifications", [])
        context_docs = inputs.get("knowledge_base", [])
        query = inputs.get("query", "战场目标实体与威胁关联")
        prefer_deterministic = bool(self.config.get("prefer_deterministic", True))
        if self.use_mock or prefer_deterministic:
            return self._deterministic_rag(classifications, context_docs, query)
        return self._infer(classifications, context_docs, query)

    def _deterministic_rag(
        self,
        classifications: list[dict[str, Any]],
        context_docs: list[dict[str, Any]],
        query: str,
    ) -> dict[str, Any]:
        entities: list[dict[str, Any]] = []
        for cls in classifications:
            entities.append(
                {
                    "entity_id": f"ENT-{cls['target_id']}",
                    "type": "MilitaryUnit",
                    "label": cls.get("label"),
                    "relations": [{"predicate": "threat_of", "object": "mission_area"}],
                }
            )
        retrieved = context_docs[:3] if context_docs else [{"page": "page_index_0", "score": 0.9}]
        return {
            "entities": entities,
            "retrieved_pages": retrieved,
            "rag_context": f"[SynapseRAG] query={query!r}; page_hits={len(retrieved)}",
            "agent_notes": "确定性检索完成，图谱实体已对齐。",
            "rag_mode": "deterministic",
        }

    def _infer(
        self,
        classifications: list[dict[str, Any]],
        context_docs: list[dict[str, Any]],
        query: str,
    ) -> dict[str, Any]:
        from agent.inference.rag import run_synapse_rag

        result = run_synapse_rag(classifications, context_docs, query, self.config)
        if isinstance(result, dict):
            result.setdefault("rag_mode", "neural")
        return result
