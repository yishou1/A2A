from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict
from uuid import uuid4


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_workflow_id(prefix: str = "workflow") -> str:
    return f"{prefix}-{uuid4().hex[:12]}"


class WorkflowStateStore:
    def __init__(self, base_dir: str):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def state_path(self, workflow_id: str) -> Path:
        return self.base_dir / f"{workflow_id}.json"

    def exists(self, workflow_id: str) -> bool:
        return self.state_path(workflow_id).exists()

    def load(self, workflow_id: str) -> Dict[str, Any]:
        path = self.state_path(workflow_id)
        with self._lock:
            with path.open("r", encoding="utf-8") as state_file:
                return json.load(state_file)

    def save(self, workflow_id: str, state: Dict[str, Any]) -> None:
        with self._lock:
            path = self.state_path(workflow_id)
            path.parent.mkdir(parents=True, exist_ok=True)

            payload = dict(state)
            payload["workflow_id"] = workflow_id
            payload.setdefault("created_at", utc_now_iso())
            payload["updated_at"] = utc_now_iso()

            tmp_path = path.with_suffix(path.suffix + ".tmp")
            with tmp_path.open("w", encoding="utf-8") as tmp_file:
                json.dump(payload, tmp_file, ensure_ascii=False, indent=2)
            os.replace(tmp_path, path)

    def delete(self, workflow_id: str) -> None:
        with self._lock:
            path = self.state_path(workflow_id)
            if path.exists():
                path.unlink()
