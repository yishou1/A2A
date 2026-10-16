"""独立算法包公共基类（不依赖 Agent Skill 编排）。"""

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
        "anti_jam_mcdm_router",
        "marl_ppo_scheduler",
    }
)


def subskill_config(parent: dict[str, Any] | None, key: str) -> dict[str, Any]:
    """合并顶层 inference 配置与子算法块（子块优先）。"""
    cfg = dict(parent or {})
    sub = dict(cfg.get(key) or {})
    base = {k: v for k, v in cfg.items() if k not in NESTED_SKILL_KEYS}
    return {**base, **sub}


class AlgorithmBackend(ABC, Generic[T]):
    """单算法后端：给定 inputs dict，返回该算法自身的输出。"""

    name: str = "base"
    algorithm_id: str = "base"
    config_key: str = ""

    def __init__(self, *, use_mock: bool = True, config: dict[str, Any] | None = None):
        self.use_mock = use_mock
        self.config = config or {}

    @abstractmethod
    def run(self, inputs: dict[str, Any]) -> T:
        ...
