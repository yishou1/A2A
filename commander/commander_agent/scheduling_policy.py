from __future__ import annotations

import json
import threading
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional


@dataclass
class AgentFeedback:
    instance_key: str
    attempts: int = 0
    successes: int = 0
    failures: int = 0
    avg_latency_ms: float = 0.0
    last_error_code: Optional[str] = None

    @property
    def success_rate(self) -> float:
        if self.attempts <= 0:
            return 1.0
        return self.successes / self.attempts

    def record(
        self,
        *,
        success: bool,
        latency_ms: Optional[float] = None,
        error_code: Optional[str] = None,
    ) -> None:
        self.attempts += 1
        if success:
            self.successes += 1
            self.last_error_code = None
        else:
            self.failures += 1
            self.last_error_code = error_code
        if latency_ms is not None:
            latency = max(0.0, float(latency_ms))
            if self.avg_latency_ms <= 0:
                self.avg_latency_ms = latency
            else:
                self.avg_latency_ms = self.avg_latency_ms * 0.8 + latency * 0.2

    def snapshot(self) -> dict:
        payload = asdict(self)
        payload["success_rate"] = round(self.success_rate, 6)
        payload["avg_latency_ms"] = round(self.avg_latency_ms, 3)
        return payload


class SchedulerFeedbackStore:
    """Thread-safe feedback memory used by scheduling decisions."""

    def __init__(self):
        self._lock = threading.RLock()
        self._items: dict[str, AgentFeedback] = {}

    def get(self, instance_key: str) -> AgentFeedback:
        with self._lock:
            item = self._items.get(instance_key)
            if item is None:
                item = AgentFeedback(instance_key=instance_key)
                self._items[instance_key] = item
            return deepcopy(item)

    def record(
        self,
        instance_key: str,
        *,
        success: bool,
        latency_ms: Optional[float] = None,
        error_code: Optional[str] = None,
    ) -> AgentFeedback:
        with self._lock:
            item = self._items.get(instance_key)
            if item is None:
                item = AgentFeedback(instance_key=instance_key)
                self._items[instance_key] = item
            item.record(success=success, latency_ms=latency_ms, error_code=error_code)
            self._after_update()
            return deepcopy(item)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                key: item.snapshot()
                for key, item in sorted(self._items.items())
            }

    def _after_update(self) -> None:
        return None


class JsonSchedulerFeedbackStore(SchedulerFeedbackStore):
    """Small JSON-backed feedback store for resident workflow managers."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        super().__init__()
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        with self._lock:
            for key, payload in (raw or {}).items():
                if not isinstance(payload, dict):
                    continue
                self._items[key] = AgentFeedback(
                    instance_key=payload.get("instance_key") or key,
                    attempts=int(payload.get("attempts", 0) or 0),
                    successes=int(payload.get("successes", 0) or 0),
                    failures=int(payload.get("failures", 0) or 0),
                    avg_latency_ms=float(payload.get("avg_latency_ms", 0.0) or 0.0),
                    last_error_code=payload.get("last_error_code"),
                )

    def _after_update(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            key: item.snapshot()
            for key, item in sorted(self._items.items())
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)


@dataclass
class SchedulingDecision:
    instance_key: str
    score: float
    accepted: bool = True
    reasons: list[str] = field(default_factory=list)
    components: dict = field(default_factory=dict)

    def snapshot(self) -> dict:
        return {
            "instance_key": self.instance_key,
            "score": round(self.score, 6),
            "accepted": self.accepted,
            "reasons": list(self.reasons),
            "components": deepcopy(self.components),
        }


class SchedulingPolicy:
    """Resource, capacity, quality and feedback aware Agent selection policy."""

    def __init__(
        self,
        *,
        resource_aware: bool = True,
        resource_limits: Optional[dict] = None,
        feedback_store: Optional[SchedulerFeedbackStore] = None,
    ):
        self.resource_aware = bool(resource_aware)
        self.resource_limits = dict(resource_limits or {})
        self.feedback_store = feedback_store or SchedulerFeedbackStore()

    def rank(
        self,
        candidates: Iterable[dict],
        *,
        instance_key: Callable[[dict], str],
    ) -> list[dict]:
        ranked = []
        for target in candidates:
            key = instance_key(target)
            decision = self.evaluate(target, key)
            target["_scheduling_decision"] = decision.snapshot()
            if decision.accepted:
                ranked.append(target)
        if self.resource_aware:
            ranked.sort(
                key=lambda target: (
                    -target.get("_scheduling_decision", {}).get("score", 0.0),
                    instance_key(target),
                )
            )
        return ranked

    def evaluate(self, target: dict, instance_key: str) -> SchedulingDecision:
        metadata = target.get("metadata", {}) or {}
        reasons = []
        limit_ok, limit_reasons = self._within_limits(metadata)
        reasons.extend(limit_reasons)
        if not limit_ok:
            return SchedulingDecision(
                instance_key=instance_key,
                score=float("-inf"),
                accepted=False,
                reasons=reasons,
                components={},
            )

        cpu = self._as_float(metadata.get("resource_cpu_percent"), 50.0)
        memory = self._as_float(metadata.get("resource_memory_percent"), 50.0)
        gpu = self._as_float(metadata.get("resource_gpu_percent"), 50.0)
        active = self._as_float(metadata.get("active_tasks"), 0.0)
        max_tasks = max(1.0, self._as_float(metadata.get("max_concurrent_tasks"), 1.0))
        available_slots = self._as_float(
            metadata.get("available_task_slots"),
            max(0.0, max_tasks - active),
        )
        metadata_success_rate = self._as_float(metadata.get("quality_success_rate"), 1.0)
        metadata_latency = self._as_float(metadata.get("quality_avg_latency_ms"), 0.0)
        feedback = self.feedback_store.get(instance_key)

        resource_score = (100.0 - cpu) * 0.18 + (100.0 - memory) * 0.12
        if str(metadata.get("resource_gpu_available", "false")).lower() == "true":
            resource_score += (100.0 - gpu) * 0.10
        capacity_score = min(max(available_slots, 0.0), max_tasks) / max_tasks * 10.0
        quality_score = max(0.0, min(1.0, metadata_success_rate)) * 30.0
        metadata_latency_penalty = min(max(metadata_latency, 0.0) / 1000.0, 10.0) * 1.5
        feedback_score = feedback.success_rate * 20.0
        feedback_latency_penalty = min(max(feedback.avg_latency_ms, 0.0) / 1000.0, 10.0)
        feedback_failure_penalty = min(feedback.failures, 5) * 2.0
        load_penalty = max(active, 0.0) * 5.0

        score = (
            resource_score
            + capacity_score
            + quality_score
            + feedback_score
            - metadata_latency_penalty
            - feedback_latency_penalty
            - feedback_failure_penalty
            - load_penalty
        )
        components = {
            "resource_score": round(resource_score, 6),
            "capacity_score": round(capacity_score, 6),
            "metadata_quality_score": round(quality_score, 6),
            "feedback_score": round(feedback_score, 6),
            "latency_penalty": round(metadata_latency_penalty + feedback_latency_penalty, 6),
            "failure_penalty": round(feedback_failure_penalty, 6),
            "load_penalty": round(load_penalty, 6),
            "feedback": feedback.snapshot(),
        }
        reasons.append("ranked_by_resource_capacity_quality_feedback")
        return SchedulingDecision(
            instance_key=instance_key,
            score=round(score, 6),
            accepted=True,
            reasons=reasons,
            components=components,
        )

    def record_feedback(
        self,
        instance_key: str,
        *,
        success: bool,
        latency_ms: Optional[float] = None,
        error_code: Optional[str] = None,
    ) -> AgentFeedback:
        return self.feedback_store.record(
            instance_key,
            success=success,
            latency_ms=latency_ms,
            error_code=error_code,
        )

    def feedback_snapshot(self) -> dict:
        return self.feedback_store.snapshot()

    def _within_limits(self, metadata: dict) -> tuple[bool, list[str]]:
        reasons = []
        mappings = {
            "cpu_percent": "resource_cpu_percent",
            "memory_percent": "resource_memory_percent",
            "gpu_percent": "resource_gpu_percent",
            "gpu_memory_percent": "resource_gpu_memory_percent",
            "active_tasks": "active_tasks",
        }
        for limit_name, metadata_name in mappings.items():
            if limit_name not in self.resource_limits:
                continue
            value = self._as_float(metadata.get(metadata_name))
            if value is not None and value > float(self.resource_limits[limit_name]):
                reasons.append(f"{metadata_name}={value} exceeds {limit_name}={self.resource_limits[limit_name]}")
                return False, reasons
        return True, reasons

    @staticmethod
    def _as_float(value, default=None):
        if value in (None, ""):
            return default
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
