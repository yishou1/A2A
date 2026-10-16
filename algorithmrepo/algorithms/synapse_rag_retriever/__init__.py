"""synapse_rag_retriever — 独立算法入口。"""

from __future__ import annotations

from typing import Any

from algorithms.synapse_rag_retriever.backend import SynapseRAG

ALGORITHM_ID = "synapse_rag_retriever"
CONFIG_KEY = "synapse_rag"


def predict(
    inputs: dict[str, Any],
    *,
    use_mock: bool = True,
    config: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = dict(config or {})
    if params:
        cfg.update(params)
    return SynapseRAG(use_mock=use_mock, config=cfg).run(
        {
            "classifications": inputs.get("classifications") or [],
            "knowledge_base": inputs.get("knowledge_base") or [],
            "query": inputs.get("query") or "战场目标实体与威胁关联",
        }
    )


__all__ = ["ALGORITHM_ID", "CONFIG_KEY", "SynapseRAG", "predict"]
