"""In-process records for AMOS-to-Commander workflow submissions.

The simulation keeps moving after a workflow is submitted.  These records
freeze the exact causal input summary sent at submission time so the operator
can distinguish the live scene from what Commander actually received.
"""

from __future__ import annotations

from copy import deepcopy
from threading import RLock
from typing import Any


class WorkflowRunStore:
    """Small bounded store of workflow submission snapshots."""

    def __init__(self, max_runs: int = 100) -> None:
        self.max_runs = max(1, int(max_runs))
        self._lock = RLock()
        self._runs: dict[str, dict[str, Any]] = {}
        self._order: list[str] = []

    def put(self, workflow_id: str, submission: dict[str, Any]) -> None:
        key = str(workflow_id or "").strip()
        if not key:
            return
        with self._lock:
            if key in self._order:
                self._order.remove(key)
            self._order.append(key)
            self._runs[key] = deepcopy(submission)
            while len(self._order) > self.max_runs:
                expired = self._order.pop(0)
                self._runs.pop(expired, None)

    def get(self, workflow_id: str) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._runs.get(str(workflow_id), {}))

    def clear(self) -> None:
        with self._lock:
            self._runs.clear()
            self._order.clear()


_STORE = WorkflowRunStore()


def get_workflow_run_store() -> WorkflowRunStore:
    return _STORE


__all__ = ["WorkflowRunStore", "get_workflow_run_store"]
