"""技能与算法后端基类。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Generic, TypeVar

T = TypeVar("T")

NESTED_SKILL_KEYS = frozenset(
    {
        "rt_detr_odconv",
        "siamese_mask2former",
        "edl",
        "motr_neural_kalman",
        "imagebind",
        "multimodal_mamba",
        "supcon_meta",
        "synapse_rag",
        "knowledge_semantic_comm",
        "marl_dynamic_router",
        "marl_ppo_scheduler",
    }
)


def subskill_config(parent: dict[str, Any] | None, key: str) -> dict[str, Any]:
    """合并顶层 inference 配置与子技能块（子块优先）。"""
    cfg = dict(parent or {})
    sub = dict(cfg.get(key) or {})
    base = {k: v for k, v in cfg.items() if k not in NESTED_SKILL_KEYS}
    return {**base, **sub}


class AlgorithmBackend(ABC, Generic[T]):
    name: str = "base"

    def __init__(self, *, use_mock: bool = True, config: dict[str, Any] | None = None):
        self.use_mock = use_mock
        self.config = config or {}

    @abstractmethod
    def run(self, inputs: dict[str, Any]) -> T:
        ...
