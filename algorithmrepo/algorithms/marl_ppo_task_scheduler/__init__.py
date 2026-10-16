"""marl_ppo_task_scheduler — 独立算法入口（归属任务调度智能体）。"""

from __future__ import annotations

import os
from typing import Any

from algorithms.marl_ppo_task_scheduler.backend import MARLPPOScheduler

ALGORITHM_ID = "marl_ppo_task_scheduler"
CONFIG_KEY = "marl_ppo_scheduler"


def predict(
    inputs: dict[str, Any],
    *,
    use_mock: bool = True,
    config: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """独立调度。优先 amos_payload；否则用 tracks/detections 态势。"""
    cfg = dict(config or {})
    if params:
        cfg.update(params)

    amos_payload = inputs.get("amos_payload")
    if isinstance(amos_payload, dict) and amos_payload:
        from task_scheduling_agent.amos_adapter import situation_from_amos
        from task_scheduling_agent.engine import (
            marl_schedule_from_situation,
            mock_schedule_from_situation,
        )

        situation = situation_from_amos(amos_payload)
        env_mock = os.environ.get("TIA_USE_MOCK", "1").strip() not in {"0", "false", "False"}
        effective_mock = use_mock if use_mock is not None else env_mock
        if effective_mock:
            return mock_schedule_from_situation(situation)
        return marl_schedule_from_situation(situation, config={"inference": cfg})

    return MARLPPOScheduler(use_mock=use_mock, config=cfg).run(
        {
            "tracks": inputs.get("tracks") or [],
            "detections": inputs.get("detections") or [],
            "batch_context": inputs.get("batch_context") or {},
            "frames": inputs.get("frames") or [],
        }
    )


__all__ = ["ALGORITHM_ID", "CONFIG_KEY", "MARLPPOScheduler", "predict"]
