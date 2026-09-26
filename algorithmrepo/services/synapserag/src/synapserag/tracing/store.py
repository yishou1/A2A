"""SQLite persistence for bounded retrieval traces."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


class RetrievalTraceStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def initialize(self) -> None:
        self._recover_if_malformed()
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS retrieval_runs (
                    trace_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL,
                    workflow_id TEXT,
                    task_id TEXT,
                    work_item_id TEXT,
                    agent_id TEXT,
                    purpose TEXT NOT NULL,
                    index_id TEXT,
                    status TEXT NOT NULL,
                    query_count INTEGER NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    duration_ms REAL,
                    trace_json TEXT NOT NULL
                )"""
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_retrieval_task ON retrieval_runs(task_id, started_at DESC)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_retrieval_workflow ON retrieval_runs(workflow_id, started_at DESC)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_retrieval_request ON retrieval_runs(request_id)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_retrieval_created ON retrieval_runs(started_at DESC)")

    def _recover_if_malformed(self) -> None:
        if not self.path.exists():
            return
        try:
            with self._connect() as connection:
                result = connection.execute("PRAGMA integrity_check").fetchone()
            if result and str(result[0]).lower() == "ok":
                return
        except sqlite3.DatabaseError:
            pass
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        backup = self.path.with_name(f"{self.path.name}.corrupt-{timestamp}")
        self.path.replace(backup)
        for suffix in ("-wal", "-shm"):
            sidecar = self.path.with_name(self.path.name + suffix)
            if sidecar.exists():
                sidecar.replace(backup.with_name(backup.name + suffix))

    def save(self, trace: Dict[str, Any]) -> None:
        serialized = json.dumps(trace, ensure_ascii=False, separators=(",", ":"))
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO retrieval_runs (
                    trace_id, request_id, workflow_id, task_id, work_item_id,
                    agent_id, purpose, index_id, status, query_count,
                    started_at, completed_at, duration_ms, trace_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(trace_id) DO UPDATE SET
                    status=excluded.status,
                    completed_at=excluded.completed_at,
                    duration_ms=excluded.duration_ms,
                    trace_json=excluded.trace_json""",
                (
                    trace["trace_id"], trace["request_id"], trace.get("workflow_id"),
                    trace.get("task_id"), trace.get("work_item_id"), trace.get("agent_id"),
                    trace["purpose"], trace.get("index_id"), trace["status"],
                    int(trace.get("query_count") or 0), trace["started_at"],
                    trace.get("completed_at"), trace.get("duration_ms"), serialized,
                ),
            )

    def get(self, trace_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT trace_json FROM retrieval_runs WHERE trace_id=?", (trace_id,)
            ).fetchone()
        return json.loads(row["trace_json"]) if row is not None else None

    def list(
        self,
        *,
        task_id: Optional[str] = None,
        workflow_id: Optional[str] = None,
        request_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        clauses = []
        values: List[Any] = []
        for column, value in (
            ("task_id", task_id),
            ("workflow_id", workflow_id),
            ("request_id", request_id),
        ):
            if value:
                clauses.append(f"{column}=?")
                values.append(value)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(max(1, min(int(limit), 100)))
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT trace_json FROM retrieval_runs"
                f"{where} ORDER BY started_at DESC LIMIT ?",
                values,
            ).fetchall()
        traces = []
        for row in rows:
            trace = json.loads(row["trace_json"])
            traces.append({
                key: trace.get(key)
                for key in (
                    "trace_id", "request_id", "workflow_id", "task_id", "work_item_id",
                    "agent_id", "purpose", "index_id", "status", "started_at",
                    "index_version", "completed_at", "duration_ms", "query_count", "warnings", "error",
                )
            })
        return traces

    def purge_older_than(self, retention_days: int) -> int:
        if retention_days <= 0:
            return 0
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM retrieval_runs WHERE started_at < ?", (cutoff,)
            )
        return cursor.rowcount
