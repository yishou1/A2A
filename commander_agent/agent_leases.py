from __future__ import annotations

import threading
from dataclasses import asdict, dataclass
from typing import Iterable, Optional

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

    def snapshot(self) -> dict:
        return asdict(self)


class AgentLeaseManager:
    """Coordinates exclusive Agent use for Commanders in one manager process."""

    def __init__(self, registry, service_name: str = "A2A-Agent"):
        self.registry = registry
        self.service_name = service_name
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
    ) -> Optional[AgentLease]:
        with self._lock:
            excluded = set(exclude_keys or [])
            for target in self._discover_idle(role):
                key = self.instance_key(target)
                if key in excluded or key in self._leases:
                    continue
                return self._acquire(target, role, workflow_id, work_item)
        return None

    def acquire_all(
        self,
        role: str,
        workflow_id: str,
        work_item: str,
        limit: Optional[int] = None,
    ) -> list[AgentLease]:
        leases = []
        with self._lock:
            for target in self._discover_idle(role):
                key = self.instance_key(target)
                if key in self._leases:
                    continue
                leases.append(self._acquire(target, role, workflow_id, work_item))
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
            updates = {"status": status}
            updates.update(metadata_updates or {})
            cleanup_keys = [
                "lease_workflow_id",
                "lease_work_item",
                "lease_acquired_at",
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
            self.registry.update_instance_metadata(
                lease.service_name,
                lease.target,
                metadata_updates=updates,
                remove_keys=cleanup_keys,
            )

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
            return self._leases.get(lease.instance_key) == lease

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

    def _discover_idle(self, role: str) -> list[dict]:
        return self.registry.discover_service(
            self.service_name,
            {"role": role, "status": "idle"},
        )

    def _acquire(
        self,
        target: dict,
        role: str,
        workflow_id: str,
        work_item: str,
    ) -> AgentLease:
        key = self.instance_key(target)
        acquired_at = utc_now_iso()
        lease = AgentLease(
            instance_key=key,
            service_name=self.service_name,
            role=role,
            workflow_id=workflow_id,
            work_item=work_item,
            acquired_at=acquired_at,
            target=target,
        )
        self._leases[key] = lease
        try:
            self.registry.update_instance_metadata(
                self.service_name,
                target,
                metadata_updates={
                    "status": "busy",
                    "lease_workflow_id": workflow_id,
                    "lease_work_item": work_item,
                    "lease_acquired_at": acquired_at,
                },
            )
        except Exception:
            self._leases.pop(key, None)
            raise
        return lease
