"""TIA 算法规划 Prompt。"""

from __future__ import annotations

import json
from typing import Any

ALGOLIB_SYSTEM_PROMPT = """You are the tactical_intelligence_agent algorithm planner.
Return valid JSON only.
Choose an ordered subset of active algorithms from the provided catalog.
The first three algorithm_calls MUST be these required algorithms in this exact order when active:
- battlefield_rtdetr_detector
- edl_evidential_verifier
- motr_neural_kalman_tracker
You MAY skip optional algorithms when context justifies it, for example:
- siamese_mask2former_damage when has_reference_frame is false
- synapse_rag_retriever when has_knowledge_base is false
- marl_dynamic_router / multimodal steps when budget is tight
Do NOT select marl_ppo_task_scheduler (scheduling is a separate agent).
Do NOT invent detections, tracks, boxes, URIs, or algorithm outputs.
Do NOT invent algorithm_id values outside the catalog.
Preserve pipeline order: perception algorithms before cognition before communication.
Set algorithm_calls[].params only for knobs (e.g. confidence_threshold); leave inputs empty.
The JSON shape must be:
{
  "intent": "short intent",
  "algorithm_calls": [
    {
      "algorithm_id": "battlefield_rtdetr_detector",
      "version": "1.0.0",
      "backend_type": "python_http_service",
      "inputs": {},
      "params": {},
      "reason": "why this algorithm fits"
    },
    {
      "algorithm_id": "edl_evidential_verifier",
      "version": "1.0.0",
      "backend_type": "python_http_service",
      "inputs": {},
      "params": {},
      "reason": "why this algorithm fits"
    },
    {
      "algorithm_id": "motr_neural_kalman_tracker",
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
    batch_summary: dict[str, Any],
    modalities: list[str],
) -> str:
    return (
        "Agent name: tactical_intelligence_agent\n\n"
        "Active algorithms:\n"
        f"{json.dumps(algorithms, ensure_ascii=False, indent=2)}\n\n"
        "Batch context summary (no raw images):\n"
        f"{json.dumps(batch_summary, ensure_ascii=False, indent=2)}\n\n"
        f"Frame modalities: {modalities}\n"
        "The three required algorithms listed in the system prompt must all appear before optional ones.\n"
    )


def validation_retry_prompt(*, required_algorithms: list[dict[str, Any]], error: str) -> str:
    """Ask the model to regenerate a valid minimal plan after a rejected response."""
    return (
        f"The previous plan was rejected: {error}\n"
        "Generate a new JSON plan with exactly the three required algorithm_calls below, "
        "in the listed order. Copy each version and backend_type from the active catalog. "
        "Do not omit, rename, or add any algorithm. Return only the JSON object with "
        "intent, algorithm_calls, missing_fields, and explanation.\n"
        f"Required active catalog:\n{json.dumps(required_algorithms, ensure_ascii=False, indent=2)}\n"
    )
