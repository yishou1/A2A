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
    lock_handle: Optional[DistributedLockHandle] = None

    def snapshot(self) -> dict:
        snapshot = asdict(self)
        handle = snapshot.pop("lock_handle", None)
        snapshot["distributed_lock"] = bool(handle)
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
        resource_aware: bool = False,
        resource_limits: Optional[dict] = None,
    ):
        self.registry = registry
        self.service_name = service_name
        self.circuit_breaker = circuit_breaker
        self.distributed_lock = distributed_lock
        self.resource_aware = resource_aware
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
                    or key in self._leases
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
                if key in self._leases:
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
            current = self._leases.get(lease.instance_key)
            if current != lease:
                return
            self._leases.pop(lease.instance_key, None)
            if lease.lock_handle and not self.distributed_lock.is_owned(lease.lock_handle):
                return
            updates = {"status": status}
            updates.update(metadata_updates or {})
            cleanup_keys = [
                "lease_workflow_id",
                "lease_work_item",
                "lease_acquired_at",
                "lease_lock_backend",
                "lease_lock_key",
            ]
            if status == "idle":
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
            if self._leases.get(lease.instance_key) != lease:
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
        tags = {"status": "idle"} if skill_requirements else {"role": role, "status": "idle"}
        idle = self.registry.discover_service(self.service_name, tags)
        if skill_requirements:
            idle = self._filter_by_skill(idle, skill_requirements)
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

    def _apply_selection_filters(
        self,
        candidates: list[dict],
        required_model: Optional[str] = None,
    ) -> list[dict]:
        """Filter/rank discovered candidates by model availability and resources.

        Model filtering is applied whenever a required model is requested.
        Resource-aware filtering and ranking are only applied when the manager
        was created with ``resource_aware=True`` so default behaviour is
        unchanged.
        """
        result = list(candidates)
        if required_model:
            result = [
                target
                for target in result
                if instance_has_model(target.get("metadata", {}) or {}, required_model)
            ]
        if self.resource_aware:
            if self.resource_limits:
                result = [target for target in result if self._resource_allows(target)]
            result = sorted(result, key=self._resource_score)
        return result

    @staticmethod
    def _metadata_float(metadata: dict, key: str) -> Optional[float]:
        try:
            value = metadata.get(key)
            return None if value is None else float(value)
        except (TypeError, ValueError):
            return None

    def _resource_allows(self, target: dict) -> bool:
        metadata = target.get("metadata", {}) or {}
        max_checks = {
            "resource_cpu_percent": self.resource_limits.get("cpu_percent"),
            "resource_memory_percent": self.resource_limits.get("memory_percent"),
            "resource_gpu_percent": self.resource_limits.get("gpu_percent"),
            "resource_disk_percent": self.resource_limits.get("disk_percent"),
        }
        for meta_key, limit in max_checks.items():
            if limit is None:
                continue
            value = self._metadata_float(metadata, meta_key)
            if value is not None and value > float(limit):
                return False
        min_link = self.resource_limits.get("min_link_stability")
        if min_link is not None:
            link = self._metadata_float(metadata, "resource_link_stability")
            if link is not None and link < float(min_link):
                return False
        return True

    def _resource_score(self, target: dict) -> float:
        """Lower score means a less-loaded, preferred instance."""
        metadata = target.get("metadata", {}) or {}
        cpu = self._metadata_float(metadata, "resource_cpu_percent") or 0.0
        memory = self._metadata_float(metadata, "resource_memory_percent") or 0.0
        gpu = self._metadata_float(metadata, "resource_gpu_percent") or 0.0
        return cpu + memory + gpu

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
            lock_key = metadata.get("lease_lock_key")
            if not lock_key:
                continue
            try:
                if self.distributed_lock.is_key_locked(lock_key):
                    continue
            except Exception:
                # Redis uncertainty is fail-closed: never reclaim the Agent.
                continue
            self.registry.update_instance_metadata(
                self.service_name,
                target,
                metadata_updates={"status": "idle"},
                remove_keys=[
                    "lease_workflow_id",
                    "lease_work_item",
                    "lease_acquired_at",
                    "lease_lock_backend",
                    "lease_lock_key",
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
        for token in cls._skill_tokens_from_metadata(metadata):
            normalized = cls._normalize_token(token)
            if normalized and (normalized == required or required in normalized):
                return True
        return False

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
        lock_handle = None
        if self.distributed_lock is not None:
            lock_handle = self.distributed_lock.acquire(
                f"{self.service_name}:{key}"
            )
            if lock_handle is None:
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
            lock_handle=lock_handle,
        )
        self._leases[key] = lease
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
            self.registry.update_instance_metadata(
                self.service_name,
                target,
                metadata_updates={
                    "status": "busy",
                    "lease_workflow_id": workflow_id,
                    "lease_work_item": work_item,
                    "lease_acquired_at": acquired_at,
                    **lock_metadata,
                    **circuit_metadata,
                },
            )
        except Exception:
            self._leases.pop(key, None)
            if lock_handle:
                self.distributed_lock.release(lock_handle)
            raise
        return lease
