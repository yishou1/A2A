"""MARL-PPO 调度结果与下游 Agent/算法库数据契约转换。"""

from __future__ import annotations

from typing import Any

from agent.models.schemas import (
    ReattackAssignment,
    SensorAssignment,
    TaskSchedulePlan,
)


def scheduler_result_to_plan(result: dict[str, Any]) -> TaskSchedulePlan:
    sensor_assignments = [
        SensorAssignment(
            sensor_id=str(item.get("sensor_id", "")),
            target_id=item.get("target_id"),
            task=str(item.get("task", "surveillance")),
            priority=str(item.get("priority", "normal")),
            rationale=str(item.get("rationale", "")),
        )
        for item in result.get("sensor_assignments") or []
    ]
    reattack_plan = [
        ReattackAssignment(
            asset_id=str(item.get("asset_id", "")),
            target_id=str(item.get("target_id", "")),
            task=str(item.get("task", "reattack")),
            priority=str(item.get("priority", "critical")),
            expected_damage=item.get("expected_damage"),
            rationale=str(item.get("rationale", "")),
        )
        for item in result.get("reattack_plan") or []
    ]
    return TaskSchedulePlan(
        sensor_assignments=sensor_assignments,
        reattack_plan=reattack_plan,
        covered_targets=[str(t) for t in result.get("covered_targets") or []],
        reattack_targets=[str(t) for t in result.get("reattack_targets") or []],
        algorithm=str(result.get("algorithm", "")),
    )


def task_schedule_to_resource_allocation(schedule: TaskSchedulePlan) -> dict[str, Any]:
    """对齐 execution_control_planner / mission_feature_adapter 的 resource_allocation 块。"""
    strike_count = len(schedule.reattack_plan)
    sensor_count = len(schedule.sensor_assignments)
    readiness = 0.85 if sensor_count > 0 else 0.5
    if strike_count:
        readiness = max(0.45, readiness - 0.08 * strike_count)
    supply_pressure = min(1.0, 0.2 + 0.12 * strike_count)
    return {
        "output_data": {
            "readiness": round(readiness, 4),
            "supply_pressure": round(supply_pressure, 4),
            "sensor_assignments": [a.model_dump(mode="json") for a in schedule.sensor_assignments],
            "strike_assignments": [a.model_dump(mode="json") for a in schedule.reattack_plan],
            "covered_targets": list(schedule.covered_targets),
            "reattack_targets": list(schedule.reattack_targets),
            "source": "marl_ppo_task_scheduler",
            "algorithm": schedule.algorithm,
        }
    }
