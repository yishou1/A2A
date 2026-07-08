"""MARL-PPO 任务调度与资源分配：传感器任务调度 + 重攻击规划。"""

from __future__ import annotations

from typing import Any

from agent.skills.base import AlgorithmBackend
from agent.training.battlefield_scheduling_env import situation_from_perception


class MARLPPOScheduler(AlgorithmBackend[dict[str, Any]]):
    name = "MARL-PPO-Task-Scheduler"

    def run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        tracks = inputs.get("tracks") or []
        detections = inputs.get("detections") or []
        batch_context = inputs.get("batch_context") or {}
        frames = inputs.get("frames") or []

        if self.use_mock:
            return self._mock_schedule(tracks, detections, batch_context, frames)
        return self._infer(tracks, detections, batch_context, frames)

    def _mock_schedule(
        self,
        tracks: list[dict[str, Any]],
        detections: list[dict[str, Any]],
        batch_context: dict[str, Any],
        frames: list[dict[str, Any]],
    ) -> dict[str, Any]:
        situation = situation_from_perception(tracks, detections, batch_context, frames)
        sensor_assignments = []
        targets = situation.targets
        sensors = situation.sensors

        for i, sensor in enumerate(sensors):
            target_id = None
            priority = "idle"
            rationale = "无高威胁目标"
            if targets:
                sorted_targets = sorted(targets, key=lambda t: t.threat_score, reverse=True)
                assigned_idx = i % len(sorted_targets)
                t = sorted_targets[assigned_idx]
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

        reattack_plan = []
        for t in targets:
            if not t.needs_reattack:
                continue
            asset = situation.strike_assets[0] if situation.strike_assets else None
            if asset is None:
                break
            reattack_plan.append(
                {
                    "asset_id": asset.asset_id,
                    "target_id": t.target_id,
                    "task": "reattack",
                    "priority": "critical",
                    "expected_damage": round(min(1.0, t.damage_score + 0.3), 3),
                    "rationale": f"mock: 毁伤不足({t.damage_score:.2f})",
                }
            )

        return {
            "sensor_assignments": sensor_assignments,
            "reattack_plan": reattack_plan,
            "covered_targets": [s["target_id"] for s in sensor_assignments if s.get("target_id")],
            "reattack_targets": [r["target_id"] for r in reattack_plan],
            "algorithm": "mock-heuristic",
        }

    def _infer(
        self,
        tracks: list[dict[str, Any]],
        detections: list[dict[str, Any]],
        batch_context: dict[str, Any],
        frames: list[dict[str, Any]],
    ) -> dict[str, Any]:
        from agent.inference.scheduling import marl_ppo_schedule

        return marl_ppo_schedule(tracks, detections, batch_context, frames, self.config)
