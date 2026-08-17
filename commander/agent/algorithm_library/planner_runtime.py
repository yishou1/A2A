"""TIA 算法规划运行时：fixed / llm + 活跃目录校验。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Protocol

from agent.algorithm_library.catalog import (
    TIA_ALLOWED_ALGORITHMS,
    TIA_DEFAULT_PIPELINE,
    TIA_REQUIRED_ALGORITHMS,
    batch_context_summary,
    build_algorithm_catalog,
)
from agent.algorithm_library.client import AlgorithmLibraryClient, AlgorithmLibraryError
from agent.algorithm_library.endpoints import TIA_ALGORITHM_VERSIONS
from agent.models.schemas import SensorBatch
from tactical_intelligence_agent.llm.client import (
    LLMClientError,
    OpenAICompatibleClient,
    ToolLLMSettings,
)
from tactical_intelligence_agent.llm import prompts as tia_prompts


class ChatJSONClient(Protocol):
    def chat_json(self, *, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        ...


@dataclass
class AlgorithmCall:
    algorithm_id: str
    version: str = "1.0.0"
    backend_type: str = "python_http_service"
    params: dict[str, Any] = field(default_factory=dict)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "algorithm_id": self.algorithm_id,
            "version": self.version,
            "backend_type": self.backend_type,
            "inputs": {},
            "params": dict(self.params),
            "reason": self.reason,
        }


@dataclass
class AlgorithmPlan:
    mode: str
    intent: str
    algorithm_calls: list[AlgorithmCall]
    explanation: str = ""
    missing_fields: list[str] = field(default_factory=list)
    raw_llm_plan: dict[str, Any] | None = None
    fallback_reason: str = ""
    catalog_source: str = "local"

    @property
    def enabled_ids(self) -> set[str]:
        return {c.algorithm_id for c in self.algorithm_calls}

    def params_for(self, algorithm_id: str) -> dict[str, Any]:
        for call in self.algorithm_calls:
            if call.algorithm_id == algorithm_id:
                return dict(call.params)
        return {}

    def is_enabled(self, algorithm_id: str) -> bool:
        return algorithm_id in self.enabled_ids

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "intent": self.intent,
            "algorithm_calls": [c.to_dict() for c in self.algorithm_calls],
            "explanation": self.explanation,
            "missing_fields": list(self.missing_fields),
            "fallback_reason": self.fallback_reason,
            "raw_llm_plan": self.raw_llm_plan,
            "catalog_source": self.catalog_source,
        }


class AlgorithmPlannerError(ValueError):
    pass


def resolve_planner_mode(config: dict[str, Any] | None = None) -> str:
    env = os.environ.get("TIA_ALGORITHM_PLANNER", "").strip().lower()
    if env in {"fixed", "llm"}:
        return env
    planner_cfg = dict((config or {}).get("algorithm_planner") or {})
    mode = str(planner_cfg.get("mode") or "fixed").strip().lower()
    return "llm" if mode == "llm" else "fixed"


def plan_algorithms(
    batch: SensorBatch,
    *,
    config: dict[str, Any] | None = None,
    llm_client: ChatJSONClient | None = None,
    algolib_client: AlgorithmLibraryClient | None = None,
) -> AlgorithmPlan:
    cfg = config or {}
    mode = resolve_planner_mode(cfg)
    if mode != "llm":
        return _fixed_plan()

    planner_cfg = dict(cfg.get("algorithm_planner") or {})
    fallback = bool(planner_cfg.get("fallback_to_fixed", True))
    try:
        return _llm_plan(
            batch,
            config=cfg,
            llm_client=llm_client,
            algolib_client=algolib_client,
        )
    except (LLMClientError, AlgorithmPlannerError, AlgorithmLibraryError, ValueError, TypeError) as exc:
        if not fallback:
            raise
        plan = _fixed_plan()
        plan.fallback_reason = f"llm_plan_failed:{exc}"
        plan.explanation = f"LLM 规划失败，已降级固定管线：{exc}"
        return plan


def _fixed_plan() -> AlgorithmPlan:
    calls = [
        AlgorithmCall(
            algorithm_id=aid,
            version=TIA_ALGORITHM_VERSIONS.get(aid, "1.0.0"),
            reason="fixed default pipeline",
        )
        for aid in TIA_DEFAULT_PIPELINE
    ]
    return AlgorithmPlan(
        mode="fixed",
        intent="default_full_pipeline",
        algorithm_calls=calls,
        explanation="LLM disabled or planner=fixed; using full TIA pipeline.",
        catalog_source="local_default",
    )


def _load_active_catalog(
    config: dict[str, Any],
    algolib_client: AlgorithmLibraryClient | None,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], str]:
    """优先 GET /algorithms；失败则回退本地静态目录。"""
    lib = config.get("algorithm_library") or {}
    client = algolib_client or AlgorithmLibraryClient(lib)
    try:
        algorithms = client.list_algorithms(active_only=True)
        active_by_id = {
            str(item.get("algorithm_id")): item
            for item in algorithms
            if isinstance(item.get("algorithm_id"), str)
        }
        # 仅保留 TIA 白名单
        catalog = []
        for item in algorithms:
            aid = item.get("algorithm_id")
            if aid not in TIA_ALLOWED_ALGORITHMS:
                continue
            catalog.append(
                {
                    "algorithm_id": aid,
                    "version": item.get("version", "1.0.0"),
                    "backend_type": item.get("backend_type", "python_http_service"),
                    "task_family": item.get("task_family", ""),
                    "capabilities": item.get("capabilities", []),
                    "required_fields": (item.get("input_schema_summary") or {}).get("required", []),
                    "summary": (item.get("agent_card") or {}).get("summary")
                    or item.get("summary", ""),
                    "optional": item.get("optional", True),
                    "stage": item.get("stage", ""),
                }
            )
        if catalog:
            return catalog, active_by_id, "algolib:/algorithms"
    except AlgorithmLibraryError:
        pass

    host = str(lib.get("host") or "127.0.0.1")
    catalog = build_algorithm_catalog(host=host)
    active_by_id = {c["algorithm_id"]: c for c in catalog}
    return catalog, active_by_id, "local_fallback"


def _llm_plan(
    batch: SensorBatch,
    *,
    config: dict[str, Any],
    llm_client: ChatJSONClient | None,
    algolib_client: AlgorithmLibraryClient | None,
) -> AlgorithmPlan:
    settings = ToolLLMSettings.from_config(config)
    if not settings.enable and llm_client is None:
        raise AlgorithmPlannerError(
            "TIA_ALGORITHM_PLANNER=llm requires ENABLE_LLM=true (or inject llm_client)."
        )

    client: ChatJSONClient = llm_client or OpenAICompatibleClient(settings)
    catalog, active_by_id, catalog_source = _load_active_catalog(config, algolib_client)
    if not catalog:
        raise AlgorithmPlannerError("No active algorithms available from library or local catalog.")

    modalities = [
        f.modality.value if hasattr(f.modality, "value") else str(f.modality) for f in batch.frames
    ]
    summary = batch_context_summary(batch.context)

    raw = client.chat_json(
        system_prompt=tia_prompts.ALGOLIB_SYSTEM_PROMPT,
        user_prompt=tia_prompts.algolib_user_prompt(
            algorithms=catalog,
            batch_summary=summary,
            modalities=modalities,
        ),
    )
    calls = _normalize_and_validate_calls(raw, active_by_id=active_by_id)
    calls = _ensure_required_and_order(calls, active_by_id=active_by_id)
    return AlgorithmPlan(
        mode="llm",
        intent=str(raw.get("intent") or "llm_planned"),
        algorithm_calls=calls,
        explanation=str(raw.get("explanation") or ""),
        missing_fields=[str(x) for x in (raw.get("missing_fields") or []) if x],
        raw_llm_plan=raw if isinstance(raw, dict) else None,
        catalog_source=catalog_source,
    )


def _normalize_and_validate_calls(
    raw: dict[str, Any],
    *,
    active_by_id: dict[str, dict[str, Any]],
) -> list[AlgorithmCall]:
    items = raw.get("algorithm_calls")
    if not isinstance(items, list) or not items:
        raise AlgorithmPlannerError("LLM plan did not include algorithm_calls.")

    calls: list[AlgorithmCall] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise AlgorithmPlannerError("LLM algorithm call must be an object.")
        algorithm_id = str(item.get("algorithm_id") or "").strip()
        if not algorithm_id:
            raise AlgorithmPlannerError("algorithm_id is required.")
        if algorithm_id not in TIA_ALLOWED_ALGORITHMS:
            raise AlgorithmPlannerError(f"Algorithm is not allowed: {algorithm_id}")
        if algorithm_id not in active_by_id:
            raise AlgorithmLibraryError(f"Algorithm is not active: {algorithm_id}")
        if algorithm_id in seen:
            continue
        seen.add(algorithm_id)

        meta = active_by_id[algorithm_id]
        version = str(item.get("version") or meta.get("version") or TIA_ALGORITHM_VERSIONS.get(algorithm_id, "1.0.0"))
        backend = str(item.get("backend_type") or meta.get("backend_type") or "python_http_service")
        if str(meta.get("version")) and version != str(meta.get("version")):
            raise AlgorithmLibraryError(f"Algorithm version mismatch: {algorithm_id}:{version}")
        if str(meta.get("backend_type") or "python_http_service") != backend:
            raise AlgorithmLibraryError(f"Algorithm backend mismatch: {algorithm_id}:{backend}")

        params = item.get("params") if isinstance(item.get("params"), dict) else {}
        calls.append(
            AlgorithmCall(
                algorithm_id=algorithm_id,
                version=version,
                backend_type=backend,
                params=dict(params),
                reason=str(item.get("reason") or ""),
            )
        )
    return calls


def _ensure_required_and_order(
    calls: list[AlgorithmCall],
    *,
    active_by_id: dict[str, dict[str, Any]] | None = None,
) -> list[AlgorithmCall]:
    by_id = {c.algorithm_id: c for c in calls}
    active = active_by_id or {}
    for required in TIA_REQUIRED_ALGORITHMS:
        if required not in by_id:
            meta = active.get(required) or {}
            by_id[required] = AlgorithmCall(
                algorithm_id=required,
                version=str(meta.get("version") or TIA_ALGORITHM_VERSIONS.get(required, "1.0.0")),
                backend_type=str(meta.get("backend_type") or "python_http_service"),
                reason="auto-injected required algorithm",
            )
    ordered: list[AlgorithmCall] = []
    for aid in TIA_DEFAULT_PIPELINE:
        if aid in by_id:
            ordered.append(by_id[aid])
    known = {c.algorithm_id for c in ordered}
    for aid, call in by_id.items():
        if aid not in known:
            ordered.append(call)
    return ordered
