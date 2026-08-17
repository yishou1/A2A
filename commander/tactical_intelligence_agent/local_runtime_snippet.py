"""A2A-main local_runtime 接入片段（合并时粘贴到 local_runtime.py）。"""

from __future__ import annotations

import time
from typing import Iterable


def tactical_intelligence_agent_card() -> dict[str, str]:
    return {
        "name": "Local_Tactical_Intelligence_Agent",
        "description": "Local tactical intelligence unit.",
        "role": "tactical_intelligence",
    }


def tactical_intelligence_message(payload: dict) -> str:
    command = payload.get("command", "process_intelligence")
    return f"Local tactical intelligence completed command={command}"


def tactical_intelligence_stream_events(payload: dict) -> list[dict]:
    command = payload.get("command", "process_intelligence")
    return [
        {
            "status": "Working",
            "progress": "10%",
            "stage": "perception",
            "message": "Perception: RT-DETR / Siamese-Mask2Former / MOTR+Kalman",
            "role": "tactical_intelligence",
            "work_item": payload.get("work_item"),
        },
        {
            "status": "Working",
            "progress": "45%",
            "stage": "cognition",
            "message": "Cognition: ImageBind / Mamba / SynapseRAG fusion",
            "role": "tactical_intelligence",
            "work_item": payload.get("work_item"),
        },
        {
            "status": "Working",
            "progress": "75%",
            "stage": "communication",
            "message": "Communication: Knowledge Semantic Comm / MARL routing",
            "role": "tactical_intelligence",
            "work_item": payload.get("work_item"),
        },
        {
            "status": "Completed",
            "progress": "100%",
            "stage": "done",
            "role": "tactical_intelligence",
            "work_item": payload.get("work_item"),
            "message": f"Local tactical intelligence completed command={command}",
        },
    ]


def simulate_local_stream(events: Iterable[dict]) -> Iterable[dict]:
    for event in events:
        time.sleep(0.05)
        yield event
