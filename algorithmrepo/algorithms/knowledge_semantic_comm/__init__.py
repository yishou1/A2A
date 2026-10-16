"""knowledge_semantic_comm — 独立算法入口。"""

from __future__ import annotations

from typing import Any

from algorithms.knowledge_semantic_comm.backend import KnowledgeSemanticCommModel

ALGORITHM_ID = "knowledge_semantic_comm"
CONFIG_KEY = "knowledge_semantic_comm"


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
    return KnowledgeSemanticCommModel(use_mock=use_mock, config=cfg).run(
        {
            "perception": inputs.get("perception") or {},
            "cognition": inputs.get("cognition") or {},
        }
    )


__all__ = ["ALGORITHM_ID", "CONFIG_KEY", "KnowledgeSemanticCommModel", "predict"]
