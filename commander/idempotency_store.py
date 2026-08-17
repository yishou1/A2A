from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any

from workflow_state_store import utc_now_iso


class IdempotencyStore:
    def __init__(self, database_path: str | Path, namespace: str):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.namespace = namespace
        self._lock = threading.RLock()
        self._initialize()

    @contextmanager
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

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS idempotency_records (
                    namespace TEXT NOT NULL,
                    work_item TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(namespace, work_item)
                )
                """
            )

    def get(self, work_item: str) -> dict | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT response_json FROM idempotency_records "
                "WHERE namespace=? AND work_item=?",
                (self.namespace, work_item),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, work_item: str, response: dict[str, Any]) -> None:
        payload = json.dumps(deepcopy(response), ensure_ascii=False, separators=(",", ":"))
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO idempotency_records(namespace, work_item, response_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(namespace, work_item) DO UPDATE SET
                    response_json=excluded.response_json,
                    updated_at=excluded.updated_at
                """,
                (self.namespace, work_item, payload, utc_now_iso()),
            )

    def delete(self, work_item: str) -> bool:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM idempotency_records WHERE namespace=? AND work_item=?",
                (self.namespace, work_item),
            )
            return cursor.rowcount > 0

    def delete_workflow(self, workflow_id: str) -> int:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM idempotency_records WHERE namespace=? AND work_item LIKE ?",
                (self.namespace, f"{workflow_id}:%"),
            )
            return cursor.rowcount

    def clear(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "DELETE FROM idempotency_records WHERE namespace=?",
                (self.namespace,),
            )
