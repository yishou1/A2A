"""
链路（与 decision_agents/common/algolib_runtime.py / TIA planner 一致）：
  ENABLE_LLM → chat_json 选算法
  GET /algorithms → 白名单过滤
  POST /run → 执行（inputs 强制覆盖为真实 AMOS 派生输入）
  ENABLE_LLM=false → 默认 marl_ppo_task_scheduler
"""

from __future__ import annotations

import os
import uuid
from typing import Any

from agent.algorithm_library.client import (
    AlgorithmLibraryClient,
    AlgorithmLibraryError,
    AlgorithmRunCall,
)
from agent.algorithm_library.endpoints import TIA_ALGORITHM_VERSIONS
from agent.skills.perception.schedule_adapter import scheduler_result_to_plan
from task_scheduling_agent.amos_adapter import situation_from_amos
from task_scheduling_agent.llm import (
    LLMClientError,
    OpenAICompatibleClient,
    ToolLLMSettings,
)
from task_scheduling_agent.llm import prompts as sched_prompts

AGENT_NAME = "task_scheduling_agent"
DEFAULT_ALGORITHM = "marl_ppo_task_scheduler"
ALLOWED_ALGORITHMS: set[str] = {"marl_ppo_task_scheduler"}


def use_algolib_backend(config: dict[str, Any] | None = None) -> bool:
    env = os.environ.get("TASK_SCHEDULING_BACKEND", "").strip().lower()
    if env in {"algolib", "library", "algorithm_library"}:
        return True
    if env in {"local", "in_process", "mock"}:
        return False
    cfg = config or {}
    backend = str(cfg.get("backend") or cfg.get("execution_mode") or "").strip().lower()
    if backend in {"algolib", "algorithm_library"}:
        return True
    lib = cfg.get("algorithm_library") or {}
    if lib.get("enabled") is True and backend != "in_process":
        # 显式打开 algorithm_library 且 backend 未指定 local 时走 algolib
        if os.environ.get("TASK_SCHEDULING_USE_ALGOLIB", "").strip().lower() in {"1", "true", "yes"}:
            return True
    return backend == "algorithm_library"


def amos_request_summary(amos_payload: dict[str, Any]) -> dict[str, Any]:
    """给小模型的精简请求视图（对齐 lzh _llm_request_view）。"""
    tasks = amos_payload.get("tasks") or []
    platforms = amos_payload.get("platforms") or []
    return {
        "mission_id": amos_payload.get("mission_id"),
        "phase": amos_payload.get("phase"),
        "jamming_level": amos_payload.get("jamming_level", 0.0),
        "task_count": len(tasks) if isinstance(tasks, list) else 0,
        "platform_count": len(platforms) if isinstance(platforms, list) else 0,
        "task_ids": [
            str(t.get("target_id") or t.get("task_id") or "")
            for t in (tasks if isinstance(tasks, list) else [])[:8]
            if isinstance(t, dict)
        ],
        "platform_ids": [
            str(p.get("platform_id") or "")
            for p in (platforms if isinstance(platforms, list) else [])[:8]
            if isinstance(p, dict)
        ],
        "has_time_windows": any(
            isinstance(t, dict) and t.get("time_window") for t in (tasks if isinstance(tasks, list) else [])
        ),
    }


def amos_to_algolib_inputs(amos_payload: dict[str, Any]) -> dict[str, Any]:
    """AMOS JSON → marl_ppo_task_scheduler /predict 输入；并附带 amos_payload 供服务端直连适配。"""
    situation = situation_from_amos(amos_payload)
    tracks: list[dict[str, Any]] = []
    detections: list[dict[str, Any]] = []
    for t in situation.targets:
        tracks.append(
            {
                "track_id": t.target_id,
                "class_name": t.class_name,
                "confidence": t.confidence,
                "damage_score": t.damage_score,
                "geo": {"lat": t.lat, "lon": t.lon},
            }
        )
        detections.append(
            {
                "track_id": t.target_id,
                "class_name": t.class_name,
                "confidence": t.confidence,
                "damage_score": t.damage_score,
            }
        )
    frames: list[dict[str, Any]] = []
    for s in situation.sensors:
        frames.append(
            {
                "sensor_id": s.sensor_id,
                "modality": s.modality,
                "metadata": {
                    "platform_lat": s.lat,
                    "platform_lon": s.lon,
                    "available": s.available,
                    "load": s.load,
                },
            }
        )
    batch_context = {
        "phase": situation.phase,
        "jamming_level": situation.jamming_level,
        "base_lat": situation.base_lat,
        "base_lon": situation.base_lon,
        "battlefield_situation": {
            "blue_force": {
                "units": [
                    {
                        "unit_id": a.asset_id,
                        "type": a.asset_type,
                        "status": "active" if a.available else "down",
                        "strength": float(a.remaining_ammo) * 100.0,
                    }
                    for a in situation.strike_assets
                ]
            }
        },
    }
    return {
        "amos_payload": amos_payload,
        "tracks": tracks,
        "detections": detections,
        "frames": frames,
        "batch_context": batch_context,
    }


def build_local_scheduling_catalog(*, host: str = "127.0.0.1") -> list[dict[str, Any]]:
    from agent.algorithm_library.endpoints import TIA_ALGORITHM_PORTS

    port = TIA_ALGORITHM_PORTS.get(DEFAULT_ALGORITHM, 9024)
    return [
        {
            "algorithm_id": DEFAULT_ALGORITHM,
            "version": TIA_ALGORITHM_VERSIONS.get(DEFAULT_ALGORITHM, "1.0.0"),
            "backend_type": "python_http_service",
            "status": "active",
            "task_family": "task_scheduling",
            "capabilities": ["sensor_tasking", "reattack_planning", "resource_allocation"],
            "input_schema_summary": {"required": ["tracks", "batch_context"]},
            "agent_card": {"summary": "MARL-PPO 传感器任务调度与再攻击规划"},
            "summary": "MARL-PPO 传感器任务调度与再攻击规划",
            "optional": False,
            "stage": "planning",
            "predict_endpoint": f"http://{host}:{port}/predict",
        }
    ]


def run_with_algolib(
    amos_payload: dict[str, Any],
    *,
    config: dict[str, Any] | None = None,
    algolib_client: AlgorithmLibraryClient | None = None,
    llm_client: Any | None = None,
) -> dict[str, Any]:
    """小模型选算法 + 算法库 /run 执行，返回与本地 engine 兼容的调度结果。"""
    cfg = config or {}
    lib = dict(cfg.get("algorithm_library") or {})
    client = algolib_client or AlgorithmLibraryClient(lib)

    try:
        algorithms = client.list_algorithms(active_only=True)
    except AlgorithmLibraryError:
        algorithms = []

    catalog = _filter_catalog(algorithms)
    catalog_source = "algolib:/algorithms"
    if not catalog:
        host = str(lib.get("host") or "127.0.0.1")
        catalog = build_local_scheduling_catalog(host=host)
        catalog_source = "local_fallback"

    active_by_id = {str(item["algorithm_id"]): item for item in catalog}
    real_inputs = amos_to_algolib_inputs(amos_payload)

    try:
        call, llm_plan = _select_algorithm_call(
            amos_payload,
            catalog=catalog,
            active_by_id=active_by_id,
            real_inputs=real_inputs,
            config=cfg,
            llm_client=llm_client,
        )
    except (LLMClientError, AlgorithmLibraryError, ValueError) as exc:
        # 与 TIA 一致：规划失败时若允许降级则用默认算法
        fallback = bool((cfg.get("algorithm_planner") or {}).get("fallback_to_fixed", True))
        if not fallback:
            raise
        meta = active_by_id.get(DEFAULT_ALGORITHM) or catalog[0]
        call = AlgorithmRunCall(
            algorithm_id=str(meta.get("algorithm_id") or DEFAULT_ALGORITHM),
            version=str(meta.get("version") or "1.0.0"),
            backend_type=str(meta.get("backend_type") or "python_http_service"),
            inputs=real_inputs,
            params={},
            reason=f"plan_failed_fallback:{exc}",
        )
        llm_plan = {
            "intent": "fallback_default_algorithm",
            "algorithm_calls": [
                {
                    "algorithm_id": call.algorithm_id,
                    "version": call.version,
                    "backend_type": call.backend_type,
                    "inputs": {},
                    "params": {},
                    "reason": call.reason,
                }
            ],
            "missing_fields": [],
            "explanation": f"LLM/plan failed, fallback to default: {exc}",
            "fallback_reason": str(exc),
        }

    request_id = str(amos_payload.get("request_id") or f"sched-{uuid.uuid4().hex[:12]}")
    trace_id = str(amos_payload.get("trace_id") or os.environ.get("TIA_TRACE_ID") or request_id)

    outputs = client.predict(
        call.algorithm_id,
        call.inputs,
        params=call.params,
        request_id=request_id,
        trace_id=trace_id,
        version=call.version,
        backend_type=call.backend_type,
    )

    plan = scheduler_result_to_plan(outputs)
    situation = situation_from_amos(amos_payload)
    return {
        "mission_id": str(amos_payload.get("mission_id", "")),
        "phase": situation.phase,
        "jamming_level": situation.jamming_level,
        "n_targets": len(situation.targets),
        "n_sensors": len(situation.sensors),
        "n_strike_assets": len(situation.strike_assets),
        "sensor_assignments": outputs.get("sensor_assignments") or [],
        "reattack_plan": outputs.get("reattack_plan") or [],
        "covered_targets": outputs.get("covered_targets") or [],
        "reattack_targets": outputs.get("reattack_targets") or [],
        "algorithm": outputs.get("algorithm") or call.algorithm_id,
        "task_schedule": plan.model_dump(mode="json"),
        "selected_algorithms": [call.algorithm_id],
        "llm_plan": {
            **llm_plan,
            "catalog_source": catalog_source,
            "mode": "llm" if _llm_enabled(cfg, llm_client) else "fixed",
        },
        "algolib_result": {
            "algorithm_id": call.algorithm_id,
            "version": call.version,
            "backend_type": call.backend_type,
        },
    }


def _llm_enabled(config: dict[str, Any], llm_client: Any | None) -> bool:
    if llm_client is not None:
        return True
    settings = ToolLLMSettings.from_config(config)
    planner = str((config.get("algorithm_planner") or {}).get("mode") or "fixed").lower()
    env_planner = os.environ.get("TASK_SCHEDULING_ALGORITHM_PLANNER", "").strip().lower()
    if env_planner == "llm" or planner == "llm":
        return settings.enable
    # 与 lzh 一致：ENABLE_LLM=true 即启用规划
    return settings.enable


def _filter_catalog(algorithms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    catalog: list[dict[str, Any]] = []
    for item in algorithms:
        aid = item.get("algorithm_id")
        if aid not in ALLOWED_ALGORITHMS:
            continue
        catalog.append(item)
    return catalog


def _select_algorithm_call(
    amos_payload: dict[str, Any],
    *,
    catalog: list[dict[str, Any]],
    active_by_id: dict[str, dict[str, Any]],
    real_inputs: dict[str, Any],
    config: dict[str, Any],
    llm_client: Any | None,
) -> tuple[AlgorithmRunCall, dict[str, Any]]:
    if _llm_enabled(config, llm_client):
        llm_plan = _llm_plan(
            amos_payload,
            catalog=catalog,
            config=config,
            llm_client=llm_client,
        )
        calls = llm_plan.get("algorithm_calls")
        if not isinstance(calls, list) or not calls:
            raise ValueError("LLM plan did not include algorithm_calls.")
        raw_call = calls[0]
        if not isinstance(raw_call, dict):
            raise ValueError("LLM algorithm call must be an object.")
    else:
        meta = active_by_id.get(DEFAULT_ALGORITHM)
        if not meta:
            raise AlgorithmLibraryError(f"Default algorithm is not active: {DEFAULT_ALGORITHM}")
        raw_call = {
            "algorithm_id": DEFAULT_ALGORITHM,
            "version": meta.get("version", "1.0.0"),
            "backend_type": meta.get("backend_type", "python_http_service"),
            "inputs": {},
            "params": {},
            "reason": "LLM disabled; using the agent default algorithm.",
        }
        llm_plan = {
            "intent": "default_algorithm_call",
            "algorithm_calls": [raw_call],
            "missing_fields": [],
            "explanation": "LLM disabled; using default algorithm.",
        }

    # 强制覆盖 inputs，防止 LLM 幻觉改写 AMOS 态势
    raw_call = {**raw_call, "inputs": real_inputs}
    llm_plan = {**llm_plan, "algorithm_calls": [{**raw_call, "inputs": {}}]}
    call = _normalize_call(raw_call)
    _validate_call(call, active_by_id)
    return call, llm_plan


def _llm_plan(
    amos_payload: dict[str, Any],
    *,
    catalog: list[dict[str, Any]],
    config: dict[str, Any],
    llm_client: Any | None,
) -> dict[str, Any]:
    settings = ToolLLMSettings.from_config(config)
    if not settings.enable and llm_client is None:
        raise LLMClientError("ENABLE_LLM=true is required for LLM algorithm planning.")
    client = llm_client or OpenAICompatibleClient(settings)
    slim_catalog = []
    for algorithm in catalog:
        input_summary = algorithm.get("input_schema_summary") or {}
        agent_card = algorithm.get("agent_card") or {}
        slim_catalog.append(
            {
                "algorithm_id": algorithm.get("algorithm_id"),
                "version": algorithm.get("version"),
                "backend_type": algorithm.get("backend_type"),
                "task_family": algorithm.get("task_family"),
                "capabilities": algorithm.get("capabilities", []),
                "required_fields": input_summary.get("required", []),
                "summary": agent_card.get("summary") or algorithm.get("summary", ""),
            }
        )
    return client.chat_json(
        system_prompt=sched_prompts.ALGOLIB_SYSTEM_PROMPT,
        user_prompt=sched_prompts.algolib_user_prompt(
            algorithms=slim_catalog,
            request_summary=amos_request_summary(amos_payload),
        ),
    )


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


def _validate_call(call: AlgorithmRunCall, active_by_id: dict[str, dict[str, Any]]) -> None:
    if call.algorithm_id not in ALLOWED_ALGORITHMS:
        raise AlgorithmLibraryError(
            f"Algorithm is not allowed for {AGENT_NAME}: {call.algorithm_id}"
        )
    algorithm = active_by_id.get(call.algorithm_id)
    if not algorithm:
        raise AlgorithmLibraryError(f"Algorithm is not active: {call.algorithm_id}")
    expected_version = str(algorithm.get("version") or "1.0.0")
    if call.version != expected_version:
        raise AlgorithmLibraryError(
            f"Algorithm version mismatch: {call.algorithm_id}:{call.version}"
        )
    expected_backend = str(algorithm.get("backend_type") or "python_http_service")
    if call.backend_type != expected_backend:
        raise AlgorithmLibraryError(
            f"Algorithm backend mismatch: {call.algorithm_id}:{call.backend_type}"
        )
