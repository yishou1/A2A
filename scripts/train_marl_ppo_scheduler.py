"""
在战场任务调度环境上训练 MARL-PPO 策略（参数共享多智能体）。

场景来源：iron_valley_red_blue 红蓝对抗态势 + 合成目标/传感器配置。
训练完成后将权重用于感知阶段任务调度推理。

用法（项目根目录）:
  .\\.venv\\Scripts\\python.exe scripts/train_marl_ppo_scheduler.py --epochs 200
  .\\.venv\\Scripts\\python.exe scripts/train_marl_ppo_scheduler.py --epochs 500 --save models/checkpoints/marl_ppo_scheduler.pt
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CHECKPOINT_DIR = ROOT / "models" / "checkpoints"
DEFAULT_SCENARIO = ROOT / "scripts" / "simulation" / "scenarios" / "iron_valley_red_blue.yaml"


def _random_situation(rng: random.Random, phase: str):
    from agent.training.battlefield_scheduling_env import (
        BattlefieldSchedulingState,
        SchedulingSensor,
        SchedulingTarget,
        StrikeAsset,
    )
    from scripts.simulation.situation import RedBlueSituation

    sit = RedBlueSituation(DEFAULT_SCENARIO)
    overlay = sit.context_overlay(phase)
    snap = overlay["battlefield_situation"]
    ao = (snap.get("area_of_operations") or {}).get("center") or {}
    base_lat = float(ao.get("lat", 30.512))
    base_lon = float(ao.get("lon", 114.381))

    n_targets = rng.randint(2, 6)
    targets: list[SchedulingTarget] = []
    enemy_units = (snap.get("red_force") or {}).get("units") or []
    for i in range(n_targets):
        if i < len(enemy_units):
            u = enemy_units[i]
            geo = u.get("geo") or {}
            lat = float(geo.get("lat", base_lat)) + rng.uniform(-0.02, 0.02)
            lon = float(geo.get("lon", base_lon)) + rng.uniform(-0.02, 0.02)
            cls = str(u.get("type", "tank"))
        else:
            lat = base_lat + rng.uniform(-0.03, 0.03)
            lon = base_lon + rng.uniform(-0.03, 0.03)
            cls = rng.choice(["tank", "vehicle", "artillery", "person"])
        damage = rng.uniform(0.0, 0.9)
        threat = rng.uniform(0.3, 1.0)
        needs = damage < 0.55 and threat > 0.4
        if phase == "bda":
            needs = damage < 0.65
        targets.append(
            SchedulingTarget(
                target_id=f"T-{i}",
                threat_score=threat,
                damage_score=damage,
                confidence=rng.uniform(0.5, 0.95),
                lat=lat,
                lon=lon,
                needs_reattack=needs,
                class_name=cls,
            )
        )

    sensors = [
        SchedulingSensor("EO-FWD-1", "eo_ir", True, rng.uniform(0, 0.3), base_lat, base_lon),
        SchedulingSensor("SAR-1", "sar", True, rng.uniform(0, 0.2), base_lat + 0.01, base_lon),
        SchedulingSensor("RADAR-1", "radar", rng.random() > 0.2, 0.0, base_lat, base_lon + 0.01),
    ]

    friendly = (snap.get("blue_force") or {}).get("units") or []
    strikes: list[StrikeAsset] = []
    for u in friendly:
        ut = str(u.get("type", ""))
        if ut in ("artillery", "mlrs", "atgm", "missile", "fire_support"):
            strikes.append(
                StrikeAsset(
                    str(u.get("unit_id", f"A-{len(strikes)}")),
                    ut,
                    u.get("status", "active") == "active",
                    rng.uniform(0.3, 1.0),
                )
            )
    if not strikes:
        strikes = [
            StrikeAsset("ARTY-1", "artillery", True, rng.uniform(0.5, 1.0)),
            StrikeAsset("ATGM-1", "atgm", True, rng.uniform(0.4, 1.0)),
        ]

    return BattlefieldSchedulingState(
        targets=targets,
        sensors=sensors,
        strike_assets=strikes,
        jamming_level=float(overlay["jamming_level"]),
        phase=phase,
        base_lat=base_lat,
        base_lon=base_lon,
    )


def collect_rollout(model, env, situation, device):
    import numpy as np
    import torch

    env.reset(situation)
    obs_list, act_list, logp_list, rew_list, val_list, done_list = [], [], [], [], [], []

    for agent_idx in range(env.n_agents):
        obs = env.build_agent_obs(agent_idx)
        obs_t = torch.tensor(obs, dtype=torch.float32, device=device).unsqueeze(0)
        logits, value = model(obs_t)
        dist = torch.distributions.Categorical(logits=logits)
        action = dist.sample()
        log_prob = dist.log_prob(action)

        obs_list.append(obs)
        act_list.append(int(action.item()))
        logp_list.append(log_prob)
        val_list.append(value.squeeze())
        rew_list.append(0.0)
        done_list.append(False)

    _, rewards, done, _ = env.step(act_list)
    for i in range(len(rewards)):
        rew_list[i] = rewards[i]
        done_list[i] = done

    return {
        "obs": np.array(obs_list, dtype=np.float32),
        "actions": np.array(act_list, dtype=np.int64),
        "log_probs": torch.stack(logp_list),
        "rewards": np.array(rew_list, dtype=np.float32),
        "values": torch.stack(val_list).detach(),
        "dones": np.array(done_list, dtype=np.float32),
    }


def compute_gae(rewards, values, dones, gamma=0.99, lam=0.95):
    import numpy as np

    adv = np.zeros_like(rewards, dtype=np.float32)
    last_gae = 0.0
    for t in reversed(range(len(rewards))):
        next_val = 0.0 if t == len(rewards) - 1 else values[t + 1]
        mask = 1.0 - dones[t]
        delta = rewards[t] + gamma * next_val * mask - values[t]
        last_gae = delta + gamma * lam * mask * last_gae
        adv[t] = last_gae
    returns = adv + values
    return adv, returns


def ppo_update(model, optimizer, batch, device, clip_eps=0.2, vf_coef=0.5, ent_coef=0.01):
    import torch
    import torch.nn.functional as F

    obs = torch.tensor(batch["obs"], dtype=torch.float32, device=device)
    actions = torch.tensor(batch["actions"], dtype=torch.long, device=device)
    old_log_probs = batch["log_probs"].detach().to(device)
    advantages = torch.tensor(batch["advantages"], dtype=torch.float32, device=device)
    returns = torch.tensor(batch["returns"], dtype=torch.float32, device=device)

    advantages = (advantages - advantages.mean()) / (advantages.std() + 1e-8)

    new_log_probs, values, entropy = model.evaluate_actions(obs, actions)
    ratio = torch.exp(new_log_probs - old_log_probs)
    surr1 = ratio * advantages
    surr2 = torch.clamp(ratio, 1.0 - clip_eps, 1.0 + clip_eps) * advantages
    policy_loss = -torch.min(surr1, surr2).mean()
    value_loss = F.mse_loss(values, returns)
    ent_loss = -entropy.mean()

    loss = policy_loss + vf_coef * value_loss + ent_coef * ent_loss
    optimizer.zero_grad()
    loss.backward()
    torch.nn.utils.clip_grad_norm_(model.parameters(), 0.5)
    optimizer.step()
    return {
        "loss": float(loss.item()),
        "policy_loss": float(policy_loss.item()),
        "value_loss": float(value_loss.item()),
        "entropy": float(-ent_loss.item()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Train MARL-PPO battlefield task scheduler")
    parser.add_argument("--epochs", type=int, default=200)
    parser.add_argument("--rollouts-per-epoch", type=int, default=16)
    parser.add_argument("--lr", type=float, default=3e-4)
    parser.add_argument("--gamma", type=float, default=0.99)
    parser.add_argument("--gae-lam", type=float, default=0.95)
    parser.add_argument("--save", type=Path, default=CHECKPOINT_DIR / "marl_ppo_scheduler.pt")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    import torch

    from agent.inference.models.marl_ppo_scheduler import MARLPPOSchedulerNet
    from agent.training.battlefield_scheduling_env import BattlefieldSchedulingEnv

    rng = random.Random(args.seed)
    torch.manual_seed(args.seed)

    env = BattlefieldSchedulingEnv()
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = MARLPPOSchedulerNet(
        obs_dim=env.obs_dim,
        n_actions=env.n_actions,
        n_agents=env.n_agents,
    ).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=args.lr)

    phases = ["recon", "contact", "bda", "jammed"]
    args.save.parent.mkdir(parents=True, exist_ok=True)

    print(f"[train] device={device} obs_dim={env.obs_dim} n_actions={env.n_actions} n_agents={env.n_agents}")

    for epoch in range(1, args.epochs + 1):
        all_obs, all_act, all_logp, all_adv, all_ret = [], [], [], [], []
        epoch_rewards: list[float] = []

        for _ in range(args.rollouts_per_epoch):
            phase = rng.choice(phases)
            situation = _random_situation(rng, phase)
            rollout = collect_rollout(model, env, situation, device)
            values_np = rollout["values"].cpu().numpy()
            adv, ret = compute_gae(
                rollout["rewards"], values_np, rollout["dones"], args.gamma, args.gae_lam
            )
            all_obs.append(rollout["obs"])
            all_act.append(rollout["actions"])
            all_logp.append(rollout["log_probs"])
            all_adv.append(adv)
            all_ret.append(ret)
            epoch_rewards.append(float(rollout["rewards"].sum()))

        import numpy as np

        batch = {
            "obs": np.concatenate(all_obs, axis=0),
            "actions": np.concatenate(all_act, axis=0),
            "log_probs": torch.cat(all_logp, dim=0),
            "advantages": np.concatenate(all_adv, axis=0),
            "returns": np.concatenate(all_ret, axis=0),
        }
        metrics = ppo_update(model, optimizer, batch, device)

        if epoch % 20 == 0 or epoch == 1:
            avg_rew = sum(epoch_rewards) / max(len(epoch_rewards), 1)
            print(
                f"epoch {epoch:4d} | reward={avg_rew:.3f} | "
                f"loss={metrics['loss']:.4f} policy={metrics['policy_loss']:.4f} "
                f"value={metrics['value_loss']:.4f} ent={metrics['entropy']:.3f}"
            )

    torch.save(model.state_dict(), args.save)
    print(f"[OK] saved {args.save}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
