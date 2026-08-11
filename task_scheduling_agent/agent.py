"""独立任务调度 / 资源分配 Agent（AMOS JSON 输入）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from task_scheduling_agent.engine import run_schedule


class TaskSchedulingAgent:
    """读取 AMOS 态势 JSON，输出传感器分配与再攻击计划。"""

    name = "Task_Scheduling_Agent"
    description = (
        "Standalone MARL-PPO / heuristic task scheduling agent. "
        "Consumes AMOS JSON (tasks, platforms, battery, ammo, links, time windows)."
    )

    def __init__(self, *, use_mock: bool = True, config: dict[str, Any] | None = None):
        self.use_mock = use_mock
        self.config = config or {}

    def run(self, amos_payload: dict[str, Any]) -> dict[str, Any]:
        return run_schedule(amos_payload, use_mock=self.use_mock, config=self.config)

    def run_file(
        self,
        input_path: str | Path,
        *,
        output_path: str | Path | None = None,
    ) -> dict[str, Any]:
        path = Path(input_path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        result = self.run(payload)
        if output_path is not None:
            out = Path(output_path)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result
