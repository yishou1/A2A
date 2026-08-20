"""LLM-assisted algorithm selection for the zh algorithm-library bridge."""
from __future__ import annotations

import json
import re
from typing import Any, Optional, Sequence

import requests

from algolib_bridge.client import AlgorithmRunCall
from algolib_bridge.config import AlgolibSettings


class AlgolibLLMPlannerError(RuntimeError):
    pass


_THINK_BLOCK_RE = re.compile(r"<think\b[^>]*>.*?</think>", re.IGNORECASE | re.DOTALL)


class OpenAICompatiblePlannerClient:
    def __init__(self, settings: AlgolibSettings) -> None:
        if not settings.llm_base_url:
            raise AlgolibLLMPlannerError("ALGOLIB_LLM_BASE_URL or AZURE_OPENAI_ENDPOINT is required.")
        if not settings.llm_model:
            raise AlgolibLLMPlannerError("ALGOLIB_LLM_MODEL or AZURE_OPENAI_DEPLOYMENT is required.")
        if not settings.llm_api_key:
            raise AlgolibLLMPlannerError("AZURE_OPENAI_API_KEY or ALGOLIB_LLM_API_KEY is required.")
        self.settings = settings

    def chat_json(self, *, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        payload = {
            "model": self.settings.llm_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.settings.llm_temperature,
            "response_format": {"type": "json_object"},
        }
        url, headers = self._request_target()
        try:
            response = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=self.settings.llm_timeout_seconds,
            )
            response.raise_for_status()
            data = response.json()
            content = data["choices"][0]["message"]["content"]
        except (requests.RequestException, ValueError, KeyError, IndexError, TypeError) as exc:
            raise AlgolibLLMPlannerError(f"LLM request failed: {exc}") from exc
        return _loads_json_object(content)

    def _request_target(self) -> tuple[str, dict[str, str]]:
        headers = {"Content-Type": "application/json"}
        base_url = self.settings.llm_base_url.rstrip("/")
        if self._is_azure_provider():
            headers["api-key"] = self.settings.llm_api_key
            if "/chat/completions" in base_url:
                return base_url, headers
            url = (
                f"{base_url}/openai/deployments/{self.settings.llm_model}/chat/completions"
                f"?api-version={self.settings.llm_api_version}"
            )
            return url, headers
        headers["Authorization"] = f"Bearer {self.settings.llm_api_key}"
        return f"{base_url}/chat/completions", headers

    def _is_azure_provider(self) -> bool:
        return self.settings.llm_provider in {"azure", "azure_openai"} or ".openai.azure.com" in self.settings.llm_base_url


def plan_algorithm_call(
    *,
    settings: AlgolibSettings,
    algorithms: Sequence[dict[str, Any]],
    default_algorithm_id: str,
    allowed_algorithm_ids: Sequence[str],
    inputs: dict[str, Any],
    params: Optional[dict[str, Any]] = None,
    task: str = "algorithm_call",
    client: Optional[OpenAICompatiblePlannerClient] = None,
) -> tuple[AlgorithmRunCall, dict[str, Any]]:
    """Return a validated algorithm call.

    The LLM may choose the algorithm/version/backend/params, but execution always
    receives the caller's original structured inputs.
    """
    active = _active_by_id(algorithms)
    allowed = set(allowed_algorithm_ids)
    if not settings.enable_llm:
        algorithm = _require_active(default_algorithm_id, active)
        raw_call = {
            "algorithm_id": default_algorithm_id,
            "version": algorithm.get("version", settings.default_version),
            "backend_type": algorithm.get("backend_type", settings.default_backend_type),
            "inputs": inputs,
            "params": dict(params or {}),
            "reason": "LLM disabled; using default algorithm.",
        }
        return _normalize_and_validate(raw_call, active, allowed), {
            "intent": "default_algorithm_call",
            "algorithm_calls": [raw_call],
            "missing_fields": [],
            "explanation": raw_call["reason"],
        }

    planner = client or OpenAICompatiblePlannerClient(settings)
    catalog = _catalog(algorithms, allowed)
    payload = planner.chat_json(
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=_user_prompt(task=task, inputs=inputs, algorithms=catalog),
    )
    calls = payload.get("algorithm_calls")
    if not isinstance(calls, list) or not calls or not isinstance(calls[0], dict):
        raise AlgolibLLMPlannerError("LLM plan did not include algorithm_calls[0].")
    raw_call = {
        **calls[0],
        "inputs": inputs,
        "params": dict(calls[0].get("params") if isinstance(calls[0].get("params"), dict) else params or {}),
    }
    call = _normalize_and_validate(raw_call, active, allowed)
    plan = {
        **payload,
        "algorithm_calls": [{**raw_call, "inputs": {"_source": "caller_structured_inputs"}}],
    }
    return call, plan


def plan_algorithm_calls(
    *,
    settings: AlgolibSettings,
    algorithms: Sequence[dict[str, Any]],
    call_specs: Sequence[dict[str, Any]],
    task: str = "algorithm_stage",
    client: Optional[OpenAICompatiblePlannerClient] = None,
) -> tuple[list[AlgorithmRunCall], dict[str, Any]]:
    """Plan multiple algorithm calls in one LLM round-trip.

    Each spec should include:
      - task: logical task name
      - default_algorithm_id: fallback algorithm id
      - allowed_algorithm_ids: optional explicit allow-list for this task
      - inputs: structured inputs that will be supplied at execution time
      - params: optional parameters to preserve
    """
    active = _active_by_id(algorithms)

    if not settings.enable_llm:
        calls: list[AlgorithmRunCall] = []
        raw_calls: list[dict[str, Any]] = []
        for spec in call_specs:
            default_algorithm_id = str(spec.get("default_algorithm_id") or "")
            algorithm = _require_active(default_algorithm_id, active)
            raw_call = {
                "task": str(spec.get("task") or task),
                "algorithm_id": default_algorithm_id,
                "version": str(algorithm.get("version", settings.default_version)),
                "backend_type": str(algorithm.get("backend_type", settings.default_backend_type)),
                "inputs": spec.get("inputs") if isinstance(spec.get("inputs"), dict) else {},
                "params": dict(spec.get("params") if isinstance(spec.get("params"), dict) else {}),
                "reason": "LLM disabled; using default algorithm.",
            }
            call = _normalize_and_validate(raw_call, active, {default_algorithm_id})
            calls.append(call)
            raw_calls.append({**raw_call, "inputs": {"_source": "caller_structured_inputs"}})
        return calls, {
            "intent": "default_algorithm_stage",
            "task": task,
            "algorithm_calls": raw_calls,
            "missing_fields": [],
            "explanation": "LLM disabled; using default algorithms for the stage.",
            "mode": "fixed",
        }

    planner = client or OpenAICompatiblePlannerClient(settings)
    catalog = _catalog(algorithms, {str(spec.get("default_algorithm_id") or "") for spec in call_specs})
    payload = planner.chat_json(
        system_prompt=_SYSTEM_PROMPT_STAGE,
        user_prompt=_user_prompt_stage(task=task, call_specs=call_specs, algorithms=catalog),
    )
    raw_calls = payload.get("algorithm_calls")
    if not isinstance(raw_calls, list) or not raw_calls:
        raise AlgolibLLMPlannerError("LLM plan did not include algorithm_calls.")

    calls: list[AlgorithmRunCall] = []
    normalized_plan_calls: list[dict[str, Any]] = []
    for index, spec in enumerate(call_specs):
        default_algorithm_id = str(spec.get("default_algorithm_id") or "")
        algorithm = _require_active(default_algorithm_id, active)
        raw_item = raw_calls[index] if index < len(raw_calls) and isinstance(raw_calls[index], dict) else {}
        raw_call = {
            **raw_item,
            "task": str(raw_item.get("task") or spec.get("task") or f"task-{index}"),
            "algorithm_id": str(raw_item.get("algorithm_id") or default_algorithm_id),
            "version": str(raw_item.get("version") or algorithm.get("version", settings.default_version)),
            "backend_type": str(raw_item.get("backend_type") or algorithm.get("backend_type", settings.default_backend_type)),
            "inputs": spec.get("inputs") if isinstance(spec.get("inputs"), dict) else {},
            "params": dict(raw_item.get("params") if isinstance(raw_item.get("params"), dict) else spec.get("params") or {}),
            "reason": str(raw_item.get("reason") or spec.get("reason") or ""),
        }
        try:
            call = _normalize_and_validate(raw_call, active, {default_algorithm_id})
        except AlgolibLLMPlannerError as exc:
            raw_call = {
                "task": str(spec.get("task") or task),
                "algorithm_id": default_algorithm_id,
                "version": str(algorithm.get("version", settings.default_version)),
                "backend_type": str(algorithm.get("backend_type", settings.default_backend_type)),
                "inputs": spec.get("inputs") if isinstance(spec.get("inputs"), dict) else {},
                "params": dict(spec.get("params") if isinstance(spec.get("params"), dict) else {}),
                "reason": f"plan_failed_fallback:{exc}",
            }
            call = _normalize_and_validate(raw_call, active, {default_algorithm_id})
        calls.append(call)
        normalized_plan_calls.append({**raw_call, "inputs": {"_source": "caller_structured_inputs"}})

    plan = {
        **payload,
        "task": task,
        "algorithm_calls": normalized_plan_calls,
        "mode": "llm",
    }
    return calls, plan


_SYSTEM_PROMPT = (
    "You select one algorithm-library call for an A2A agent. "
    "Return only one JSON object. Use only algorithms from the catalog. "
    "Do not invent algorithm ids, versions, or backend types. "
    "If the default algorithm fits, choose it. "
    "Schema: {\"intent\": string, \"algorithm_calls\": [{\"algorithm_id\": string, "
    "\"version\": string, \"backend_type\": string, \"params\": object, \"reason\": string}], "
    "\"missing_fields\": array, \"explanation\": string}."
)

_SYSTEM_PROMPT_STAGE = (
    "You plan one closed-loop stage with multiple algorithm-library calls. "
    "Return only one JSON object. Use only algorithms from the catalog. "
    "Do not invent algorithm ids, versions, or backend types. "
    "For each requested task, return one algorithm call in the same order. "
    "Schema: {\"intent\": string, \"algorithm_calls\": [{\"task\": string, \"algorithm_id\": string, "
    "\"version\": string, \"backend_type\": string, \"params\": object, \"reason\": string}], "
    "\"missing_fields\": array, \"explanation\": string}."
)


def _user_prompt(*, task: str, inputs: dict[str, Any], algorithms: list[dict[str, Any]]) -> str:
    preview = json.dumps(inputs, ensure_ascii=False, default=str)
    if len(preview) > 6000:
        preview = preview[:6000] + "...<truncated>"
    return json.dumps(
        {
            "task": task,
            "algorithm_catalog": algorithms,
            "inputs_preview": preview,
        },
        ensure_ascii=False,
    )


def _user_prompt_stage(
    *,
    task: str,
    call_specs: Sequence[dict[str, Any]],
    algorithms: list[dict[str, Any]],
) -> str:
    payload = {
        "task": task,
        "requested_calls": [
            {
                "task": str(spec.get("task") or ""),
                "default_algorithm_id": str(spec.get("default_algorithm_id") or ""),
                "description": str(spec.get("description") or ""),
                "inputs_preview": json.dumps(spec.get("inputs") or {}, ensure_ascii=False, default=str)[:2000],
            }
            for spec in call_specs
        ],
        "algorithm_catalog": algorithms,
    }
    return json.dumps(payload, ensure_ascii=False)


def _loads_json_object(content: str) -> dict[str, Any]:
    text = _THINK_BLOCK_RE.sub("", str(content)).strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AlgolibLLMPlannerError("LLM content is not valid JSON.") from exc
    if not isinstance(parsed, dict):
        raise AlgolibLLMPlannerError("LLM JSON content must be an object.")
    return parsed


def _active_by_id(algorithms: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("algorithm_id")): item
        for item in algorithms
        if isinstance(item, dict) and isinstance(item.get("algorithm_id"), str)
    }


def _require_active(algorithm_id: str, active: dict[str, dict[str, Any]]) -> dict[str, Any]:
    algorithm = active.get(algorithm_id)
    if not algorithm:
        raise AlgolibLLMPlannerError(f"Algorithm is not active: {algorithm_id}")
    return algorithm


def _catalog(algorithms: Sequence[dict[str, Any]], allowed: set[str]) -> list[dict[str, Any]]:
    catalog = []
    for algorithm in algorithms:
        algorithm_id = algorithm.get("algorithm_id")
        if algorithm_id not in allowed:
            continue
        summary = algorithm.get("input_schema_summary") if isinstance(algorithm.get("input_schema_summary"), dict) else {}
        card = algorithm.get("agent_card") if isinstance(algorithm.get("agent_card"), dict) else {}
        catalog.append(
            {
                "algorithm_id": algorithm_id,
                "version": algorithm.get("version"),
                "backend_type": algorithm.get("backend_type"),
                "task_family": algorithm.get("task_family"),
                "capabilities": algorithm.get("capabilities", []),
                "required_fields": summary.get("required", []),
                "summary": card.get("summary", ""),
            }
        )
    return catalog


def _normalize_and_validate(
    raw_call: dict[str, Any],
    active: dict[str, dict[str, Any]],
    allowed: set[str],
) -> AlgorithmRunCall:
    call = AlgorithmRunCall(
        algorithm_id=str(raw_call.get("algorithm_id") or ""),
        version=str(raw_call.get("version") or "1.0.0"),
        backend_type=str(raw_call.get("backend_type") or "python_http_service"),
        inputs=raw_call.get("inputs") if isinstance(raw_call.get("inputs"), dict) else {},
        params=raw_call.get("params") if isinstance(raw_call.get("params"), dict) else {},
        reason=str(raw_call.get("reason") or ""),
    )
    if call.algorithm_id not in allowed:
        raise AlgolibLLMPlannerError(f"Algorithm is not allowed: {call.algorithm_id}")
    algorithm = _require_active(call.algorithm_id, active)
    if call.version != str(algorithm.get("version")):
        raise AlgolibLLMPlannerError(f"Algorithm version mismatch: {call.algorithm_id}:{call.version}")
    if call.backend_type != str(algorithm.get("backend_type")):
        raise AlgolibLLMPlannerError(f"Algorithm backend mismatch: {call.algorithm_id}:{call.backend_type}")
    return call
