"""Algolib-backed execution for execution_control_agent."""
from __future__ import annotations

import time
import uuid
from typing import Any

from algolib_bridge import AlgorithmLibraryClient, AlgorithmLibraryError, AlgolibSettings
from execution_control_agent.execution_control_core import extract_upstream_results, run_execution_control

ALGORITHM_ID = "execution_control_planner"
AGENT_BACKEND_ENV = "EXECUTION_CONTROL_BACKEND"


def use_execution_control_algolib() -> bool:
    return AlgolibSettings.load(agent_backend_env=AGENT_BACKEND_ENV).backend == "algolib"


def _wrap_planner_outputs(arguments: dict, outputs: dict, *, warnings: list[str] | None = None) -> dict:
    matched_rules = list(outputs.get("matched_rules") or [])
    latency_ms = float(outputs.get("latency_ms") or 0.0)
    output_data = {
        "phase": outputs.get("phase") or arguments.get("phase") or "strike",
        "situation": outputs.get("situation") or {},
        "matched_items": outputs.get("matched_items") or [],
        "commands": list(outputs.get("commands") or []),
        "tracks": list(outputs.get("tracks") or []),
        "coordination": outputs.get("coordination") or {"groups": []},
        "latency_ms": latency_ms,
        "matched_rules": matched_rules,
        "prediction_details": list(outputs.get("prediction_details") or []),
        "backend": "algolib",
        "algorithm_id": ALGORITHM_ID,
        "warnings": list(warnings or []),
    }
    return {
        "task_type": "execution_control",
        "input_data": arguments,
        "output_data": output_data,
        "accuracy": round(float(matched_rules[0]["confidence"]), 4) if matched_rules else 0.0,
        "latency": latency_ms / 1000.0 if latency_ms else 0.0,
    }


def run_execution_control_via_algolib(arguments: dict) -> dict:
    settings = AlgolibSettings.load(agent_backend_env=AGENT_BACKEND_ENV)
    client = AlgorithmLibraryClient(settings)
    phase = str(arguments.get("phase") or arguments.get("control_phase") or "strike").strip().lower()
    if phase not in {"strike", "assault"}:
        phase = "strike"
    results = extract_upstream_results(arguments)
    context = arguments.get("context") if isinstance(arguments.get("context"), dict) else {}
    request_id = str(arguments.get("request_id") or f"ec-{uuid.uuid4().hex[:10]}")
    start = time.perf_counter()
    outputs = client.run_outputs(
        algorithm_id=ALGORITHM_ID,
        inputs={
            "phase": phase,
            "results": results,
            "context": context,
        },
        request_id=request_id,
        trace_id=request_id,
    )
    if "latency_ms" not in outputs:
        outputs = dict(outputs)
        outputs["latency_ms"] = round((time.perf_counter() - start) * 1000.0, 3)
    return _wrap_planner_outputs(arguments, outputs)


def run_execution_control_with_backend(arguments: dict) -> dict:
    """Run EC via algolib when enabled; on failure optionally fall back to local."""
    settings = AlgolibSettings.load(agent_backend_env=AGENT_BACKEND_ENV)
    if settings.backend != "algolib":
        result = run_execution_control(arguments)
        if isinstance(result.get("output_data"), dict):
            result["output_data"].setdefault("backend", "local")
        return result

    try:
        return run_execution_control_via_algolib(arguments)
    except AlgorithmLibraryError as exc:
        if not settings.fallback_local:
            raise
        result = run_execution_control(arguments)
        output_data = result.setdefault("output_data", {})
        if isinstance(output_data, dict):
            warnings = list(output_data.get("warnings") or [])
            warnings.append(f"algolib_fallback:{exc}")
            output_data["warnings"] = warnings
            output_data["backend"] = "local_fallback"
        return result
