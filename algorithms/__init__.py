"""两个本地智能体所用算法的独立包集合。

每个子包均可单独调用，不依赖 TIA / 任务调度 Agent 编排：

    from algorithms.battlefield_rtdetr_detector import predict
    outputs = predict({"frames": [...]}, use_mock=True)

算力三档（今早划分，与 config/profiles 对齐）::

    from algorithms import list_tiers, invoke
    list_tiers()  # low / mid / high
    invoke("battlefield_rtdetr_detector", inputs, tier="low", use_mock=True)

或：

    python -m algorithms --list-tiers
    python -m algorithms battlefield_rtdetr_detector --inputs in.json --tier mid
"""

from __future__ import annotations

from typing import Any, Callable

from algorithms.compute_tiers import (  # noqa: F401
    COMPUTE_TIERS,
    TIER_AFFECTED_ALGORITHMS,
    apply_tier,
    list_tiers,
    tier_summary,
)
from algorithms.contracts import (  # noqa: F401
    CONTRACTS,
    ContractError,
    contract_summary,
    get_contract,
    invoke,
)

ALGORITHM_IDS: tuple[str, ...] = (
    "battlefield_rtdetr_detector",
    "siamese_mask2former_damage",
    "edl_evidential_verifier",
    "motr_neural_kalman_tracker",
    "marl_ppo_task_scheduler",
    "imagebind_multimodal_encoder",
    "multimodal_mamba_fusion",
    "supcon_meta_classifier",
    "synapse_rag_retriever",
    "knowledge_semantic_comm",
    "anti_jam_mcdm_router",
)

# 成熟度：production = 可独立实战；stable = 工业可用有据可查；research = 原型
ALGORITHM_MATURITY: dict[str, str] = {
    "battlefield_rtdetr_detector": "production",
    "siamese_mask2former_damage": "stable",
    "edl_evidential_verifier": "stable",
    "motr_neural_kalman_tracker": "stable",
    "marl_ppo_task_scheduler": "stable",
    "imagebind_multimodal_encoder": "stable",
    "multimodal_mamba_fusion": "research",
    "supcon_meta_classifier": "stable",
    "synapse_rag_retriever": "research",
    "knowledge_semantic_comm": "stable",
    "anti_jam_mcdm_router": "stable",
}


def get_predict(algorithm_id: str) -> Callable[..., dict[str, Any]]:
    """按 algorithm_id 获取独立 predict 入口。"""
    if algorithm_id not in ALGORITHM_IDS:
        raise KeyError(f"unknown algorithm_id: {algorithm_id}")
    module = __import__(f"algorithms.{algorithm_id}", fromlist=["predict"])
    return module.predict


def predict(algorithm_id: str, inputs: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """直接调用算法包 predict（不做契约投影）。

    需要校验/投影时请用 ``algorithms.invoke`` 或 ``algorithms.contracts.invoke``。
    若传入 ``tier`` / ``param_tier``，会先 ``apply_tier`` 再调用。
    """
    tier = kwargs.pop("tier", None)
    config = dict(kwargs.get("config") or {})
    params = dict(kwargs.get("params") or {})
    if params:
        config.update(params)
    if tier or config.get("param_tier") or config.get("compute_profile") or config.get("tier"):
        config = apply_tier(
            config,
            tier=str(tier or config.get("tier") or config.get("param_tier") or config.get("compute_profile")),
        )
        kwargs["config"] = config
    return get_predict(algorithm_id)(inputs, **kwargs)


__all__ = [
    "ALGORITHM_IDS",
    "ALGORITHM_MATURITY",
    "COMPUTE_TIERS",
    "CONTRACTS",
    "ContractError",
    "TIER_AFFECTED_ALGORITHMS",
    "apply_tier",
    "contract_summary",
    "get_contract",
    "get_predict",
    "invoke",
    "list_tiers",
    "predict",
    "tier_summary",
]
