"""MARL-PPO 任务调度推理入口。"""

from __future__ import annotations

from typing import Any

from agent.inference.registry import get_marl_ppo_scheduler
from agent.training.battlefield_scheduling_env import situation_from_perception


def marl_ppo_schedule(
    tracks: list[dict[str, Any]],
    detections: list[dict[str, Any]],
    batch_context: dict[str, Any],
    frames: list[dict[str, Any]],
    config: dict[str, Any],
) -> dict[str, Any]:
    situation = situation_from_perception(tracks, detections, batch_context, frames)
    policy = get_marl_ppo_scheduler(config)
    return policy.schedule(situation, deterministic=True)
