"""Server-sent event helpers for operator-visible simulation streams."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from typing import Any


def format_sse_event(event: str, data: Any) -> str:
    """Format one server-sent event with JSON data."""
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {payload}\n\n"


def operator_state_event_stream(
    get_engine: Callable[[], Any],
    *,
    interval_sec: float = 0.5,
    heartbeat_sec: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.time,
) -> Iterator[str]:
    """Yield heartbeat and operator-visible sim_state SSE events."""
    engine = get_engine()
    last_push = 0.0
    was_running = bool(engine.clock.get("running"))

    while True:
        sleep(interval_sec)
        running = bool(engine.clock.get("running"))
        if not running:
            if was_running:
                yield format_sse_event("sim_state", engine.get_operator_state())
                last_push = now()
                was_running = False
                continue
            if now() - last_push > heartbeat_sec:
                yield format_sse_event("heartbeat", {"running": False})
                last_push = now()
            continue

        yield format_sse_event("sim_state", engine.get_operator_state())
        last_push = now()
        was_running = True


__all__ = ["format_sse_event", "operator_state_event_stream"]
