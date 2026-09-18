from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import threading
from typing import Any


TERMINAL_STATES = {"completed", "failed", "aborted"}
ALLOWED_TRANSITIONS = {
    "reserved": {"ready", "aborted", "failed"},
    "ready": {"synchronized", "aborted", "failed"},
    "synchronized": {"executing", "aborted", "failed"},
    "executing": {"completed", "failed", "aborted"},
    "completed": set(),
    "failed": set(),
    "aborted": set(),
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ExecutionStateError(ValueError):
    """Raised when an execution state transition violates the contract."""


class ExecutionStateStore:
    """Durable per-Agent execution state and append-only evidence audit."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS execution_sessions (
                    coordination_id TEXT NOT NULL,
                    slot_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    authorization_ref TEXT NOT NULL,
                    planned_start_at TEXT,
                    participant_ids_json TEXT NOT NULL,
                    assignment_json TEXT NOT NULL,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (coordination_id, slot_id)
                );
                CREATE TABLE IF NOT EXISTS execution_events (
                    event_id TEXT PRIMARY KEY,
                    coordination_id TEXT NOT NULL,
                    slot_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    source TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    received_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS execution_audit (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    coordination_id TEXT NOT NULL,
                    slot_id TEXT NOT NULL,
                    from_status TEXT,
                    to_status TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                );
                """
            )

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        value = dict(row)
        value["participant_ids"] = json.loads(value.pop("participant_ids_json"))
        value["assignment"] = json.loads(value.pop("assignment_json"))
        return value

    def get(self, coordination_id: str, slot_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM execution_sessions WHERE coordination_id=? AND slot_id=?",
                (coordination_id, slot_id),
            ).fetchone()
        return self._row(row)

    def reserve(
        self,
        *,
        coordination_id: str,
        slot_id: str,
        task_id: str,
        agent_id: str,
        authorization_ref: str,
        participant_ids: list[str],
        assignment: dict[str, Any],
        planned_start_at: str | None,
        max_active_sessions: int,
    ) -> dict[str, Any]:
        now = utc_now_iso()
        with self._lock, self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM execution_sessions WHERE coordination_id=? AND slot_id=?",
                (coordination_id, slot_id),
            ).fetchone()
            if existing:
                current = self._row(existing)
                if (
                    current["agent_id"] != agent_id
                    or current["task_id"] != task_id
                    or current["authorization_ref"] != authorization_ref
                ):
                    raise ExecutionStateError(
                        "slot is already reserved with different immutable fields"
                    )
                return deepcopy(current)
            active_count = connection.execute(
                """
                SELECT COUNT(*) FROM execution_sessions
                WHERE agent_id=? AND status NOT IN ('completed', 'failed', 'aborted')
                """,
                (agent_id,),
            ).fetchone()[0]
            if int(active_count) >= max(1, int(max_active_sessions)):
                raise ExecutionStateError("execution Agent has no available task slots")
            connection.execute(
                """
                INSERT INTO execution_sessions (
                    coordination_id, slot_id, task_id, agent_id, status,
                    authorization_ref, planned_start_at, participant_ids_json,
                    assignment_json, last_error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'reserved', ?, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    coordination_id,
                    slot_id,
                    task_id,
                    agent_id,
                    authorization_ref,
                    planned_start_at,
                    json.dumps(sorted(set(participant_ids)), separators=(",", ":")),
                    json.dumps(assignment, sort_keys=True, separators=(",", ":")),
                    now,
                    now,
                ),
            )
            self._audit(connection, coordination_id, slot_id, None, "reserved", "reservation accepted")
        return self.get(coordination_id, slot_id) or {}

    def transition(
        self,
        coordination_id: str,
        slot_id: str,
        to_status: str,
        *,
        reason: str,
        last_error: str | None = None,
    ) -> dict[str, Any]:
        to_status = str(to_status).lower()
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM execution_sessions WHERE coordination_id=? AND slot_id=?",
                (coordination_id, slot_id),
            ).fetchone()
            if row is None:
                raise ExecutionStateError("unknown execution slot")
            current = self._row(row) or {}
            from_status = current["status"]
            if to_status == from_status:
                return current
            if to_status not in ALLOWED_TRANSITIONS.get(from_status, set()):
                raise ExecutionStateError(
                    f"invalid execution transition: {from_status} -> {to_status}"
                )
            connection.execute(
                """
                UPDATE execution_sessions
                SET status=?, last_error=?, updated_at=?
                WHERE coordination_id=? AND slot_id=?
                """,
                (to_status, last_error, utc_now_iso(), coordination_id, slot_id),
            )
            self._audit(connection, coordination_id, slot_id, from_status, to_status, reason)
        return self.get(coordination_id, slot_id) or {}

    def record_event(
        self,
        *,
        event_id: str,
        coordination_id: str,
        slot_id: str,
        event_type: str,
        source: str,
        observed_at: str,
        evidence: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        with self._lock, self._connect() as connection:
            known = connection.execute(
                "SELECT event_id FROM execution_events WHERE event_id=?", (event_id,)
            ).fetchone()
            if known:
                return self.get(coordination_id, slot_id) or {}, True
            row = connection.execute(
                "SELECT * FROM execution_sessions WHERE coordination_id=? AND slot_id=?",
                (coordination_id, slot_id),
            ).fetchone()
            if row is None:
                raise ExecutionStateError("unknown execution slot")
            current = self._row(row) or {}
            target = {
                "started": "executing",
                "completed": "completed",
                "failed": "failed",
                "aborted": "aborted",
            }.get(event_type)
            if target and target != current["status"] and target not in ALLOWED_TRANSITIONS.get(
                current["status"], set()
            ):
                raise ExecutionStateError(
                    f"invalid execution transition: {current['status']} -> {target}"
                )
            connection.execute(
                """
                INSERT INTO execution_events (
                    event_id, coordination_id, slot_id, event_type, source,
                    observed_at, evidence_json, received_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    coordination_id,
                    slot_id,
                    event_type,
                    source,
                    observed_at,
                    json.dumps(evidence, sort_keys=True, separators=(",", ":")),
                    utc_now_iso(),
                ),
            )
            if target and target != current["status"]:
                connection.execute(
                    """
                    UPDATE execution_sessions
                    SET status=?, last_error=?, updated_at=?
                    WHERE coordination_id=? AND slot_id=?
                    """,
                    (
                        target,
                        evidence.get("error") if target == "failed" else None,
                        utc_now_iso(),
                        coordination_id,
                        slot_id,
                    ),
                )
                self._audit(
                    connection,
                    coordination_id,
                    slot_id,
                    current["status"],
                    target,
                    f"verified external event {event_id}",
                )
        return self.get(coordination_id, slot_id) or {}, False

    def events(self, coordination_id: str, slot_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM execution_events
                WHERE coordination_id=? AND slot_id=? ORDER BY observed_at, event_id
                """,
                (coordination_id, slot_id),
            ).fetchall()
        output = []
        for row in rows:
            value = dict(row)
            value["evidence"] = json.loads(value.pop("evidence_json"))
            output.append(value)
        return output

    def list_sessions(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM execution_sessions ORDER BY updated_at DESC"
            ).fetchall()
        return [self._row(row) or {} for row in rows]

    @staticmethod
    def _audit(
        connection: sqlite3.Connection,
        coordination_id: str,
        slot_id: str,
        from_status: str | None,
        to_status: str,
        reason: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO execution_audit (
                coordination_id, slot_id, from_status, to_status, reason, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (coordination_id, slot_id, from_status, to_status, reason, utc_now_iso()),
        )
