"""Small, input-free audit records for business LLM planning calls."""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def strict_llm_required() -> bool:
    return os.environ.get("A2A_LLM_STRICT", "").strip().lower() in {"1", "true", "yes", "on"}


def new_call_id() -> str:
    return f"llm-{uuid.uuid4().hex}"


def record_llm_call(
    *,
    workflow_id: str,
    llm_call_id: str,
    agent: str,
    phase: str,
    provider: str,
    model: str,
    response_model: str = "",
    success: bool,
    fallback_reason: str = "",
) -> dict[str, Any]:
    """Return workflow evidence and optionally append the same event to JSONL.

    Prompts, completions, credentials, and request payloads are deliberately
    excluded. One append write keeps each record intact across Agent processes.
    """
    event: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "workflow_id": str(workflow_id),
        "llm_call_id": llm_call_id,
        "agent": agent,
        "phase": phase,
        "provider": provider,
        "model": model,
        "response_model": response_model,
        "status": "success" if success else "failed",
        "success": success,
        "fallback_reason": fallback_reason,
    }
    logging.getLogger("a2a.llm_audit").info("LLM_AUDIT %s", json.dumps(event, ensure_ascii=False))
    path_value = os.environ.get("LLM_AUDIT_PATH", "").strip()
    if path_value:
        path = Path(path_value)
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
        try:
            os.write(descriptor, (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8"))
        finally:
            os.close(descriptor)
    return event
