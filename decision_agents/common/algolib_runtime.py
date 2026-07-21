"""Algorithm-library backed execution for decision agents."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from decision_agents.common.algolib_client import (
    AlgorithmLibraryClient,
    AlgorithmLibraryError,
    AlgorithmRunCall,
)
from decision_agents.common.algorithm_registry import missing_required_fields
from decision_agents.common.config import get_settings
from decision_agents.common.llm_enhancer import llm_enabled
from decision_agents.common.prompt_loader import get_prompt_module
from decision_agents.common.schemas import AgentRequest, AgentResponse
from llm.client import LLMClientError, OpenAICompatibleClient


AGENT_DEFAULT_ALGORITHMS = {
    "decision_planning_agent": "decision_planning_core",
    "compliance_authorization_agent": "compliance_authorization_core",
}

AGENT_ALLOWED_ALGORITHMS = {
    "decision_planning_agent": {"decision_planning_core"},
    "compliance_authorization_agent": {"compliance_authorization_core"},
}

AGENT_REQUIRED_FIELDS = {
    "decision_planning_agent": ("scheduled_tasks", "resources"),
    "compliance_authorization_agent": ("candidate_plans", "authorization"),
}


def use_algolib_backend() -> bool:
    return get_settings().decision_agent_backend == "algolib"


def run_agent_with_algolib(agent_name: str, request: AgentRequest) -> AgentResponse:
    settings = get_settings()
    client = AlgorithmLibraryClient(settings)
    try:
        algorithms = client.list_algorithms()
    except AlgorithmLibraryError as exc:
        return _error_response(
            agent_name,
            "Algorithm library is unavailable.",
            [str(exc)],
            error_code="ALGORITHM_RUNTIME_ERROR",
        )

    try:
        call, llm_plan = _select_algorithm_call(agent_name, request, algorithms)
    except LLMClientError as exc:
        return AgentResponse(
            status="input_required",
            agent=agent_name,  # type: ignore[arg-type]
            error_code="LLM_PROVIDER_ERROR",
            summary="Could not build a valid algorithm-library call.",
            warnings=[f"algolib_plan_failed:{exc}"],
        )
    except (ValidationError, ValueError) as exc:
        return AgentResponse(
            status="input_required",
            agent=agent_name,  # type: ignore[arg-type]
            error_code="ALGORITHM_INPUT_ERROR",
            summary="Could not build a valid algorithm-library call.",
            warnings=[f"algolib_plan_failed:{exc}"],
        )
    except AlgorithmLibraryError as exc:
        return _error_response(
            agent_name,
            "Could not select an active algorithm-library implementation.",
            [f"algolib_plan_failed:{exc}"],
            error_code="ALGORITHM_RUNTIME_ERROR",
        )

    try:
        result = client.run_algorithm(
            request_id=request.request_id,
            trace_id=request.request_id,
            call=call,
        )
    except AlgorithmLibraryError as exc:
        return _error_response(
            agent_name,
            "Algorithm library execution failed.",
            [str(exc)],
            error_code="ALGORITHM_RUNTIME_ERROR",
        )

    if not result.get("ok", False):
        error = result.get("error") if isinstance(result.get("error"), dict) else {}
        return _error_response(
            agent_name,
            "Algorithm library returned an error.",
            [f"algolib_run_error:{error.get('code', 'UNKNOWN')}:{error.get('message', '')}"],
            error_code="ALGORITHM_RUNTIME_ERROR",
            selected_algorithms=[call.algorithm_id],
            result={"llm_plan": llm_plan, "algolib_result": result},
        )

    outputs = result.get("outputs") if isinstance(result.get("outputs"), dict) else {}
    response_result = {
        **outputs,
        "llm_plan": llm_plan,
        "algolib_result": {
            "algorithm_id": result.get("algorithm_id"),
            "version": result.get("version"),
            "usage": result.get("usage", {}),
        },
    }
    return AgentResponse(
        agent=agent_name,  # type: ignore[arg-type]
        selected_algorithms=[call.algorithm_id],
        result=response_result,
        rag_evidence=response_result.get("rag_evidence", response_result.get("evidence", [])),
        summary=_summary(agent_name, response_result),
        warnings=[],
    )


def _select_algorithm_call(
    agent_name: str,
    request: AgentRequest,
    algorithms: list[dict[str, Any]],
) -> tuple[AlgorithmRunCall, dict[str, Any]]:
    actual_missing_fields = missing_required_fields(
        request,
        AGENT_REQUIRED_FIELDS[agent_name],
    )
    if actual_missing_fields:
        raise ValueError(f"missing_fields:{','.join(actual_missing_fields)}")

    active_by_id = {
        item.get("algorithm_id"): item
        for item in algorithms
        if isinstance(item.get("algorithm_id"), str)
    }
    if llm_enabled():
        llm_plan = _llm_plan(agent_name, request, algorithms)
        calls = llm_plan.get("algorithm_calls")
        if not isinstance(calls, list) or not calls:
            raise ValueError("LLM plan did not include algorithm_calls.")
        raw_call = calls[0]
        if not isinstance(raw_call, dict):
            raise ValueError("LLM algorithm call must be an object.")
    else:
        default_id = AGENT_DEFAULT_ALGORITHMS[agent_name]
        algorithm = active_by_id.get(default_id)
        if not algorithm:
            raise AlgorithmLibraryError(f"Default algorithm is not active: {default_id}")
        raw_call = {
            "algorithm_id": default_id,
            "version": algorithm.get("version", "1.0.0"),
            "backend_type": algorithm.get("backend_type", "python_http_service"),
            "inputs": request.model_dump(mode="json"),
            "params": {},
            "reason": "LLM disabled; using the agent default algorithm.",
        }
        llm_plan = {
            "intent": "default_algorithm_call",
            "algorithm_calls": [raw_call],
            "missing_fields": [],
            "explanation": "LLM disabled; using default algorithm.",
        }

    raw_call = {
        **raw_call,
        "inputs": request.model_dump(mode="json"),
    }
    llm_plan = {
        **llm_plan,
        "algorithm_calls": [raw_call],
    }
    call = _normalize_call(raw_call)
    _validate_call(agent_name, call, active_by_id)
    return call, llm_plan


def _llm_plan(
    agent_name: str,
    request: AgentRequest,
    algorithms: list[dict[str, Any]],
) -> dict[str, Any]:
    settings = get_settings()
    model = request.algorithm_params.get("llm_model")
    if model is not None:
        model = str(model)
        if settings.tool_llm_allowed_models and model not in settings.tool_llm_allowed_models:
            raise LLMClientError(f"LLM model is not allowed: {model}")
    llm = OpenAICompatibleClient(settings, model=model)
    prompts = get_prompt_module(agent_name)
    request_view = _llm_request_view(request)
    algorithm_catalog = _llm_algorithm_catalog(agent_name, algorithms)
    return llm.chat_json(
        system_prompt=prompts.ALGOLIB_SYSTEM_PROMPT,
        user_prompt=prompts.algolib_user_prompt(
            request_json=request_view.model_dump_json(indent=2),
            algorithms=algorithm_catalog,
        ),
    )


def _llm_request_view(request: AgentRequest) -> AgentRequest:
    """Keep algorithm-selection context small; execution still uses the full request."""
    payload = request.model_dump(mode="json")
    payload["candidate_plans"] = [
        {
            "id": plan["id"],
            "name": plan["name"],
            "status": plan["status"],
            "target_ids": plan["target_ids"],
            "assigned_resources": plan["assigned_resources"],
            "score": plan["score"],
            "rationale": "",
            "assumptions": [],
            "risk_notes": [],
            "actions": [],
            "expected_effects": [],
        }
        for plan in payload.get("candidate_plans", [])
    ]
    payload["target_histories"] = [
        {
            "target_id": history["target_id"],
            "steps": history.get("steps", [])[-1:],
        }
        for history in payload.get("target_histories", [])
    ]
    return AgentRequest.model_validate(payload)


def _llm_algorithm_catalog(
    agent_name: str,
    algorithms: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    allowed = AGENT_ALLOWED_ALGORITHMS[agent_name]
    catalog = []
    for algorithm in algorithms:
        if algorithm.get("algorithm_id") not in allowed:
            continue
        input_summary = algorithm.get("input_schema_summary") or {}
        agent_card = algorithm.get("agent_card") or {}
        catalog.append(
            {
                "algorithm_id": algorithm.get("algorithm_id"),
                "version": algorithm.get("version"),
                "backend_type": algorithm.get("backend_type"),
                "task_family": algorithm.get("task_family"),
                "capabilities": algorithm.get("capabilities", []),
                "required_fields": input_summary.get("required", []),
                "summary": agent_card.get("summary", ""),
            }
        )
    return catalog


def _normalize_call(raw_call: dict[str, Any]) -> AlgorithmRunCall:
    inputs = raw_call.get("inputs")
    params = raw_call.get("params")
    return AlgorithmRunCall(
        algorithm_id=str(raw_call.get("algorithm_id") or ""),
        version=str(raw_call.get("version") or "1.0.0"),
        backend_type=str(raw_call.get("backend_type") or "python_http_service"),
        inputs=inputs if isinstance(inputs, dict) else {},
        params=params if isinstance(params, dict) else {},
        reason=str(raw_call.get("reason") or ""),
    )


def _validate_call(
    agent_name: str,
    call: AlgorithmRunCall,
    active_by_id: dict[str, dict[str, Any]],
) -> None:
    allowed = AGENT_ALLOWED_ALGORITHMS[agent_name]
    if call.algorithm_id not in allowed:
        raise AlgorithmLibraryError(f"Algorithm is not allowed for {agent_name}: {call.algorithm_id}")
    algorithm = active_by_id.get(call.algorithm_id)
    if not algorithm:
        raise AlgorithmLibraryError(f"Algorithm is not active: {call.algorithm_id}")
    if call.version != str(algorithm.get("version")):
        raise AlgorithmLibraryError(f"Algorithm version mismatch: {call.algorithm_id}:{call.version}")
    if call.backend_type != str(algorithm.get("backend_type")):
        raise AlgorithmLibraryError(
            f"Algorithm backend mismatch: {call.algorithm_id}:{call.backend_type}"
        )


def _error_response(
    agent_name: str,
    summary: str,
    warnings: list[str],
    *,
    error_code: str,
    selected_algorithms: list[str] | None = None,
    result: dict[str, Any] | None = None,
) -> AgentResponse:
    return AgentResponse(
        status="error",
        agent=agent_name,  # type: ignore[arg-type]
        error_code=error_code,
        selected_algorithms=selected_algorithms or [],
        result=result or {},
        summary=summary,
        warnings=warnings,
    )


def _summary(agent_name: str, result: dict[str, Any]) -> str:
    if agent_name == "decision_planning_agent":
        return (
            f"Generated {len(result.get('candidate_plans', []))} candidate plan(s); "
            f"recommended {result.get('recommended_plan_id', 'none')}."
        )
    return (
        f"Compliance decision is {result.get('decision', 'unknown')}; "
        f"human_approval_required={result.get('requires_human_approval')}."
    )
