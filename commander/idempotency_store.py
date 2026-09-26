from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from workflow_state_store import utc_now_iso


class IdempotencyStore:
    def __init__(self, database_path: str | Path, namespace: str):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.namespace = namespace
        self._lock = threading.RLock()
        self.retention_count = self._env_limit(
            "A2A_IDEMPOTENCY_RETENTION_COUNT", 256
        )
        self.retention_bytes = self._env_limit(
            "A2A_IDEMPOTENCY_RETENTION_BYTES", 64 * 1024 * 1024
        )
        self.retention_days = self._env_limit(
            "A2A_IDEMPOTENCY_RETENTION_DAYS", 14
        )
        self._initialize()
        with self._lock, self._connect() as connection:
            self._enforce_retention(connection, protected_work_item=None)

    @staticmethod
    def _env_limit(name: str, default: int) -> int:
        try:
            return max(1, int(os.environ.get(name, default)))
        except (TypeError, ValueError):
            return max(1, int(default))

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
                    response_bytes INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(namespace, work_item)
                )
                """
            )
            columns = {
                row[1] for row in connection.execute(
                    "PRAGMA table_info(idempotency_records)"
                ).fetchall()
            }
            if "response_bytes" not in columns:
                connection.execute(
                    "ALTER TABLE idempotency_records "
                    "ADD COLUMN response_bytes INTEGER NOT NULL DEFAULT 0"
                )
            connection.execute(
                "UPDATE idempotency_records "
                "SET response_bytes=length(CAST(response_json AS BLOB)) "
                "WHERE response_bytes=0"
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
                INSERT INTO idempotency_records(
                    namespace, work_item, response_json, response_bytes, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(namespace, work_item) DO UPDATE SET
                    response_json=excluded.response_json,
                    response_bytes=excluded.response_bytes,
                    updated_at=excluded.updated_at
                """,
                (
                    self.namespace,
                    work_item,
                    payload,
                    len(payload.encode("utf-8")),
                    utc_now_iso(),
                ),
            )
            self._enforce_retention(connection, protected_work_item=work_item)

    def _enforce_retention(
        self, connection: sqlite3.Connection, *, protected_work_item: str | None
    ) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self.retention_days)).isoformat()
        rows = connection.execute(
            "SELECT work_item, response_bytes, updated_at "
            "FROM idempotency_records WHERE namespace=? "
            "ORDER BY updated_at DESC, work_item DESC",
            (self.namespace,),
        ).fetchall()
        kept_count = 0
        kept_bytes = 0
        expired: list[str] = []
        for work_item, encoded_bytes, updated_at in rows:
            size = max(0, int(encoded_bytes or 0))
            current = protected_work_item is not None and (
                str(work_item) == str(protected_work_item)
            )
            within_age = str(updated_at or "") >= cutoff
            keep = current or (
                within_age
                and kept_count < self.retention_count
                and kept_bytes + size <= self.retention_bytes
            )
            if keep:
                kept_count += 1
                kept_bytes += size
            else:
                expired.append(str(work_item))
        if expired:
            connection.executemany(
                "DELETE FROM idempotency_records WHERE namespace=? AND work_item=?",
                [(self.namespace, work_item) for work_item in expired],
            )
        return len(expired)

    def stats(self) -> dict[str, int]:
        with self._lock, self._connect() as connection:
            count, encoded_bytes = connection.execute(
                "SELECT count(*), coalesce(sum(response_bytes), 0) "
                "FROM idempotency_records WHERE namespace=?",
                (self.namespace,),
            ).fetchone()
        return {
            "items": int(count or 0),
            "bytes": int(encoded_bytes or 0),
            "max_items": self.retention_count,
            "max_bytes": self.retention_bytes,
            "retention_days": self.retention_days,
        }

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
