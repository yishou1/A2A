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

    def release(self, lease: AgentLease) -> None:
        with self._lock:
            current = self._leases.get(lease.instance_key)
            if current != lease:
                return
            self._leases.pop(lease.instance_key, None)
            self.registry.update_instance_metadata(
                lease.service_name,
                lease.target,
                metadata_updates={"status": "idle"},
                remove_keys=[
                    "lease_workflow_id",
                    "lease_work_item",
                    "lease_acquired_at",
                ],
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
