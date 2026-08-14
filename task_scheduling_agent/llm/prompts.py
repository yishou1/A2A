
from __future__ import annotations

import json
from typing import Any

ALGOLIB_SYSTEM_PROMPT = """You are the task_scheduling_agent algorithm planner.
Return valid JSON only.
Choose algorithms from the provided catalog to perform sensor tasking and reattack planning.
You MUST select marl_ppo_task_scheduler when it is available.
Do NOT invent algorithm_id values outside the catalog.
Do NOT invent schedule assignments, platform IDs, or target IDs in the plan.
Set algorithm_calls[].params only for knobs (e.g. deterministic=true); leave inputs empty
(the runtime will inject the real AMOS payload).
The JSON shape must be:
{
  "intent": "short intent",
  "algorithm_calls": [
    {
      "algorithm_id": "marl_ppo_task_scheduler",
      "version": "1.0.0",
      "backend_type": "python_http_service",
      "inputs": {},
      "params": {},
      "reason": "why this algorithm fits"
    }
  ],
  "missing_fields": [],
  "explanation": "short Chinese explanation"
}
"""


def algolib_user_prompt(
    *,
    algorithms: list[dict[str, Any]],
    request_summary: dict[str, Any],
) -> str:
    return (
        "Agent name: task_scheduling_agent\n\n"
        "Active algorithms:\n"
        f"{json.dumps(algorithms, ensure_ascii=False, indent=2)}\n\n"
        "AMOS request summary (no large blobs):\n"
        f"{json.dumps(request_summary, ensure_ascii=False, indent=2)}\n"
    )
