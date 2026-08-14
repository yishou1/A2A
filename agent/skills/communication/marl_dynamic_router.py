"""MARL 动态路由：MARLPolicyNetwork 策略网络。"""

from __future__ import annotations

from typing import Any

from agent.skills.base import AlgorithmBackend


class MARLDynamicRouter(AlgorithmBackend[dict[str, Any]]):
    name = "MARL-Dynamic-Routing"

    def run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        packet = inputs.get("packet", {})
        agents = inputs.get("subscriber_agents", [])
        jamming = float(inputs.get("jamming_level", 0.0))
        if self.use_mock:
            return self._mock_route(packet, agents, jamming)
        return self._infer(packet, agents, jamming)

    def _mock_route(
        self, packet: dict[str, Any], agents: list[str], jamming: float
    ) -> dict[str, Any]:
        default_agents = agents or ["command_agent", "fire_control_agent", "logistics_agent"]
        priority = "high" if any(
            t.get("threat_level") == "high" for t in packet.get("targets", [])
        ) else "normal"
        routes = []
        for i, agent in enumerate(default_agents):
            reward = max(0.1, 1.0 - jamming * 0.65 - i * 0.05)
            channel = "semantic_rf" if jamming < 0.5 else "fhss_backup"
            routes.append(
                {
                    "destination": agent,
                    "channel": channel,
                    "reliability": round(reward, 3),
                    "priority": priority,
                    "marl_policy": "mock",
                }
            )
        return {
            "routes": routes,
            "anti_jam_mode": jamming >= 0.5,
            "broadcast_summary": packet.get("summary", ""),
        }

    def _infer(
        self, packet: dict[str, Any], agents: list[str], jamming: float
    ) -> dict[str, Any]:
        from agent.inference.routing import marl_route

        return marl_route(packet, agents, jamming, self.config)
