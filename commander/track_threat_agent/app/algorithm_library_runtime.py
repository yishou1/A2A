"""Validated GPT planner and zsl algorithm-library runtime integration."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib import error, request

from .tool_llm import AzureToolLLM, ToolLLMError


TRACK_THREAT_ALGORITHM_ORDER = (
    "multimodal_feature_fuser",
    "trajectory_predictor",
    "graph_relation_reasoner",
    "threat_priority_random_forest",
)
TRACK_THREAT_ALGORITHM_IDS = frozenset(TRACK_THREAT_ALGORITHM_ORDER)

SKILL_ALGORITHM_DEFAULTS: dict[str, tuple[str, ...]] = {
    # The upstream CMS agent owns data association and track fusion.  This
    # agent consumes that stable track state and applies prediction/assessment.
    "trajectory_tracking": (),
    "trajectory_prediction": ("trajectory_predictor",),
    "threat_ranking": ("multimodal_feature_fuser", "threat_priority_random_forest"),
    "group_detection": ("graph_relation_reasoner",),
    "group_threat_ranking": (
        "graph_relation_reasoner",
        "multimodal_feature_fuser",
        "threat_priority_random_forest",
    ),
    "protected_asset_impact_analysis": ("multimodal_feature_fuser", "trajectory_predictor"),
    "track_threat_situation_analysis": TRACK_THREAT_ALGORITHM_ORDER,
}

SKILL_ALGORITHM_ALLOWLIST: dict[str, frozenset[str]] = {
    "trajectory_tracking": frozenset(),
    "trajectory_prediction": frozenset({"trajectory_predictor"}),
    "threat_ranking": frozenset({"multimodal_feature_fuser", "threat_priority_random_forest"}),
    "group_detection": frozenset({"graph_relation_reasoner"}),
    "group_threat_ranking": frozenset({
        "multimodal_feature_fuser",
        "graph_relation_reasoner",
        "threat_priority_random_forest",
    }),
    "protected_asset_impact_analysis": frozenset({"multimodal_feature_fuser", "trajectory_predictor"}),
    "track_threat_situation_analysis": TRACK_THREAT_ALGORITHM_IDS,
}


class AlgorithmLibraryError(RuntimeError):
    pass


@dataclass(frozen=True)
class AlgorithmLibrarySettings:
    enabled: bool
    required: bool
    base_url: str
    timeout_seconds: float
    llm_enabled: bool
    llm_required: bool
    llm_provider: str
    llm_endpoint: str
    llm_deployment: str
    llm_api_version: str
    llm_api_key: str
    llm_timeout_seconds: float

    @classmethod
    def from_env(cls) -> "AlgorithmLibrarySettings":
        provider = os.getenv("LLM_PROVIDER", "azure_openai").strip().lower()
        if provider in {"azure", "azure_openai"}:
            endpoint = os.getenv("AZURE_OPENAI_ENDPOINT") or os.getenv("TOOL_LLM_URL", "")
            deployment = os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT") or os.getenv(
                "TOOL_LLM_NAME", "gpt-4o-mini"
            )
            api_key = os.getenv("AZURE_OPENAI_API_KEY") or os.getenv("API_KEY", "")
        else:
            endpoint = os.getenv("TOOL_LLM_URL") or os.getenv("AZURE_OPENAI_ENDPOINT", "")
            deployment = os.getenv("TOOL_LLM_NAME") or os.getenv(
                "AZURE_OPENAI_CHAT_DEPLOYMENT", "gpt-4o-mini"
            )
            api_key = os.getenv("API_KEY") or os.getenv("AZURE_OPENAI_API_KEY", "")
        return cls(
            enabled=_env_bool("ALGORITHM_LIBRARY_ENABLED", False),
            required=_env_bool("ALGORITHM_LIBRARY_REQUIRED", False),
            base_url=os.getenv("ALGOLIB_BASE_URL", "http://127.0.0.1:8088").rstrip("/"),
            timeout_seconds=float(os.getenv("ALGOLIB_TIMEOUT_SECONDS", "10")),
            llm_enabled=_env_bool("ENABLE_LLM", False),
            llm_required=_env_bool("TOOL_LLM_REQUIRED", False),
            llm_provider=provider,
            llm_endpoint=endpoint.rstrip("/"),
            llm_deployment=deployment.strip(),
            llm_api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview"),
            llm_api_key=api_key.strip(),
            llm_timeout_seconds=float(os.getenv("LLM_TIMEOUT_SECONDS", "30")),
        )

    def public_status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "required": self.required,
            "base_url": self.base_url,
            "timeout_seconds": self.timeout_seconds,
            "llm_enabled": self.llm_enabled,
            "llm_required": self.llm_required,
            "llm_provider": self.llm_provider,
            "llm_deployment": self.llm_deployment,
            "llm_endpoint_configured": bool(self.llm_endpoint),
            "llm_api_key_configured": bool(self.llm_api_key),
        }


@dataclass(frozen=True)
class AlgorithmLibraryCall:
    algorithm_id: str
    version: str
    backend_type: str
    inputs: dict[str, Any]
    params: dict[str, Any]
    reason: str = ""


AlgorithmTransport = Callable[[str, str, dict[str, Any] | None, float], dict[str, Any]]


class AlgorithmLibraryClient:
    def __init__(
        self,
        settings: AlgorithmLibrarySettings,
        *,
        transport: AlgorithmTransport | None = None,
    ) -> None:
        self.base_url = settings.base_url.rstrip("/")
        self.timeout_seconds = settings.timeout_seconds
        self.transport = transport or _request_json

    def list_algorithms(self) -> list[dict[str, Any]]:
        payload = self.transport("GET", f"{self.base_url}/algorithms", None, self.timeout_seconds)
        algorithms = payload.get("algorithms")
        if not isinstance(algorithms, list):
            raise AlgorithmLibraryError("algorithm library response is missing algorithms")
        return [item for item in algorithms if isinstance(item, dict)]

    def run_algorithm(
        self,
        *,
        request_id: str,
        trace_id: str,
        call: AlgorithmLibraryCall,
    ) -> dict[str, Any]:
        payload = {
            "request_id": request_id,
            "trace_id": trace_id,
            "algorithm_id": call.algorithm_id,
            "version": call.version,
            "backend_type": call.backend_type,
            "inputs": call.inputs,
            "params": call.params,
        }
        return self.transport("POST", f"{self.base_url}/run", payload, self.timeout_seconds)


class TrackThreatAlgorithmRuntime:
    """Plan and execute only validated Track Threat algorithm-library calls."""

    def __init__(
        self,
        settings: AlgorithmLibrarySettings | None = None,
        *,
        client: Any | None = None,
        llm: Any | None = None,
    ) -> None:
        self.settings = settings or AlgorithmLibrarySettings.from_env()
        self.client = client or AlgorithmLibraryClient(self.settings)
        self.llm = llm
        if self.llm is None and self.settings.llm_enabled:
            self.llm = AzureToolLLM(self.settings)
        self._active_by_id: dict[str, dict[str, Any]] = {}
        self._planned_by_id: dict[str, AlgorithmLibraryCall] = {}
        self._trace = self._empty_trace()

    def begin_request(
        self,
        *,
        request_id: str,
        requested_skills: list[str],
        request_summary: dict[str, Any],
    ) -> list[AlgorithmLibraryCall]:
        self._trace = self._empty_trace()
        self._trace.update(
            request_id=request_id,
            requested_skills=list(requested_skills),
            enabled=self.settings.enabled,
        )
        self._planned_by_id = {}
        self._active_by_id = {}
        if not self.settings.enabled:
            self._trace["planner_mode"] = "local_only"
            self._trace["planner_fallback_reason"] = "algorithm_library_disabled"
            return []

        try:
            catalog = self.client.list_algorithms()
            self._active_by_id = self._validated_catalog(catalog)
        except Exception as exc:
            self._trace["planner_mode"] = "local_fallback"
            self._trace["planner_fallback_reason"] = f"algorithm_library_unavailable:{exc}"
            if self.settings.required:
                raise AlgorithmLibraryError(str(exc)) from exc
            return []

        allowed = self._allowed_for_skills(requested_skills)
        raw_calls: list[dict[str, Any]]
        if self.settings.llm_enabled:
            try:
                if self.llm is None:
                    raise ToolLLMError("tool LLM is not configured")
                llm_plan = self.llm.plan(
                    requested_skills=requested_skills,
                    algorithms=[
                        self._catalog_view(item)
                        for algorithm_id, item in self._active_by_id.items()
                        if algorithm_id in allowed
                    ],
                    request_summary=request_summary,
                )
                raw_calls = self._raw_llm_calls(llm_plan, allowed)
                raw_calls = self._complete_skill_coverage(raw_calls, requested_skills)
                self._trace["planner_mode"] = self._llm_planner_mode()
                self._trace["llm_plan"] = {
                    "intent": str(llm_plan.get("intent") or ""),
                    "explanation": str(llm_plan.get("explanation") or ""),
                }
            except Exception as exc:
                if self.settings.llm_required:
                    raise ToolLLMError(str(exc)) from exc
                raw_calls = self._deterministic_calls(requested_skills)
                self._trace["planner_mode"] = "deterministic_fallback"
                self._trace["planner_fallback_reason"] = str(exc)
        else:
            raw_calls = self._deterministic_calls(requested_skills)
            self._trace["planner_mode"] = "deterministic"

        calls = [self._normalize_and_validate(raw, allowed) for raw in raw_calls]
        self._planned_by_id = {call.algorithm_id: call for call in calls}
        self._trace["planned_algorithms"] = [call.algorithm_id for call in calls]
        return calls

    def should_run(self, algorithm_id: str) -> bool:
        return algorithm_id in self._planned_by_id

    def _llm_planner_mode(self) -> str:
        provider = str(self.settings.llm_provider or "llm").strip().lower()
        deployment = str(self.settings.llm_deployment or "chat").strip().lower()
        label = f"{provider}_{deployment}"
        return "".join(character if character.isalnum() else "_" for character in label).strip("_")

    def run(
        self,
        algorithm_id: str,
        *,
        inputs: dict[str, Any],
        params: dict[str, Any],
    ) -> dict[str, Any]:
        planned = self._planned_by_id.get(algorithm_id)
        if planned is None:
            raise AlgorithmLibraryError(f"algorithm was not planned: {algorithm_id}")
        call = AlgorithmLibraryCall(
            algorithm_id=planned.algorithm_id,
            version=planned.version,
            backend_type=planned.backend_type,
            inputs=inputs,
            params=params,
            reason=planned.reason,
        )
        started = time.perf_counter()
        try:
            result = self.client.run_algorithm(
                request_id=str(self._trace.get("request_id") or "track-threat-request"),
                trace_id=str(self._trace.get("request_id") or "track-threat-trace"),
                call=call,
            )
        except Exception as exc:
            self._record_execution(algorithm_id, False, started, error_message=str(exc))
            raise AlgorithmLibraryError(f"algorithm library run failed: {exc}") from exc
        if not result.get("ok", False):
            error_payload = result.get("error") if isinstance(result.get("error"), dict) else {}
            code = str(error_payload.get("code") or "UNKNOWN")
            message = str(error_payload.get("message") or "algorithm execution failed")
            self._record_execution(algorithm_id, False, started, error_message=f"{code}:{message}")
            raise AlgorithmLibraryError(f"{code}: {message}")
        outputs = result.get("outputs")
        if not isinstance(outputs, dict):
            self._record_execution(algorithm_id, False, started, error_message="missing outputs")
            raise AlgorithmLibraryError("algorithm library result is missing outputs")
        usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
        self._record_execution(
            algorithm_id,
            True,
            started,
            remote_latency_ms=float(usage.get("latency_ms", 0.0) or 0.0),
            request_id=result.get("request_id"),
            trace_id=result.get("trace_id"),
            version=result.get("version") or call.version,
            backend_type=call.backend_type,
            params=params,
            reason=call.reason,
            inputs=inputs,
            outputs=outputs,
            usage=usage,
        )
        return outputs

    def execution_trace(self) -> dict[str, Any]:
        return json.loads(json.dumps(self._trace, ensure_ascii=False))

    def record_local_fallback(self, algorithm_id: str, reason: str) -> None:
        self._trace.setdefault("local_fallbacks", []).append(
            {"algorithm_id": algorithm_id, "reason": reason}
        )

    def status(self) -> dict[str, Any]:
        return {
            **self.settings.public_status(),
            "planner_mode": self._trace.get("planner_mode", "not_started"),
            "active_algorithms": sorted(self._active_by_id),
            "planned_algorithms": list(self._trace.get("planned_algorithms", [])),
            "executed_algorithms": [
                item["algorithm_id"] for item in self._trace.get("executions", []) if item.get("ok")
            ],
            "fallback_reason": self._trace.get("planner_fallback_reason", ""),
        }

    def _validated_catalog(self, catalog: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        active: dict[str, dict[str, Any]] = {}
        for item in catalog:
            algorithm_id = str(item.get("algorithm_id") or "")
            if algorithm_id not in TRACK_THREAT_ALGORITHM_IDS:
                continue
            owner_scope = str(item.get("owner_scope") or item.get("metadata", {}).get("owner_scope") or "")
            if owner_scope and owner_scope != "track_threat_agent":
                continue
            backend_type = str(item.get("backend_type") or "python_http_service")
            if backend_type != "python_http_service":
                continue
            version = str(item.get("version") or "1.0.0")
            active[algorithm_id] = {
                **item,
                "algorithm_id": algorithm_id,
                "version": version,
                "backend_type": backend_type,
            }
        return active

    def _allowed_for_skills(self, skills: list[str]) -> frozenset[str]:
        allowed: set[str] = set()
        for skill in skills:
            allowed.update(SKILL_ALGORITHM_ALLOWLIST.get(skill, ()))
        return frozenset(allowed)

    def _deterministic_calls(self, skills: list[str]) -> list[dict[str, Any]]:
        selected: list[str] = []
        for skill in skills:
            selected.extend(SKILL_ALGORITHM_DEFAULTS.get(skill, ()))
        ordered = [item for item in TRACK_THREAT_ALGORITHM_ORDER if item in set(selected)]
        return [
            {
                "algorithm_id": algorithm_id,
                "version": self._active_by_id.get(algorithm_id, {}).get("version", "1.0.0"),
                "backend_type": "python_http_service",
                "reason": "deterministic Skill-to-algorithm fallback",
            }
            for algorithm_id in ordered
            if algorithm_id in self._active_by_id
        ]

    def _raw_llm_calls(
        self,
        plan: dict[str, Any],
        allowed: frozenset[str],
    ) -> list[dict[str, Any]]:
        raw_calls = plan.get("algorithm_calls")
        if not isinstance(raw_calls, list) or not raw_calls:
            raise ToolLLMError("LLM plan did not include algorithm_calls")
        normalized = []
        for item in raw_calls:
            if not isinstance(item, dict):
                raise ToolLLMError("LLM algorithm call must be an object")
            algorithm_id = str(item.get("algorithm_id") or "")
            if algorithm_id not in allowed:
                raise ToolLLMError(f"algorithm is not allowed for requested skills: {algorithm_id}")
            normalized.append(item)
        return normalized

    def _complete_skill_coverage(
        self,
        raw_calls: list[dict[str, Any]],
        skills: list[str],
    ) -> list[dict[str, Any]]:
        """Keep LLM selection flexible while enforcing each Skill's required stages."""
        selected_by_id = {
            str(item.get("algorithm_id") or ""): item
            for item in raw_calls
        }
        augmented: list[str] = []
        for required_call in self._deterministic_calls(skills):
            algorithm_id = str(required_call["algorithm_id"])
            if algorithm_id in selected_by_id:
                continue
            selected_by_id[algorithm_id] = required_call
            augmented.append(algorithm_id)
        self._trace["planner_augmented_algorithms"] = augmented
        return [
            selected_by_id[algorithm_id]
            for algorithm_id in TRACK_THREAT_ALGORITHM_ORDER
            if algorithm_id in selected_by_id
        ]

    def _normalize_and_validate(
        self,
        raw: dict[str, Any],
        allowed: frozenset[str],
    ) -> AlgorithmLibraryCall:
        algorithm_id = str(raw.get("algorithm_id") or "")
        if algorithm_id not in TRACK_THREAT_ALGORITHM_IDS or algorithm_id not in allowed:
            raise AlgorithmLibraryError(f"algorithm is not allowed: {algorithm_id}")
        active = self._active_by_id.get(algorithm_id)
        if active is None:
            raise AlgorithmLibraryError(f"algorithm is not active: {algorithm_id}")
        version = str(raw.get("version") or active["version"])
        backend_type = str(raw.get("backend_type") or active["backend_type"])
        if version != active["version"]:
            raise AlgorithmLibraryError(f"algorithm version mismatch: {algorithm_id}:{version}")
        if backend_type != active["backend_type"]:
            raise AlgorithmLibraryError(f"algorithm backend mismatch: {algorithm_id}:{backend_type}")
        return AlgorithmLibraryCall(
            algorithm_id=algorithm_id,
            version=version,
            backend_type=backend_type,
            inputs={},
            params={},
            reason=str(raw.get("reason") or ""),
        )

    @staticmethod
    def _catalog_view(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "algorithm_id": item.get("algorithm_id"),
            "version": item.get("version"),
            "backend_type": item.get("backend_type"),
            "task_family": item.get("task_family"),
            "capabilities": item.get("capabilities", []),
            "summary": (item.get("agent_card") or {}).get("summary", ""),
        }

    def _record_execution(
        self,
        algorithm_id: str,
        ok: bool,
        started: float,
        *,
        error_message: str = "",
        remote_latency_ms: float = 0.0,
        request_id: Any = None,
        trace_id: Any = None,
        version: Any = None,
        backend_type: Any = None,
        params: dict[str, Any] | None = None,
        reason: str = "",
        inputs: dict[str, Any] | None = None,
        outputs: dict[str, Any] | None = None,
        usage: dict[str, Any] | None = None,
    ) -> None:
        client_duration_ms = round((time.perf_counter() - started) * 1000.0, 3)
        duration_ms = round(remote_latency_ms, 3) if remote_latency_ms > 0 else client_duration_ms
        self._trace.setdefault("executions", []).append(
            {
                "algorithm_id": algorithm_id,
                "algorithm_name": algorithm_id,
                "ok": ok,
                "status": "completed" if ok else "failed",
                "version": version,
                "backend_type": backend_type or "python_http_service",
                "execution_mode": "algorithm_library",
                "request_id": request_id or self._trace.get("request_id"),
                "trace_id": trace_id or self._trace.get("request_id"),
                "params": params or {},
                "reason": reason,
                "input": inputs,
                "output": outputs,
                "usage": usage or {},
                "duration_ms": duration_ms,
                "latency_ms": duration_ms,
                "duration_source": "algorithm_usage" if remote_latency_ms > 0 else "client_measured",
                "client_duration_ms": client_duration_ms,
                "remote_latency_ms": round(remote_latency_ms, 3),
                "error": error_message,
            }
        )

    @staticmethod
    def _empty_trace() -> dict[str, Any]:
        return {
            "enabled": False,
            "request_id": "",
            "requested_skills": [],
            "planner_mode": "not_started",
            "planner_fallback_reason": "",
            "planner_augmented_algorithms": [],
            "planned_algorithms": [],
            "executions": [],
            "local_fallbacks": [],
        }


def _request_json(
    method: str,
    url: str,
    payload: dict[str, Any] | None,
    timeout_seconds: float,
) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:
            result = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise AlgorithmLibraryError(f"algorithm library HTTP {exc.code}: {detail}") from exc
    except (error.URLError, TimeoutError, OSError) as exc:
        raise AlgorithmLibraryError(f"algorithm library request failed: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise AlgorithmLibraryError("algorithm library returned invalid JSON") from exc
    if not isinstance(result, dict):
        raise AlgorithmLibraryError("algorithm library returned a non-object JSON value")
    return result


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}
