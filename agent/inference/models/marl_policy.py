"""MARL 动态路由策略网络。"""

from __future__ import annotations

from typing import Any

import torch
import torch.nn as nn
import torch.nn.functional as F


class MARLPolicyNetwork(nn.Module):
    """多智能体路由策略：输入干扰/威胁/订阅者状态，输出信道与可靠性。"""

    CHANNELS = ("semantic_rf", "fhss_backup", "satcom_relay")

    def __init__(self, state_dim: int = 8, hidden: int = 64):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(state_dim, hidden),
            nn.ReLU(inplace=True),
            nn.Linear(hidden, hidden),
            nn.ReLU(inplace=True),
        )
        self.channel_head = nn.Linear(hidden, len(self.CHANNELS))
        self.reliability_head = nn.Linear(hidden, 1)

    def forward(self, state: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        h = self.net(state)
        channel_logits = self.channel_head(h)
        reliability = torch.sigmoid(self.reliability_head(h))
        return channel_logits, reliability

    @torch.inference_mode()
    def route(
        self,
        packet: dict[str, Any],
        agents: list[str],
        jamming: float,
    ) -> dict[str, Any]:
        default_agents = agents or ["command_agent", "fire_control_agent", "logistics_agent"]
        n_high = sum(1 for t in packet.get("targets", []) if t.get("threat_level") == "high")
        state = torch.tensor(
            [
                jamming,
                len(packet.get("targets", [])) / 10.0,
                n_high / max(len(packet.get("targets", [])), 1),
                len(default_agents) / 5.0,
                float(packet.get("compression_ratio", 1.0)) / 10.0,
                1.0 if jamming >= 0.5 else 0.0,
                min(1.0, len(packet.get("semantic_vector", [])) / 512.0),
                0.5,
            ],
            dtype=torch.float32,
            device=next(self.parameters()).device,
        ).unsqueeze(0)

        channel_logits, reliability = self.forward(state)
        channel_idx = int(channel_logits.argmax(dim=-1).item())
        channel = self.CHANNELS[channel_idx]
        base_rel = float(reliability.item())

        priority = "high" if n_high > 0 else "normal"
        routes = []
        for i, agent in enumerate(default_agents):
            rel = max(0.1, base_rel - i * 0.03 - jamming * 0.2)
            routes.append(
                {
                    "destination": agent,
                    "channel": channel if jamming < 0.5 else "fhss_backup",
                    "reliability": round(rel, 3),
                    "priority": priority,
                    "marl_policy": "MARLPolicyNetwork",
                }
            )
        return {
            "routes": routes,
            "anti_jam_mode": jamming >= 0.5,
            "broadcast_summary": packet.get("summary", ""),
        }
