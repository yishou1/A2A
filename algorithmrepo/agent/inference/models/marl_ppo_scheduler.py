"""MARL-PPO 任务调度策略网络（Actor-Critic，参数共享）。"""

from __future__ import annotations

from typing import Any

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

from agent.training.battlefield_scheduling_env import (
    MAX_STRIKE_ASSETS,
    MAX_TARGETS,
    BattlefieldSchedulingEnv,
    BattlefieldSchedulingState,
)


class MARLPPOSchedulerNet(nn.Module):
    """
    参数共享多智能体 PPO 策略：
    - 共享编码器提取战场态势
    - Actor 输出每个智能体的目标分配动作
    - Critic 估计全局状态价值
    """

    def __init__(
        self,
        obs_dim: int,
        n_actions: int,
        n_agents: int,
        hidden: int = 128,
    ):
        super().__init__()
        self.n_actions = n_actions
        self.n_agents = n_agents
        self.encoder = nn.Sequential(
            nn.Linear(obs_dim, hidden),
            nn.ReLU(inplace=True),
            nn.Linear(hidden, hidden),
            nn.ReLU(inplace=True),
        )
        self.actor = nn.Linear(hidden, n_actions)
        self.critic = nn.Sequential(
            nn.Linear(hidden, hidden // 2),
            nn.ReLU(inplace=True),
            nn.Linear(hidden // 2, 1),
        )

    def forward(self, obs: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        """obs: (batch, obs_dim) -> (action_logits (batch, n_actions), value (batch, 1))"""
        h = self.encoder(obs)
        return self.actor(h), self.critic(h)

    def act(
        self,
        obs: np.ndarray,
        *,
        deterministic: bool = False,
        valid_target_count: int | None = None,
        action_mask: list[bool] | np.ndarray | None = None,
    ) -> tuple[list[int], float, torch.Tensor]:
        device = next(self.parameters()).device
        x = torch.tensor(obs, dtype=torch.float32, device=device).unsqueeze(0)
        logits, value = self.forward(x)
        if valid_target_count is not None:
            valid_target_count = max(0, min(int(valid_target_count), self.n_actions - 1))
            logits[:, valid_target_count + 1 :] = torch.finfo(logits.dtype).min
        if action_mask is not None:
            allowed = torch.as_tensor(action_mask, dtype=torch.bool, device=device).reshape(1, -1)
            if allowed.shape[1] != self.n_actions or not bool(allowed.any()):
                raise ValueError("action_mask must allow at least one scheduler action")
            logits = logits.masked_fill(~allowed, torch.finfo(logits.dtype).min)
        dist = torch.distributions.Categorical(logits=logits)
        if deterministic:
            action = int(logits.argmax(dim=-1).item())
        else:
            action = int(dist.sample().item())
        log_prob = dist.log_prob(torch.tensor(action, device=device))
        return [action], float(value.item()), log_prob

    @torch.inference_mode()
    def schedule(
        self,
        situation: BattlefieldSchedulingState,
        *,
        deterministic: bool = True,
    ) -> dict[str, Any]:
        """推理：为所有传感器与打击资产生成任务分配方案。"""
        env = BattlefieldSchedulingEnv()
        env.reset(situation)
        sensor_count = len(situation.sensors[: env.max_sensors])
        strike_count = len(situation.strike_assets[:MAX_STRIKE_ASSETS])
        actions: list[int] = [0] * env.n_agents
        valid_target_count = len(situation.targets[:MAX_TARGETS])
        sensor_targets_used: set[int] = set()
        for agent_idx in range(sensor_count):
            obs = env.build_agent_obs(agent_idx)
            sensor = situation.sensors[agent_idx]
            mask = [index <= valid_target_count and index not in sensor_targets_used for index in range(self.n_actions)]
            mask[0] = True
            if not sensor.available:
                mask = [True] + [False] * (self.n_actions - 1)
            acts, _, _ = self.act(
                obs,
                deterministic=deterministic,
                valid_target_count=valid_target_count,
                action_mask=mask,
            )
            actions[agent_idx] = acts[0]
            if acts[0] > 0:
                sensor_targets_used.add(acts[0])
        strike_targets_used: set[int] = set()
        for strike_idx in range(strike_count):
            agent_idx = env.max_sensors + strike_idx
            obs = env.build_agent_obs(agent_idx)
            asset = situation.strike_assets[strike_idx]
            mask = [index <= valid_target_count and index not in strike_targets_used for index in range(self.n_actions)]
            mask[0] = True
            if not asset.available or asset.remaining_ammo <= 0:
                mask = [True] + [False] * (self.n_actions - 1)
            acts, _, _ = self.act(
                obs,
                deterministic=deterministic,
                valid_target_count=valid_target_count,
                action_mask=mask,
            )
            actions[agent_idx] = acts[0]
            if acts[0] > 0:
                strike_targets_used.add(acts[0])
        _, _, done, info = env.step(actions)
        assert done

        targets = situation.targets[:MAX_TARGETS]
        sensor_assignments = []
        for sid, tid in info["sensor_assignments"].items():
            entry: dict[str, Any] = {"sensor_id": sid, "target_id": tid, "task": "surveillance"}
            if tid:
                target = next((t for t in targets if t.target_id == tid), None)
                if target:
                    entry["priority"] = "high" if target.threat_score >= 0.6 else "normal"
                    entry["rationale"] = f"threat={target.threat_score:.2f}"
            else:
                entry["priority"] = "idle"
            sensor_assignments.append(entry)

        reattack_plan = []
        for aid, tid in info["strike_assignments"].items():
            if not tid:
                continue
            target = next((t for t in targets if t.target_id == tid), None)
            if target is None:
                continue
            reattack_plan.append(
                {
                    "asset_id": aid,
                    "target_id": tid,
                    "task": "reattack" if target.needs_reattack else "strike",
                    "priority": "critical" if target.needs_reattack else "normal",
                    "expected_damage": round(min(1.0, target.damage_score + 0.35), 3),
                    "rationale": (
                        f"毁伤不足({target.damage_score:.2f})，需再攻击"
                        if target.needs_reattack
                        else f"威胁目标({target.threat_score:.2f})"
                    ),
                }
            )

        return {
            "sensor_assignments": sensor_assignments,
            "reattack_plan": reattack_plan,
            "covered_targets": info["covered_targets"],
            "reattack_targets": info["reattack_targets"],
            "algorithm": "MARL-PPO",
            "n_agents": sensor_count + strike_count,
        }

    def evaluate_actions(
        self,
        obs: torch.Tensor,
        actions: torch.Tensor,
        valid_target_counts: torch.Tensor | None = None,
        action_masks: torch.Tensor | None = None,
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        logits, values = self.forward(obs)
        if valid_target_counts is not None:
            counts = valid_target_counts.to(device=logits.device, dtype=torch.long).clamp(
                min=0, max=self.n_actions - 1
            )
            invalid = torch.arange(self.n_actions, device=logits.device).unsqueeze(0) > counts.unsqueeze(1)
            logits = logits.masked_fill(invalid, torch.finfo(logits.dtype).min)
        if action_masks is not None:
            allowed = action_masks.to(device=logits.device, dtype=torch.bool)
            if allowed.shape != logits.shape:
                raise ValueError("action_masks shape must match action logits")
            logits = logits.masked_fill(~allowed, torch.finfo(logits.dtype).min)
        dist = torch.distributions.Categorical(logits=logits)
        log_probs = dist.log_prob(actions)
        entropy = dist.entropy()
        return log_probs, values.squeeze(-1), entropy
