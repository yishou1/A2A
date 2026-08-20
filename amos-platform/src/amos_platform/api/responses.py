"""Shared API response helpers for future route modules."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def now_iso() -> str:
    """Return a UTC timestamp for API response envelopes."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ok(data: dict[str, Any] | None = None, elapsed_ms: int = 0) -> dict[str, Any]:
    """Return the standard successful API response shape."""
    return {
        "code": 200,
        "data": data or {},
        "elapsed_ms": elapsed_ms,
        "timestamp": now_iso(),
    }


def err(
    code: int,
    msg: str,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the standard error API response shape."""
    payload: dict[str, Any] = {"code": code, "error": msg, "timestamp": now_iso()}
    if data is not None:
        payload["data"] = data
    return payload
