from __future__ import annotations

import threading
from dataclasses import asdict, dataclass
import json
import re
from typing import Iterable, Optional

from commander_agent.distributed_lock import DistributedLockHandle
from model_registry import instance_has_model
from workflow_state_store import utc_now_iso


@dataclass(frozen=True)
class AgentLease:
    instance_key: str
    service_name: str
    role: str
    workflow_id: str
    work_item: str
    acquired_at: str
    target: dict
    slot_id: int = 0
    lock_handle: Optional[DistributedLockHandle] = None

    @property
    def slot_key(self) -> str:
        return f"{self.instance_key}:slot:{self.slot_id}"

    def snapshot(self) -> dict:
        snapshot = asdict(self)
        handle = snapshot.pop("lock_handle", None)
        snapshot["distributed_lock"] = bool(handle)
        snapshot["slot_key"] = self.slot_key
        if handle:
            snapshot["distributed_lock_key"] = handle["key"]
        return snapshot


class AgentLeaseManager:
    """Coordinates exclusive Agent use for Commanders in one manager process."""

    def __init__(
        self,
        registry,
        service_name: str = "A2A-Agent",
        circuit_breaker=None,
        distributed_lock=None,
        resource_aware: bool = True,
        resource_limits: Optional[dict] = None,
    ):
        self.registry = registry
        self.service_name = service_name
        self.circuit_breaker = circuit_breaker
        self.distributed_lock = distributed_lock
        self.resource_aware = bool(resource_aware)
        self.resource_limits = dict(resource_limits or {})
        self._lock = threading.RLock()
        self._leases: dict[str, AgentLease] = {}

    @staticmethod
    def instance_key(target: dict) -> str:
        return f"{target.get('ip')}:{target.get('port')}"

    def acquire_one(
        self,
        role: str,
        workflow_id: str,
        work_item: str,
        exclude_keys: Optional[Iterable[str]] = None,
        required_skill: Optional[str] = None,
        required_skills: Optional[Iterable[str]] = None,
        required_model: Optional[str] = None,
    ) -> Optional[AgentLease]:
        with self._lock:
            excluded = set(exclude_keys or [])
            for target in self._discover_idle(
                role,
                required_skill=required_skill,
                required_skills=required_skills,
                required_model=required_model,
            ):
                key = self.instance_key(target)
                if (
                    key in excluded
                    or not self._has_available_capacity(target)
                ):
                    continue
                lease = self._acquire(target, role, workflow_id, work_item)
                if lease is not None:
                    return lease
        return None

    def acquire_all(
        self,
        role: str,
        workflow_id: str,
        work_item: str,
        limit: Optional[int] = None,
        required_skill: Optional[str] = None,
        required_skills: Optional[Iterable[str]] = None,
        required_model: Optional[str] = None,
    ) -> list[AgentLease]:
        leases = []
        with self._lock:
            for target in self._discover_idle(
                role,
                required_skill=required_skill,
                required_skills=required_skills,
                required_model=required_model,
            ):
                key = self.instance_key(target)
                if not self._has_available_capacity(target):
                    continue
                lease = self._acquire(target, role, workflow_id, work_item)
                if lease is None:
                    continue
                leases.append(lease)
                if limit is not None and len(leases) >= limit:
                    break
        return leases

    def release(
        self,
        lease: AgentLease,
        *,
        status: str = "idle",
        metadata_updates: Optional[dict] = None,
        remove_keys: Optional[Iterable[str]] = None,
    ) -> None:
        with self._lock:
            current = self._leases.get(lease.slot_key)
            if current != lease:
                return
            metadata = lease.target.get("metadata", {}) or {}
            active_before = max(
                self._as_int(metadata.get("active_tasks"), 0),
                self._local_active_count(lease.instance_key),
            )
            self._leases.pop(lease.slot_key, None)
            if lease.lock_handle and not self.distributed_lock.is_owned(lease.lock_handle):
                return
            active_after = max(
                self._local_active_count(lease.instance_key),
                active_before - 1,
            )
            max_concurrent = self._max_concurrent_tasks(lease.target)
            if status == "idle" and active_after > 0:
                status = "busy"
            execution_status = self._execution_status(active_after, max_concurrent, status)
            updates = {
                "status": status,
                "active_tasks": str(active_after),
                "available_task_slots": str(max(0, max_concurrent - active_after)),
                "task_execution_status": execution_status,
            }
            updates.update(metadata_updates or {})
            cleanup_keys = [
                "lease_workflow_id",
                "lease_work_item",
                "lease_acquired_at",
                "lease_lock_backend",
                "lease_lock_key",
                "lease_slot_id",
            ]
            if status == "idle" and active_after == 0:
                cleanup_keys.extend(
                    [
                        "unavailable_workflow_id",
                        "unavailable_work_item",
                        "unavailable_at",
                        "unavailable_reason",
                        "unavailable_error_code",
                        "unavailable_error_category",
                    ]
                )
            cleanup_keys.extend(remove_keys or [])
            try:
                self.registry.update_instance_metadata(
                    lease.service_name,
                    lease.target,
                    metadata_updates=updates,
                    remove_keys=cleanup_keys,
                )
            finally:
                if lease.lock_handle:
                    self.distributed_lock.release(lease.lock_handle)

    def release_workflow(self, workflow_id: str) -> None:
        with self._lock:
            leases = [
                lease
                for lease in self._leases.values()
                if lease.workflow_id == workflow_id
            ]
        for lease in leases:
            self.release(lease)

    def is_current(self, lease: AgentLease) -> bool:
        with self._lock:
            if self._leases.get(lease.slot_key) != lease:
                return False
            if lease.lock_handle:
                return self.distributed_lock.is_owned(lease.lock_handle)
            return True

    def latest_instance(self, lease: AgentLease) -> Optional[dict]:
        finder = getattr(self.registry, "find_instance", None)
        if finder is None:
            return lease.target
        return finder(lease.service_name, lease.target)

    def is_lease_fresh(self, lease: AgentLease) -> bool:
        if not self.is_current(lease):
            return False

        checker = (
            getattr(self.registry, "is_instance_fresh", None)
            or getattr(self.registry, "_is_instance_fresh", None)
        )
        if checker is None:
            return True

        latest = self.latest_instance(lease)
        if latest is None:
            return False
        return bool(checker(latest))

    def list_leases(self) -> list[dict]:
        with self._lock:
            return [lease.snapshot() for lease in self._leases.values()]

    def close(self):
        with self._lock:
            leases = list(self._leases.values())
        for lease in leases:
            self.release(lease)
        if self.distributed_lock is not None:
            self.distributed_lock.close()

    def _discover_idle(
        self,
        role: str,
        required_skill: Optional[str] = None,
        required_skills: Optional[Iterable[str]] = None,
        required_model: Optional[str] = None,
    ) -> list[dict]:
        skill_requirements = self._skill_requirements(required_skill, required_skills)
        idle = self._discover_capacity_candidates(role, skill_requirements)
        if self.distributed_lock is not None:
            idle.extend(
                self._recover_stale_busy_instances(
                    role,
                    required_skill=required_skill,
                    required_skills=skill_requirements,
                )
            )
        if self.circuit_breaker is None:
            return self._apply_selection_filters(idle, required_model)

        unavailable_tags = (
            {"status": "unavailable"}
            if skill_requirements
            else {"role": role, "status": "unavailable"}
        )
        unavailable = self.registry.discover_service(
            self.service_name,
            unavailable_tags,
        )
        if skill_requirements:
            unavailable = self._filter_by_skill(unavailable, skill_requirements)
        candidates = []
        seen = set()
        for target in [*idle, *unavailable]:
            key = self.instance_key(target)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(target)
        return self._apply_selection_filters(candidates, required_model)

    def _discover_capacity_candidates(self, role: str, skill_requirements: list[str]) -> list[dict]:
        statuses = ("idle", "busy")
        candidates = []
        seen = set()
        for status in statuses:
            tags = {"status": status} if skill_requirements else {"role": role, "status": status}
            for target in self.registry.discover_service(self.service_name, tags):
                key = self.instance_key(target)
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(target)
        if skill_requirements:
            candidates = self._filter_by_skill(candidates, skill_requirements)
        return [
            target
            for target in candidates
            if self._has_available_capacity(target)
        ]

    def _apply_selection_filters(
        self,
        candidates: list[dict],
        required_model: Optional[str] = None,
    ) -> list[dict]:
        """Apply hard constraints and rank candidates by resource/quality data."""
        result = list(candidates)
        if required_model:
            result = [
                target
                for target in result
                if instance_has_model(target.get("metadata", {}) or {}, required_model)
            ]
        if self.resource_limits:
            result = [target for target in result if self._within_resource_limits(target)]
        if self.resource_aware:
            result.sort(
                key=lambda target: (
                    -self._candidate_score(target),
                    self.instance_key(target),
                )
            )
        return result

    def _within_resource_limits(self, target: dict) -> bool:
        metadata = target.get("metadata", {}) or {}
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
                return False
        if not self._has_available_capacity(target):
            return False
        return True

    @classmethod
    def _candidate_score(cls, target: dict) -> float:
        metadata = target.get("metadata", {}) or {}
        cpu = cls._as_float(metadata.get("resource_cpu_percent"), 50.0)
        memory = cls._as_float(metadata.get("resource_memory_percent"), 50.0)
        gpu = cls._as_float(metadata.get("resource_gpu_percent"), 50.0)
        active = cls._as_float(metadata.get("active_tasks"), 0.0)
        success_rate = cls._as_float(metadata.get("quality_success_rate"), 1.0)
        latency = cls._as_float(metadata.get("quality_avg_latency_ms"), 0.0)
        resource_score = (100.0 - cpu) * 0.18 + (100.0 - memory) * 0.12
        if str(metadata.get("resource_gpu_available", "false")).lower() == "true":
            resource_score += (100.0 - gpu) * 0.10
        quality_score = max(0.0, min(1.0, success_rate)) * 50.0
        latency_penalty = min(max(latency, 0.0) / 1000.0, 10.0) * 2.0
        load_penalty = max(active, 0.0) * 5.0
        return round(resource_score + quality_score - latency_penalty - load_penalty, 6)

    @staticmethod
    def _as_float(value, default=None):
        if value in (None, ""):
            return default
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _as_int(value, default=0):
        if value in (None, ""):
            return default
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default

    def _max_concurrent_tasks(self, target: dict) -> int:
        metadata = target.get("metadata", {}) or {}
        return max(1, self._as_int(metadata.get("max_concurrent_tasks"), 1))

    def _local_active_count(self, instance_key: str) -> int:
        return sum(1 for lease in self._leases.values() if lease.instance_key == instance_key)

    def _active_task_count(self, target: dict) -> int:
        metadata = target.get("metadata", {}) or {}
        return max(
            self._as_int(metadata.get("active_tasks"), 0),
            self._local_active_count(self.instance_key(target)),
        )

    def _has_available_capacity(self, target: dict) -> bool:
        metadata = target.get("metadata", {}) or {}
        if str(metadata.get("agent_run_state", "ready")).lower() in {"not_ready", "unavailable"}:
            return False
        if str(metadata.get("task_execution_status", "")).lower() == "saturated":
            return False
        return self._active_task_count(target) < self._max_concurrent_tasks(target)

    @staticmethod
    def _execution_status(active_tasks: int, max_concurrent_tasks: int, status: str) -> str:
        if status == "unavailable":
            return "unavailable"
        if active_tasks <= 0:
            return "idle"
        if active_tasks >= max_concurrent_tasks:
            return "saturated"
        return "busy"

    def _recover_stale_busy_instances(
        self,
        role: str,
        required_skill: Optional[str] = None,
        required_skills: Optional[Iterable[str]] = None,
    ) -> list[dict]:
        recovered = []
        skill_requirements = self._skill_requirements(required_skill, required_skills)
        tags = {"status": "busy"} if skill_requirements else {"role": role, "status": "busy"}
        busy_instances = self.registry.discover_service(
            self.service_name,
            tags,
        )
        if skill_requirements:
            busy_instances = self._filter_by_skill(busy_instances, skill_requirements)
        for target in busy_instances:
            metadata = target.get("metadata", {}) or {}
            lock_keys = []
            legacy_lock_key = metadata.get("lease_lock_key")
            if legacy_lock_key:
                lock_keys.append(legacy_lock_key)
            max_concurrent = self._max_concurrent_tasks(target)
            key = self.instance_key(target)
            for slot_id in range(max_concurrent):
                lock_keys.append(
                    self.distributed_lock.resource_key(
                        self._lock_resource_name(key, slot_id)
                    )
                )
            if not lock_keys:
                continue
            try:
                if any(self.distributed_lock.is_key_locked(lock_key) for lock_key in lock_keys):
                    continue
            except Exception:
                # Redis uncertainty is fail-closed: never reclaim the Agent.
                continue
            self.registry.update_instance_metadata(
                self.service_name,
                target,
                metadata_updates={
                    "status": "idle",
                    "active_tasks": "0",
                    "available_task_slots": str(max_concurrent),
                    "task_execution_status": "idle",
                },
                remove_keys=[
                    "lease_workflow_id",
                    "lease_work_item",
                    "lease_acquired_at",
                    "lease_lock_backend",
                    "lease_lock_key",
                    "lease_slot_id",
                ],
            )
            recovered.append(target)
        return recovered

    @classmethod
    def _filter_by_skill(
        cls,
        instances: list[dict],
        required_skills,
    ) -> list[dict]:
        requirements = cls._skill_requirements(None, required_skills)
        return [
            instance
            for instance in instances
            if cls._instance_has_skills(instance, requirements)
        ]

    @classmethod
    def _instance_has_skill(cls, instance: dict, required_skill: str) -> bool:
        required = cls._normalize_token(required_skill)
        if not required:
            return False
        metadata = instance.get("metadata", {}) or {}
        for token in cls._skill_ids_from_metadata(metadata):
            normalized = cls._normalize_token(token)
            if normalized == required:
                return True
        return False

    @classmethod
    def _skill_ids_from_metadata(cls, metadata: dict) -> list[str]:
        explicit = metadata.get("skill_ids")
        if explicit:
            return cls._split_metadata_values(explicit, ids_only=True)
        return cls._split_metadata_values(metadata.get("skills"), ids_only=True)

    @classmethod
    def _split_metadata_values(cls, value, *, ids_only: bool = False) -> list[str]:
        if not value:
            return []
        if isinstance(value, (list, tuple, set)):
            items = list(value)
        else:
            text = str(value)
            try:
                parsed = json.loads(text)
            except (TypeError, ValueError):
                parsed = None
            items = parsed if isinstance(parsed, list) else re.split(r"[,;\s]+", text)
        result = []
        for item in items:
            if isinstance(item, dict):
                if item.get("id"):
                    result.append(str(item["id"]))
            elif item not in (None, ""):
                result.append(str(item))
        return result

    @classmethod
    def _instance_has_skills(cls, instance: dict, required_skills: Iterable[str]) -> bool:
        requirements = cls._skill_requirements(None, required_skills)
        if not requirements:
            return False
        return all(cls._instance_has_skill(instance, skill) for skill in requirements)

    @classmethod
    def _skill_tokens_from_metadata(cls, metadata: dict) -> list[str]:
        raw_values = []
        for key in ("skills", "skill", "capabilities", "capability"):
            value = metadata.get(key)
            if value:
                raw_values.append(value)

        tokens = []
        for value in raw_values:
            if isinstance(value, (list, tuple, set)):
                tokens.extend(str(item) for item in value)
                continue
            text = str(value)
            try:
                parsed = json.loads(text)
            except (TypeError, ValueError):
                parsed = None
            if isinstance(parsed, list):
                for item in parsed:
                    if isinstance(item, dict):
                        tokens.extend(
                            str(part)
                            for part in [
                                item.get("id"),
                                item.get("name"),
                                item.get("description"),
                                *(item.get("tags") or []),
                            ]
                            if part
                        )
                    else:
                        tokens.append(str(item))
                continue
            tokens.extend(part for part in re.split(r"[,;\s]+", text) if part)
        return tokens

    @staticmethod
    def _normalize_token(value: str) -> str:
        return re.sub(r"[\s_\-]+", "", str(value or "").strip().lower())

    @staticmethod
    def _skill_requirements(
        required_skill: Optional[str],
        required_skills: Optional[Iterable[str]],
    ) -> list[str]:
        if isinstance(required_skills, str):
            values = [part.strip() for part in re.split(r"[,;\s]+", required_skills) if part.strip()]
        else:
            values = [str(skill) for skill in (required_skills or []) if skill]
        if required_skill and required_skill not in values:
            values.insert(0, required_skill)
        return values

    def _circuit_allows(self, target: dict) -> bool:
        if self.circuit_breaker is None:
            return True
        return self.circuit_breaker.allow_request(target)

    def _acquire(
        self,
        target: dict,
        role: str,
        workflow_id: str,
        work_item: str,
    ) -> Optional[AgentLease]:
        key = self.instance_key(target)
        slot_id = None
        lock_handle = None
        for candidate_slot in self._available_slot_ids(target):
            if self.distributed_lock is None:
                slot_id = candidate_slot
                break
            lock_handle = self.distributed_lock.acquire(
                self._lock_resource_name(key, candidate_slot)
            )
            if lock_handle is not None:
                slot_id = candidate_slot
                break
        if slot_id is None:
            return None
        if not self._circuit_allows(target):
            if lock_handle:
                self.distributed_lock.release(lock_handle)
            return None
        acquired_at = utc_now_iso()
        lease = AgentLease(
            instance_key=key,
            service_name=self.service_name,
            role=role,
            workflow_id=workflow_id,
            work_item=work_item,
            acquired_at=acquired_at,
            target=target,
            slot_id=slot_id,
            lock_handle=lock_handle,
        )
        self._leases[lease.slot_key] = lease
        try:
            circuit_metadata = (
                self.circuit_breaker.metadata(key)
                if self.circuit_breaker is not None
                else {}
            )
            lock_metadata = {"lease_lock_backend": "process"}
            if lock_handle:
                lock_metadata = {
                    "lease_lock_backend": "redis",
                    "lease_lock_key": lock_handle.key,
                }
            active_tasks = self._active_task_count(target)
            max_concurrent = self._max_concurrent_tasks(target)
            self.registry.update_instance_metadata(
                self.service_name,
                target,
                metadata_updates={
                    "status": "busy",
                    "active_tasks": str(active_tasks),
                    "max_concurrent_tasks": str(max_concurrent),
                    "available_task_slots": str(max(0, max_concurrent - active_tasks)),
                    "task_execution_status": self._execution_status(
                        active_tasks,
                        max_concurrent,
                        "busy",
                    ),
                    "lease_workflow_id": workflow_id,
                    "lease_work_item": work_item,
                    "lease_slot_id": str(slot_id),
                    "lease_acquired_at": acquired_at,
                    **lock_metadata,
                    **circuit_metadata,
                },
            )
        except Exception:
            self._leases.pop(lease.slot_key, None)
            if lock_handle:
                self.distributed_lock.release(lock_handle)
            raise
        return lease

    def _available_slot_ids(self, target: dict) -> list[int]:
        max_concurrent = self._max_concurrent_tasks(target)
        key = self.instance_key(target)
        occupied = {
            lease.slot_id
            for lease in self._leases.values()
            if lease.instance_key == key
        }
        return [
            slot_id
            for slot_id in range(max_concurrent)
            if slot_id not in occupied
        ]

    def _lock_resource_name(self, instance_key: str, slot_id: int) -> str:
        return f"{self.service_name}:{instance_key}:slot:{slot_id}"
