from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import threading
import time
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
        self.database_path = self.base_dir / "workflow_state.db"
        self._lock = threading.RLock()
        self._initialize_database()

    @contextlib.contextmanager
    def _connect(self):
        connection = sqlite3.connect(str(self.database_path), timeout=10)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA busy_timeout=10000")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize_database(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS workflow_checkpoints (
                    workflow_id TEXT PRIMARY KEY,
                    state_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def state_path(self, workflow_id: str) -> Path:
        return self.base_dir / f"{workflow_id}.json"

    def exists(self, workflow_id: str) -> bool:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM workflow_checkpoints WHERE workflow_id=?",
                (workflow_id,),
            ).fetchone()
        return bool(row) or self.state_path(workflow_id).exists()

    def load(self, workflow_id: str) -> Dict[str, Any]:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT state_json FROM workflow_checkpoints WHERE workflow_id=?",
                (workflow_id,),
            ).fetchone()
            if row:
                return json.loads(row[0])
            path = self.state_path(workflow_id)
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

            encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO workflow_checkpoints(
                        workflow_id, state_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?)
                    ON CONFLICT(workflow_id) DO UPDATE SET
                        state_json=excluded.state_json,
                        updated_at=excluded.updated_at
                    """,
                    (workflow_id, encoded, payload["created_at"], payload["updated_at"]),
                )

            tmp_path = path.with_suffix(path.suffix + f".{uuid4().hex}.tmp")
            with tmp_path.open("w", encoding="utf-8") as tmp_file:
                json.dump(payload, tmp_file, ensure_ascii=False, indent=2)
                tmp_file.flush()
                os.fsync(tmp_file.fileno())

            last_error = None
            for attempt in range(8):
                try:
                    os.replace(tmp_path, path)
                    return
                except PermissionError as exc:
                    last_error = exc
                    time.sleep(0.05 * (attempt + 1))
            with contextlib.suppress(FileNotFoundError, PermissionError):
                tmp_path.unlink()
            raise last_error

    def delete(self, workflow_id: str) -> None:
        with self._lock:
            with self._connect() as connection:
                connection.execute(
                    "DELETE FROM workflow_checkpoints WHERE workflow_id=?",
                    (workflow_id,),
                )
            path = self.state_path(workflow_id)
            if path.exists():
                path.unlink()
