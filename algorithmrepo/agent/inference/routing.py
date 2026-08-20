"""MARL 动态路由。"""

from __future__ import annotations

from typing import Any

from agent.inference.registry import get_marl_policy


def marl_route(
    packet: dict[str, Any],
    agents: list[str],
    jamming: float,
    config: dict[str, Any],
) -> dict[str, Any]:
    policy = get_marl_policy(config)
    return policy.route(packet, agents, jamming)
