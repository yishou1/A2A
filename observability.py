from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Dict, Optional

from workflow_state_store import utc_now_iso


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
    return event
