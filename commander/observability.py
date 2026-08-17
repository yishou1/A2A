from __future__ import annotations

import json
import os
import traceback
from copy import deepcopy
from typing import Any, Dict, Optional

from workflow_state_store import utc_now_iso


def exception_diagnostics(exc: BaseException, *, limit: Optional[int] = None) -> Dict[str, Any]:
    """Build a bounded, serializable server-side exception record."""
    if limit is None:
        limit = int(os.environ.get("A2A_TRACEBACK_MAX_CHARS", "20000"))
    formatted = "".join(
        traceback.TracebackException.from_exception(exc, capture_locals=False).format()
    )
    if limit > 0 and len(formatted) > limit:
        formatted = f"{formatted[:limit]}\n... traceback truncated ..."
    return {
        "error": str(exc),
        "error_type": type(exc).__name__,
        "traceback": formatted,
    }


def build_trace_event(event_type: str, **fields: Any) -> Dict[str, Any]:
    event = {
        "ts": utc_now_iso(),
        "event_type": event_type,
    }
    event.update({key: deepcopy(value) for key, value in fields.items() if value is not None})
    return event


def log_event(event_type: str, **fields: Any) -> Dict[str, Any]:
    event = build_trace_event(event_type, **fields)
    print(f"[A2A_EVENT] {json.dumps(event, ensure_ascii=False, sort_keys=True)}")
    return event


def append_trace(
    context: Dict[str, Any],
    event_type: str,
    *,
    limit: Optional[int] = 500,
    **fields: Any,
) -> Dict[str, Any]:
    event = build_trace_event(event_type, **fields)
    trace = list(context.get("trace", []))
    trace.append(event)
    if limit and len(trace) > limit:
        trace = trace[-limit:]
    context["trace"] = trace
    try:
        from opentelemetry import trace as otel_trace

        span = otel_trace.get_current_span()
        if span and span.is_recording():
            span.add_event(event_type, {
                key: value
                for key, value in fields.items()
                if isinstance(value, (str, bool, int, float))
            })
    except Exception:
        pass
    return event
