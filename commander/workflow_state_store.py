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
        self.retention_count = self._env_limit(
            "A2A_CHECKPOINT_RETENTION_COUNT", 128
        )
        self.retention_bytes = self._env_limit(
            "A2A_CHECKPOINT_RETENTION_BYTES", 1024 * 1024 * 1024
        )
        self._initialize_database()
        self._enforce_retention(protected_workflow_id=None)

    @staticmethod
    def _env_limit(name: str, default: int) -> int:
        try:
            return max(1, int(os.environ.get(name, default)))
        except (TypeError, ValueError):
            return max(1, int(default))

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
                    state_bytes INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            columns = {
                row[1] for row in connection.execute(
                    "PRAGMA table_info(workflow_checkpoints)"
                ).fetchall()
            }
            if "state_bytes" not in columns:
                connection.execute(
                    "ALTER TABLE workflow_checkpoints "
                    "ADD COLUMN state_bytes INTEGER NOT NULL DEFAULT 0"
                )
            connection.execute(
                "UPDATE workflow_checkpoints "
                "SET state_bytes=length(CAST(state_json AS BLOB)) "
                "WHERE state_bytes=0"
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
                        workflow_id, state_json, state_bytes, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(workflow_id) DO UPDATE SET
                        state_json=excluded.state_json,
                        state_bytes=excluded.state_bytes,
                        updated_at=excluded.updated_at
                    """,
                    (
                        workflow_id,
                        encoded,
                        len(encoded.encode("utf-8")),
                        payload["created_at"],
                        payload["updated_at"],
                    ),
                )

            tmp_path = path.with_suffix(path.suffix + f".{uuid4().hex}.tmp")
            with tmp_path.open("w", encoding="utf-8") as tmp_file:
                json.dump(payload, tmp_file, ensure_ascii=False, indent=2)
                tmp_file.flush()
                os.fsync(tmp_file.fileno())

            last_error = None
            replaced = False
            for attempt in range(8):
                try:
                    os.replace(tmp_path, path)
                    replaced = True
                    break
                except PermissionError as exc:
                    last_error = exc
                    time.sleep(0.05 * (attempt + 1))
            if replaced:
                self._enforce_retention(protected_workflow_id=workflow_id)
                return
            with contextlib.suppress(FileNotFoundError, PermissionError):
                tmp_path.unlink()
            raise last_error

    def _enforce_retention(self, *, protected_workflow_id: str | None) -> int:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT workflow_id, state_bytes FROM workflow_checkpoints "
                "ORDER BY updated_at DESC, workflow_id DESC"
            ).fetchall()
            kept_count = 0
            kept_bytes = 0
            expired: list[str] = []
            for workflow_id, encoded_bytes in rows:
                size = max(0, int(encoded_bytes or 0))
                keep = workflow_id == protected_workflow_id or (
                    kept_count < self.retention_count
                    and kept_bytes + size <= self.retention_bytes
                )
                if keep:
                    kept_count += 1
                    kept_bytes += size
                else:
                    expired.append(str(workflow_id))
            if expired:
                connection.executemany(
                    "DELETE FROM workflow_checkpoints WHERE workflow_id=?",
                    [(workflow_id,) for workflow_id in expired],
                )
        for workflow_id in expired:
            self.state_path(workflow_id).unlink(missing_ok=True)
        return len(expired)

    def stats(self) -> dict[str, int]:
        with self._lock, self._connect() as connection:
            count, encoded_bytes = connection.execute(
                "SELECT count(*), coalesce(sum(state_bytes), 0) "
                "FROM workflow_checkpoints"
            ).fetchone()
        return {
            "items": int(count or 0),
            "bytes": int(encoded_bytes or 0),
            "max_items": self.retention_count,
            "max_bytes": self.retention_bytes,
        }

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
