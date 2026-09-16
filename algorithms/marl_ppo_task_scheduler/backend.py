"""MARL-PPO 任务调度与资源分配：传感器任务调度 + 重攻击规划。

mock 路径纯启发式，不依赖 agent；真推理延迟导入 agent.inference.scheduling。
"""

from __future__ import annotations

from typing import Any

from algorithms.base import AlgorithmBackend


class MARLPPOScheduler(AlgorithmBackend[dict[str, Any]]):
    name = "MARL-PPO-Task-Scheduler"
    algorithm_id = "marl_ppo_task_scheduler"
    config_key = "marl_ppo_scheduler"

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
        # 独立 mock：不依赖 agent.training
        targets = list(tracks) or list(detections)
        sensors = [
            f
            for f in frames
            if isinstance(f, dict) and f.get("sensor_id")
        ]
        if not sensors:
            sensors = [{"sensor_id": sid} for sid in (batch_context.get("sensor_ids") or ["S-1", "S-2"])]

        def _threat(t: dict[str, Any]) -> float:
            return float(t.get("threat_score", t.get("confidence", 0.5)) or 0.5)

        def _damage(t: dict[str, Any]) -> float:
            return float(t.get("damage_score", 0.0) or 0.0)

        def _tid(t: dict[str, Any], i: int) -> str:
            return str(t.get("track_id") or t.get("target_id") or f"T-{i+1:04d}")

        sorted_targets = sorted(targets, key=_threat, reverse=True)
        sensor_assignments = []
        for i, sensor in enumerate(sensors):
            sid = str(sensor.get("sensor_id", f"S-{i+1}"))
            target_id = None
            priority = "idle"
            rationale = "无高威胁目标"
            if sorted_targets:
                t = sorted_targets[i % len(sorted_targets)]
                target_id = _tid(t, i)
                thr = _threat(t)
                priority = "high" if thr >= 0.6 else "normal"
                rationale = f"mock: threat={thr:.2f}"
            sensor_assignments.append(
                {
                    "sensor_id": sid,
                    "target_id": target_id,
                    "task": "surveillance",
                    "priority": priority,
                    "rationale": rationale,
                }
            )

        reattack_plan = []
        strike_assets = list(batch_context.get("strike_assets") or [{"asset_id": "A-1"}])
        for i, t in enumerate(sorted_targets):
            dmg = _damage(t)
            if dmg >= 0.7:
                continue
            asset = strike_assets[0] if strike_assets else None
            if asset is None:
                break
            reattack_plan.append(
                {
                    "asset_id": str(asset.get("asset_id", "A-1")),
                    "target_id": _tid(t, i),
                    "task": "reattack",
                    "priority": "critical",
                    "expected_damage": round(min(1.0, dmg + 0.3), 3),
                    "rationale": f"mock: 毁伤不足({dmg:.2f})",
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
