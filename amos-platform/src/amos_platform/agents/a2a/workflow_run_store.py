"""In-process records for AMOS-to-Commander workflow submissions.

The simulation keeps moving after a workflow is submitted.  These records
freeze the exact causal input summary sent at submission time so the operator
can distinguish the live scene from what Commander actually received.
"""

from __future__ import annotations

from copy import deepcopy
import json
import os
from threading import RLock
from typing import Any


class WorkflowRunStore:
    """Small bounded store of workflow submission snapshots."""

    def __init__(self, max_runs: int | None = None, max_bytes: int | None = None) -> None:
        self.max_runs = max(1, int(
            max_runs
            if max_runs is not None
            else os.environ.get("AMOS_WORKFLOW_RUN_CACHE_MAX_ITEMS", 32)
        ))
        self.max_bytes = max(1, int(
            max_bytes
            if max_bytes is not None
            else os.environ.get("AMOS_WORKFLOW_RUN_CACHE_MAX_BYTES", 32 * 1024 * 1024)
        ))
        self._lock = RLock()
        self._runs: dict[str, dict[str, Any]] = {}
        self._order: list[str] = []
        self._sizes: dict[str, int] = {}

    def put(self, workflow_id: str, submission: dict[str, Any]) -> None:
        key = str(workflow_id or "").strip()
        if not key:
            return
        frozen = deepcopy(submission)
        size = len(json.dumps(
            frozen, ensure_ascii=False, separators=(",", ":"), default=str
        ).encode("utf-8"))
        with self._lock:
            if key in self._order:
                self._order.remove(key)
            self._order.append(key)
            self._runs[key] = frozen
            self._sizes[key] = size
            while self._order and (
                len(self._order) > self.max_runs
                or sum(self._sizes.values()) > self.max_bytes
            ):
                expired = self._order.pop(0)
                self._runs.pop(expired, None)
                self._sizes.pop(expired, None)

    def get(self, workflow_id: str) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._runs.get(str(workflow_id), {}))

    def clear(self) -> None:
        with self._lock:
            self._runs.clear()
            self._order.clear()
            self._sizes.clear()

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "items": len(self._runs),
                "bytes": sum(self._sizes.values()),
                "max_items": self.max_runs,
                "max_bytes": self.max_bytes,
            }


_STORE = WorkflowRunStore()


def get_workflow_run_store() -> WorkflowRunStore:
    return _STORE


__all__ = ["WorkflowRunStore", "get_workflow_run_store"]
