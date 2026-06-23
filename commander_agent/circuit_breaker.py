from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable


@dataclass
class CircuitRecord:
    state: str = "closed"
    failure_count: int = 0
    opened_at_ts: float | None = None
    open_until_ts: float | None = None
    probe_in_flight: bool = False


class AgentCircuitBreaker:
    """Per-Agent closed/open/half-open circuit breaker."""

    METADATA_KEYS = (
        "circuit_state",
        "circuit_failure_count",
        "circuit_opened_at_ts",
        "circuit_open_until_ts",
    )

    def __init__(
        self,
        failure_threshold: int = 3,
        recovery_timeout: float = 30.0,
        clock: Callable[[], float] | None = None,
    ):
        self.failure_threshold = max(1, int(failure_threshold))
        self.recovery_timeout = max(0.0, float(recovery_timeout))
        self._clock = clock or time.time
        self._records: dict[str, CircuitRecord] = {}
        self._lock = threading.RLock()

    @staticmethod
    def instance_key(target: dict) -> str:
        return f"{target.get('ip')}:{target.get('port')}"

    def allow_request(self, target: dict) -> bool:
        key = self.instance_key(target)
        metadata = target.get("metadata", {}) or {}
        with self._lock:
            record = self._record_from_metadata(key, metadata)
            if metadata.get("status") == "unavailable" and record.state == "closed":
                return False
            if record.state == "closed":
                return True
            if record.state == "open":
                if self._clock() < (record.open_until_ts or 0):
                    return False
                record.state = "half_open"
                record.probe_in_flight = True
                return True
            if record.state == "half_open" and not record.probe_in_flight:
                record.probe_in_flight = True
                return True
            return False

    def record_success(self, target_or_key) -> dict:
        key = self._key(target_or_key)
        with self._lock:
            self._records[key] = CircuitRecord()
            return self.snapshot(key)

    def record_failure(self, target_or_key) -> dict:
        key = self._key(target_or_key)
        with self._lock:
            record = self._records.setdefault(key, CircuitRecord())
            record.failure_count += 1
            record.probe_in_flight = False
            if record.state == "half_open" or record.failure_count >= self.failure_threshold:
                now = self._clock()
                record.state = "open"
                record.opened_at_ts = now
                record.open_until_ts = now + self.recovery_timeout
            return self.snapshot(key)

    def snapshot(self, target_or_key) -> dict:
        key = self._key(target_or_key)
        with self._lock:
            record = self._records.setdefault(key, CircuitRecord())
            return {
                "state": record.state,
                "failure_count": record.failure_count,
                "opened_at_ts": record.opened_at_ts,
                "open_until_ts": record.open_until_ts,
                "probe_in_flight": record.probe_in_flight,
            }

    def metadata(self, target_or_key) -> dict:
        snapshot = self.snapshot(target_or_key)
        metadata = {
            "circuit_state": snapshot["state"],
            "circuit_failure_count": snapshot["failure_count"],
        }
        if snapshot["opened_at_ts"] is not None:
            metadata["circuit_opened_at_ts"] = snapshot["opened_at_ts"]
        if snapshot["open_until_ts"] is not None:
            metadata["circuit_open_until_ts"] = snapshot["open_until_ts"]
        return metadata

    def _record_from_metadata(self, key: str, metadata: dict) -> CircuitRecord:
        record = self._records.get(key)
        metadata_state = str(metadata.get("circuit_state") or "closed").lower()
        if record is None:
            record = CircuitRecord(
                state=metadata_state,
                failure_count=self._as_int(metadata.get("circuit_failure_count")),
                opened_at_ts=self._as_float(metadata.get("circuit_opened_at_ts")),
                open_until_ts=self._as_float(metadata.get("circuit_open_until_ts")),
            )
            self._records[key] = record
        elif metadata_state == "open":
            metadata_until = self._as_float(metadata.get("circuit_open_until_ts"))
            if metadata_until and metadata_until > (record.open_until_ts or 0):
                record.state = "open"
                record.failure_count = max(
                    record.failure_count,
                    self._as_int(metadata.get("circuit_failure_count")),
                )
                record.opened_at_ts = self._as_float(metadata.get("circuit_opened_at_ts"))
                record.open_until_ts = metadata_until
                record.probe_in_flight = False
        return record

    @staticmethod
    def _key(target_or_key) -> str:
        if isinstance(target_or_key, dict):
            return AgentCircuitBreaker.instance_key(target_or_key)
        return str(target_or_key)

    @staticmethod
    def _as_int(value) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _as_float(value) -> float | None:
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None
