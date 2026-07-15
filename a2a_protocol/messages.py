from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Optional

from protocol_contracts import PROTOCOL_VERSION


TERMINAL_SUCCESS_STATUSES = {"completed", "succeeded", "success", "accepted"}
TERMINAL_FAILURE_STATUSES = {"failed", "error", "rejected", "timeout"}


def normalize_status(status: Any, default: str = "completed") -> str:
    if status is None:
        return default
    return str(status).strip().lower() or default


def is_success_response(response: Dict[str, Any] | None) -> bool:
    if not response:
        return False
    status = normalize_status(response.get("status"))
    if status in TERMINAL_FAILURE_STATUSES:
        return False
    if response.get("error"):
        return False
    return status in TERMINAL_SUCCESS_STATUSES


def build_task_response(
    *,
    workflow_id: Optional[str],
    work_item: str,
    agent: str,
    role: str,
    command: Optional[str] = None,
    status: str = "completed",
    output: Optional[Dict[str, Any]] = None,
    metrics: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
    message: Optional[str] = None,
    work_list_size: Optional[int] = None,
    attempts: int = 1,
    cached: bool = False,
    error_code: Optional[str] = None,
    model_result: Optional[Dict[str, Any]] = None,
    log_id: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "schema_version": PROTOCOL_VERSION,
        "workflow_id": workflow_id,
        "work_item": work_item,
        "agent": agent,
        "role": role,
        "command": command,
        "status": normalize_status(status),
        "output": deepcopy(output or {}),
        "metrics": deepcopy(metrics or {}),
        "error": error,
        "message": message or "",
        "attempts": attempts,
        "cached": cached,
    }
    if error_code is not None:
        payload["error_code"] = error_code
    if model_result is not None:
        payload["model_result"] = deepcopy(model_result)
    if log_id is not None:
        payload["log_id"] = log_id
    if work_list_size is not None:
        payload["work_list_size"] = work_list_size
    if extra:
        payload.update(deepcopy(extra))
    return payload


def build_task_error_response(
    *,
    workflow_id: Optional[str],
    work_item: str,
    agent: str,
    role: str,
    command: Optional[str],
    error: str,
    error_code: Optional[str] = None,
    attempts: int = 1,
    metrics: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return build_task_response(
        workflow_id=workflow_id,
        work_item=work_item,
        agent=agent,
        role=role,
        command=command,
        status="failed",
        output={},
        metrics=metrics,
        error=error,
        error_code=error_code,
        message=error,
        attempts=attempts,
    )
