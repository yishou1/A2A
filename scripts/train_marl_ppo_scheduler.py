"""Train and evaluate the repository MARL-PPO task scheduler deterministically.

The generated scenarios are synthetic reference fixtures. They validate the
RL training, artifact, and serving path; they are not operational evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CHECKPOINT_DIR = ROOT / "models" / "checkpoints"
DATASET_PATH = ROOT / "data" / "marl_ppo" / "reference_holdout.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def generate_situations(count: int, seed: int):
    from agent.training.battlefield_scheduling_env import (
        BattlefieldSchedulingState,
        SchedulingSensor,
        SchedulingTarget,
        StrikeAsset,
    )

    rng = random.Random(seed)
    phases = ["recon", "contact", "bda", "jammed"]
    target_classes = ["tank", "vehicle", "artillery", "person", "drone", "building"]
    sensor_modalities = ["eo_ir", "sar", "radar"]
    asset_types = ["artillery", "mlrs", "atgm", "missile"]
    situations = []
    for scenario_index in range(count):
        phase = phases[scenario_index % len(phases)]
        base_lat = 30.512 + rng.uniform(-0.02, 0.02)
        base_lon = 114.381 + rng.uniform(-0.02, 0.02)
        targets = []
        for index in range(rng.randint(2, 6)):
            damage = rng.uniform(0.0, 0.9)
            threat = rng.uniform(0.3, 1.0)
            needs_reattack = damage < (0.65 if phase == "bda" else 0.55) and threat > 0.4
            targets.append(
                SchedulingTarget(
                    target_id=f"T-{scenario_index:04d}-{index}",
                    threat_score=threat,
                    damage_score=damage,
                    confidence=rng.uniform(0.5, 0.98),
                    lat=base_lat + rng.uniform(-0.03, 0.03),
                    lon=base_lon + rng.uniform(-0.03, 0.03),
                    needs_reattack=needs_reattack,
                    class_name=rng.choice(target_classes),
                )
            )
        sensors = [
            SchedulingSensor(
                sensor_id=f"S-{scenario_index:04d}-{index}",
                modality=rng.choice(sensor_modalities),
                available=rng.random() > 0.08,
                load=rng.uniform(0.0, 0.65),
                lat=base_lat + rng.uniform(-0.015, 0.015),
                lon=base_lon + rng.uniform(-0.015, 0.015),
            )
            for index in range(rng.randint(2, 4))
        ]
        strikes = [
            StrikeAsset(
                asset_id=f"A-{scenario_index:04d}-{index}",
                asset_type=rng.choice(asset_types),
                available=rng.random() > 0.1,
                remaining_ammo=rng.uniform(0.2, 1.0),
            )
            for index in range(rng.randint(1, 3))
        ]
        situations.append(
            BattlefieldSchedulingState(
                targets=targets,
                sensors=sensors,
                strike_assets=strikes,
                jamming_level=rng.uniform(0.0, 0.85),
                phase=phase,
                base_lat=base_lat,
                base_lon=base_lon,
            )
        )
    return situations


def situation_from_dict(payload: dict[str, Any]):
    from agent.training.battlefield_scheduling_env import (
        BattlefieldSchedulingState,
        SchedulingSensor,
        SchedulingTarget,
        StrikeAsset,
    )

    return BattlefieldSchedulingState(
        targets=[SchedulingTarget(**item) for item in payload["targets"]],
        sensors=[SchedulingSensor(**item) for item in payload["sensors"]],
        strike_assets=[StrikeAsset(**item) for item in payload["strike_assets"]],
        jamming_level=float(payload["jamming_level"]),
        phase=str(payload["phase"]),
        base_lat=float(payload["base_lat"]),
        base_lon=float(payload["base_lon"]),
    )


def _active_agent_indices(env, situation) -> list[int]:
    sensor_indices = list(range(len(situation.sensors[: env.max_sensors])))
    strike_indices = [
        env.max_sensors + index
        for index in range(len(situation.strike_assets[: env.max_strike]))
    ]
    return sensor_indices + strike_indices


def _action_mask(env, situation, agent_index: int, used: set[int]) -> np.ndarray:
    valid_target_count = len(situation.targets[: env.max_targets])
    mask = np.asarray(
        [index <= valid_target_count and index not in used for index in range(env.n_actions)],
        dtype=np.bool_,
    )
    mask[0] = True
    if agent_index < env.max_sensors:
        resource = situation.sensors[agent_index]
        available = resource.available
    else:
        resource = situation.strike_assets[agent_index - env.max_sensors]
        available = resource.available and resource.remaining_ammo > 0
    if not available:
        mask[:] = False
        mask[0] = True
    return mask


def collect_batch(model, env, situations, device: str) -> dict[str, np.ndarray]:
    import torch

    observations: list[np.ndarray] = []
    actions: list[int] = []
    old_log_probs: list[float] = []
    rewards: list[float] = []
    old_values: list[float] = []
    valid_counts: list[int] = []
    action_masks: list[np.ndarray] = []
    for situation in situations:
        env.reset(situation)
        full_actions = [0] * env.n_agents
        scenario_records = []
        valid_target_count = len(situation.targets[: env.max_targets])
        used_by_role = {"sensor": set(), "strike": set()}
        for agent_index in _active_agent_indices(env, situation):
            obs = env.build_agent_obs(agent_index)
            obs_t = torch.tensor(obs, dtype=torch.float32, device=device).unsqueeze(0)
            logits, value = model(obs_t)
            role = "sensor" if agent_index < env.max_sensors else "strike"
            mask = _action_mask(env, situation, agent_index, used_by_role[role])
            logits = logits.masked_fill(
                ~torch.as_tensor(mask, dtype=torch.bool, device=device).unsqueeze(0),
                torch.finfo(logits.dtype).min,
            )
            dist = torch.distributions.Categorical(logits=logits)
            action = dist.sample()
            full_actions[agent_index] = int(action.item())
            if int(action.item()) > 0:
                used_by_role[role].add(int(action.item()))
            scenario_records.append(
                (obs, int(action.item()), float(dist.log_prob(action).item()), float(value.item()), mask)
            )
        _, scenario_rewards, _, _ = env.step(full_actions)
        for record, reward in zip(scenario_records, scenario_rewards):
            obs, action, log_prob, value, mask = record
            observations.append(obs)
            actions.append(action)
            old_log_probs.append(log_prob)
            rewards.append(float(reward))
            old_values.append(value)
            valid_counts.append(valid_target_count)
            action_masks.append(mask)
    return {
        "obs": np.asarray(observations, dtype=np.float32),
        "actions": np.asarray(actions, dtype=np.int64),
        "old_log_probs": np.asarray(old_log_probs, dtype=np.float32),
        "returns": np.asarray(rewards, dtype=np.float32),
        "advantages": np.asarray(rewards, dtype=np.float32) - np.asarray(old_values, dtype=np.float32),
        "valid_target_counts": np.asarray(valid_counts, dtype=np.int64),
        "action_masks": np.asarray(action_masks, dtype=np.bool_),
    }


def ppo_update(model, optimizer, batch, device: str, update_epochs: int = 4) -> dict[str, float]:
    import torch
    import torch.nn.functional as functional

    obs = torch.tensor(batch["obs"], dtype=torch.float32, device=device)
    actions = torch.tensor(batch["actions"], dtype=torch.long, device=device)
    old_log_probs = torch.tensor(batch["old_log_probs"], dtype=torch.float32, device=device)
    returns = torch.tensor(batch["returns"], dtype=torch.float32, device=device)
    advantages = torch.tensor(batch["advantages"], dtype=torch.float32, device=device)
    valid_counts = torch.tensor(batch["valid_target_counts"], dtype=torch.long, device=device)
    action_masks = torch.tensor(batch["action_masks"], dtype=torch.bool, device=device)
    advantages = (advantages - advantages.mean()) / (advantages.std(unbiased=False) + 1e-8)
    metrics: dict[str, float] = {}
    for _ in range(update_epochs):
        log_probs, values, entropy = model.evaluate_actions(obs, actions, valid_counts, action_masks)
        ratio = torch.exp(log_probs - old_log_probs)
        surrogate = torch.minimum(
            ratio * advantages,
            torch.clamp(ratio, 0.8, 1.2) * advantages,
        )
        policy_loss = -surrogate.mean()
        value_loss = functional.mse_loss(values, returns)
        loss = policy_loss + 0.5 * value_loss - 0.01 * entropy.mean()
        optimizer.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 0.5)
        optimizer.step()
        metrics = {
            "loss": float(loss.item()),
            "policy_loss": float(policy_loss.item()),
            "value_loss": float(value_loss.item()),
            "entropy": float(entropy.mean().item()),
        }
    return metrics


def _scenario_metrics(situation, info: dict[str, Any], rewards: list[float]) -> dict[str, float]:
    covered = set(info["covered_targets"])
    reattacked = set(info["reattack_targets"])
    high_threat = [target.target_id for target in situation.targets if target.threat_score >= 0.6]
    reattack_needed = [target.target_id for target in situation.targets if target.needs_reattack]
    return {
        "total_reward": float(sum(rewards)),
        "high_threat_coverage": sum(item in covered for item in high_threat) / max(len(high_threat), 1),
        "reattack_coverage": sum(item in reattacked for item in reattack_needed) / max(len(reattack_needed), 1),
    }


def evaluate_model(model, situations) -> dict[str, float]:
    from agent.training.battlefield_scheduling_env import BattlefieldSchedulingEnv

    env = BattlefieldSchedulingEnv()
    rows = []
    for situation in situations:
        env.reset(situation)
        full_actions = [0] * env.n_agents
        valid_target_count = len(situation.targets[: env.max_targets])
        used_by_role = {"sensor": set(), "strike": set()}
        for agent_index in _active_agent_indices(env, situation):
            obs = env.build_agent_obs(agent_index)
            role = "sensor" if agent_index < env.max_sensors else "strike"
            mask = _action_mask(env, situation, agent_index, used_by_role[role])
            selected, _, _ = model.act(
                obs,
                deterministic=True,
                valid_target_count=valid_target_count,
                action_mask=mask,
            )
            full_actions[agent_index] = selected[0]
            if selected[0] > 0:
                used_by_role[role].add(selected[0])
        _, rewards, _, info = env.step(full_actions)
        rows.append(_scenario_metrics(situation, info, rewards))
    return {key: round(float(np.mean([row[key] for row in rows])), 6) for key in rows[0]}


def evaluate_random_policy(situations, seed: int) -> dict[str, float]:
    from agent.training.battlefield_scheduling_env import BattlefieldSchedulingEnv

    rng = random.Random(seed)
    env = BattlefieldSchedulingEnv()
    rows = []
    for situation in situations:
        env.reset(situation)
        full_actions = [0] * env.n_agents
        used_by_role = {"sensor": set(), "strike": set()}
        for agent_index in _active_agent_indices(env, situation):
            role = "sensor" if agent_index < env.max_sensors else "strike"
            mask = _action_mask(env, situation, agent_index, used_by_role[role])
            choices = np.flatnonzero(mask).tolist()
            selected = rng.choice(choices)
            full_actions[agent_index] = selected
            if selected > 0:
                used_by_role[role].add(selected)
        _, rewards, _, info = env.step(full_actions)
        rows.append(_scenario_metrics(situation, info, rewards))
    return {key: round(float(np.mean([row[key] for row in rows])), 6) for key in rows[0]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Train deterministic MARL-PPO task scheduler")
    parser.add_argument("--epochs", type=int, default=240)
    parser.add_argument("--rollouts-per-epoch", type=int, default=24)
    parser.add_argument("--holdout-count", type=int, default=256)
    parser.add_argument("--learning-rate", type=float, default=3e-4)
    parser.add_argument("--seed", type=int, default=20260822)
    parser.add_argument("--holdout-seed", type=int, default=20260823)
    parser.add_argument(
        "--save",
        type=Path,
        default=CHECKPOINT_DIR / "marl_ppo_scheduler_s.safetensors",
    )
    args = parser.parse_args()

    import sklearn
    import torch
    from safetensors.torch import save_file

    from agent.inference.models.marl_ppo_scheduler import MARLPPOSchedulerNet
    from agent.training.battlefield_scheduling_env import BattlefieldSchedulingEnv

    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    torch.use_deterministic_algorithms(True)
    torch.set_num_threads(1)
    device = "cpu"
    env = BattlefieldSchedulingEnv()
    model = MARLPPOSchedulerNet(env.obs_dim, env.n_actions, env.n_agents).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=args.learning_rate)
    train_rng = random.Random(args.seed + 1)

    for epoch in range(1, args.epochs + 1):
        situations = generate_situations(args.rollouts_per_epoch, train_rng.randrange(2**31))
        batch = collect_batch(model, env, situations, device)
        metrics = ppo_update(model, optimizer, batch, device)
        if epoch == 1 or epoch % 40 == 0 or epoch == args.epochs:
            print(
                f"epoch={epoch:03d} loss={metrics['loss']:.4f} "
                f"policy={metrics['policy_loss']:.4f} value={metrics['value_loss']:.4f} "
                f"entropy={metrics['entropy']:.4f}"
            )

    holdout = generate_situations(args.holdout_count, args.holdout_seed)
    trained_metrics = evaluate_model(model, holdout)
    random_metrics = evaluate_random_policy(holdout, args.holdout_seed + 1)
    if trained_metrics["total_reward"] <= random_metrics["total_reward"]:
        raise RuntimeError(
            f"trained policy did not beat random baseline: {trained_metrics} <= {random_metrics}"
        )

    DATASET_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATASET_PATH.write_text(
        json.dumps([asdict(item) for item in holdout], ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    args.save.parent.mkdir(parents=True, exist_ok=True)
    state = {key: value.detach().cpu().contiguous() for key, value in model.state_dict().items()}
    save_file(state, str(args.save))

    metadata_path = args.save.with_suffix(".metadata.json")
    metadata = {
        "model_id": "marl_ppo_task_scheduler",
        "model_version": "1.0.0",
        "model_family": "parameter_shared_marl_ppo_actor_critic",
        "compute_profile": "small",
        "artifact_path": args.save.relative_to(ROOT).as_posix(),
        "artifact_sha256": sha256(args.save),
        "training_script": Path(__file__).resolve().relative_to(ROOT).as_posix(),
        "training_script_sha256": sha256(Path(__file__).resolve()),
        "dataset": {
            "path": DATASET_PATH.relative_to(ROOT).as_posix(),
            "sha256": sha256(DATASET_PATH),
            "source": "deterministic_synthetic_battlefield_scenario_generator",
            "holdout_seed": args.holdout_seed,
            "holdout_count": args.holdout_count,
            "limitation": "Synthetic reference scenarios only; not an operational scheduling benchmark.",
        },
        "architecture": {
            "observation_dimension": env.obs_dim,
            "action_count": env.n_actions,
            "maximum_agent_count": env.n_agents,
            "hidden_dimension": 128,
            "parameter_count": sum(parameter.numel() for parameter in model.parameters()),
        },
        "training": {
            "algorithm": "PPO",
            "epochs": args.epochs,
            "rollouts_per_epoch": args.rollouts_per_epoch,
            "update_epochs": 4,
            "optimizer": "Adam",
            "learning_rate": args.learning_rate,
            "clip_epsilon": 0.2,
            "value_loss_weight": 0.5,
            "entropy_weight": 0.01,
            "random_state": args.seed,
        },
        "evaluation": {
            "method": "independent_deterministic_synthetic_holdout",
            "trained_policy": trained_metrics,
            "random_policy_baseline": random_metrics,
            "reward_improvement": round(
                trained_metrics["total_reward"] - random_metrics["total_reward"], 6
            ),
        },
        "runtime_versions": {
            "torch": torch.__version__,
            "numpy": np.__version__,
            "scikit_learn": sklearn.__version__,
        },
    }
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(metadata["evaluation"], ensure_ascii=False, indent=2))
    print(f"saved={args.save} sha256={metadata['artifact_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
