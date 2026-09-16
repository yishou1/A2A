"""抗干扰情报分发路由（工业默认 SAW + 可选 PPO）。

algorithm_id = anti_jam_mcdm_router（端口 9030）。生产主路径：
- mcdm_saw：Hwang & Yoon (1981) 多属性加权，可审计、确定性
- ppo_channel_policy：Schulman et al. (2017) PPO 信道策略（需训练权重，可选）
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from algorithms.base import AlgorithmBackend
from algorithms.anti_jam_mcdm_router.mcdm import mcdm_route


def _has_trained_ppo(config: dict[str, Any]) -> bool:
    """若存在训练 sidecar 或显式 force_ppo，则允许神经路径。"""
    if config.get("force_ppo") or config.get("use_ppo"):
        return True
    ckpt = str(
        config.get("ppo_channel_checkpoint")
        or config.get("marl_checkpoint")
        or "ppo_channel_policy.pt"
    )
    root = Path(__file__).resolve().parents[2]
    candidates = [
        Path(ckpt),
        root / "models" / "checkpoints" / Path(ckpt).name,
        root / ckpt,
    ]
    for p in candidates:
        meta = p.with_suffix(".meta.json") if p.suffix else Path(str(p) + ".meta.json")
        if meta.is_file():
            return True
        # 训练脚本也会写 *.meta.json sidecar
        alt = p.parent / (p.stem + ".meta.json")
        if alt.is_file():
            return True
    return False


class AntiJamMCDMRouter(AlgorithmBackend[dict[str, Any]]):
    name = "AntiJam-MCDM-Router"
    algorithm_id = "anti_jam_mcdm_router"
    config_key = "anti_jam_mcdm_router"

    def run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        packet = inputs.get("packet", {}) or {}
        agents = inputs.get("subscriber_agents", []) or []
        jamming = float(inputs.get("jamming_level", 0.0))

        # 工业默认：SAW。仅当显式关闭 prefer_deterministic 且已训练 PPO 时走神经路径。
        prefer_mcdm = bool(self.config.get("prefer_deterministic", True))
        if self.use_mock or prefer_mcdm or not _has_trained_ppo(self.config):
            return mcdm_route(packet, list(agents), jamming, self.config)
        return self._infer_ppo(packet, list(agents), jamming)

    def _infer_ppo(
        self, packet: dict[str, Any], agents: list[str], jamming: float
    ) -> dict[str, Any]:
        from agent.inference.routing import ppo_channel_route

        result = ppo_channel_route(packet, agents, jamming, self.config)
        if isinstance(result, dict):
            result.setdefault("route_mode", "ppo_channel_policy")
        return result
