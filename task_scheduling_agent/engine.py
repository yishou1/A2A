"""调度引擎：本地 mock/MARL，algolib（小模型选算法 + /run）。"""

from __future__ import annotations

from typing import Any

from agent.skills.perception.schedule_adapter import scheduler_result_to_plan
from agent.training.battlefield_scheduling_env import BattlefieldSchedulingState
from task_scheduling_agent.amos_adapter import situation_from_amos


def mock_schedule_from_situation(situation: BattlefieldSchedulingState) -> dict[str, Any]:
    """与 TIA MARLPPOScheduler._mock_schedule 同策略，直接基于态势。"""
    targets = situation.targets
    sensors = situation.sensors
    sensor_assignments: list[dict[str, Any]] = []

    for i, sensor in enumerate(sensors):
        target_id = None
        priority = "idle"
        rationale = "无可用目标或平台不可用"
        if not sensor.available:
            rationale = f"平台不可用(load={sensor.load:.2f})"
        elif targets:
            sorted_targets = sorted(targets, key=lambda t: t.threat_score, reverse=True)
            t = sorted_targets[i % len(sorted_targets)]
            target_id = t.target_id
            priority = "high" if t.threat_score >= 0.6 else "normal"
            rationale = f"mock: threat={t.threat_score:.2f}"
        sensor_assignments.append(
            {
                "sensor_id": sensor.sensor_id,
                "target_id": target_id,
                "task": "surveillance",
                "priority": priority,
                "rationale": rationale,
            }
        )

    reattack_plan: list[dict[str, Any]] = []
    usable_assets = [a for a in situation.strike_assets if a.available and a.remaining_ammo > 0]
    asset_idx = 0
    for t in targets:
        if not t.needs_reattack:
            continue
        if asset_idx >= len(usable_assets):
            break
        asset = usable_assets[asset_idx]
        asset_idx += 1
        reattack_plan.append(
            {
                "asset_id": asset.asset_id,
                "target_id": t.target_id,
                "task": "reattack",
                "priority": "critical",
                "expected_damage": round(min(1.0, t.damage_score + 0.3), 3),
                "rationale": (
                    f"mock: 毁伤不足({t.damage_score:.2f}), ammo={asset.remaining_ammo:.2f}"
                ),
            }
        )

    return {
        "sensor_assignments": sensor_assignments,
        "reattack_plan": reattack_plan,
        "covered_targets": [s["target_id"] for s in sensor_assignments if s.get("target_id")],
        "reattack_targets": [r["target_id"] for r in reattack_plan],
        "algorithm": "mock-heuristic",
    }


def marl_schedule_from_situation(
    situation: BattlefieldSchedulingState,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from agent.inference.registry import get_marl_ppo_scheduler

    policy = get_marl_ppo_scheduler(config or {})
    return policy.schedule(situation, deterministic=True)


def run_schedule(
    amos_payload: dict[str, Any],
    *,
    use_mock: bool = True,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = config or {}
    from task_scheduling_agent.algolib_runtime import run_with_algolib, use_algolib_backend

    if use_algolib_backend(cfg):
        return run_with_algolib(amos_payload, config=cfg)

    situation = situation_from_amos(amos_payload)
    if use_mock:
        raw = mock_schedule_from_situation(situation)
    else:
        raw = marl_schedule_from_situation(situation, config=cfg)

    plan = scheduler_result_to_plan(raw)
    return {
        "mission_id": str(amos_payload.get("mission_id", "")),
        "phase": situation.phase,
        "jamming_level": situation.jamming_level,
        "n_targets": len(situation.targets),
        "n_sensors": len(situation.sensors),
        "n_strike_assets": len(situation.strike_assets),
        **raw,
        "task_schedule": plan.model_dump(mode="json"),
    }
