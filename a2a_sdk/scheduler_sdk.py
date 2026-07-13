from __future__ import annotations

from typing import Any, Callable, Iterable, Optional


class SchedulerSDK:
    """Scheduler-side facade for the distributed-agent interfaces.

    Aggregates dynamic discovery, delayed binding, task dispatch, result
    handling, exception classification and recovery notification behind a single
    entry point for the scheduling module.

    Heavy dependencies (Nacos SDK, Redis lock, HTTP client) are imported lazily,
    so importing this module is cheap and side-effect free.

    Example::

        sdk = SchedulerSDK()
        lease = sdk.bind_agent("recon", "wf-1", "wf-1:1", required_model="recon_detector_v1")
        result = sdk.dispatch_to_lease(lease, {"command": "scan", "input": {...}})
        sdk.release(lease)
    """

    def __init__(
        self,
        *,
        registry: Any = None,
        service_name: str = "A2A-Agent",
        resource_aware: bool = False,
        resource_limits: Optional[dict] = None,
        circuit_breaker: Any = None,
        distributed_lock: Any = None,
        client_factory: Optional[Callable[[dict], Any]] = None,
    ):
        self.service_name = service_name
        self._registry = registry
        self._lease_manager = None
        # Backward-compatible constructor arguments. Resource metrics are
        # exposed for observation, but scheduling thresholds are not applied in
        # the SDK/lease layer.
        _ = (resource_aware, resource_limits)
        self._circuit_breaker = circuit_breaker
        self._distributed_lock = distributed_lock
        self._client_factory = client_factory

    # ----- lazily-constructed collaborators --------------------------------------
    @property
    def registry(self):
        if self._registry is None:
            from registry.nacos_manager import NacosRegistry

            self._registry = NacosRegistry()
        return self._registry

    @property
    def lease_manager(self):
        if self._lease_manager is None:
            from commander_agent.agent_leases import AgentLeaseManager

            self._lease_manager = AgentLeaseManager(
                self.registry,
                service_name=self.service_name,
                circuit_breaker=self._circuit_breaker,
                distributed_lock=self._distributed_lock,
            )
        return self._lease_manager

    # ----- dynamic discovery ------------------------------------------------------
    def discover_agents(
        self,
        role: Optional[str] = None,
        status: Optional[str] = "idle",
        required_skill: Optional[str] = None,
        required_model: Optional[str] = None,
    ) -> list:
        """Discover currently available agents, optionally filtered by capability."""
        tags = {}
        if role:
            tags["role"] = role
        if status:
            tags["status"] = status
        instances = self.registry.discover_service(self.service_name, tags or None)

        if required_skill:
            from commander_agent.agent_leases import AgentLeaseManager

            instances = AgentLeaseManager._filter_by_skill(instances, [required_skill])
        if required_model:
            from model_registry import instance_has_model

            instances = [
                inst
                for inst in instances
                if instance_has_model(inst.get("metadata", {}) or {}, required_model)
            ]
        return instances

    def discover_skills(self, role: Optional[str] = None) -> list:
        """Aggregate the distinct skills advertised by available agents."""
        from commander_agent.agent_leases import AgentLeaseManager

        skills = set()
        for inst in self.discover_agents(role=role, status=None):
            metadata = inst.get("metadata", {}) or {}
            for token in AgentLeaseManager._skill_tokens_from_metadata(metadata):
                skills.add(token)
        return sorted(skills)

    def discover_models(self, role: Optional[str] = None) -> list:
        """Aggregate the distinct algorithm models deployed across available agents."""
        from model_registry import models_from_metadata

        models = set()
        for inst in self.discover_agents(role=role, status=None):
            metadata = inst.get("metadata", {}) or {}
            models.update(models_from_metadata(metadata))
        return sorted(models)

    # ----- delayed binding --------------------------------------------------------
    def bind_agent(
        self,
        role: str,
        workflow_id: str,
        work_item: str,
        *,
        required_skill: Optional[str] = None,
        required_skills: Optional[Iterable[str]] = None,
        required_model: Optional[str] = None,
        exclude_keys: Optional[Iterable[str]] = None,
    ):
        """Bind (lease) a single available agent for a task."""
        return self.lease_manager.acquire_one(
            role,
            workflow_id,
            work_item,
            exclude_keys=exclude_keys,
            required_skill=required_skill,
            required_skills=required_skills,
            required_model=required_model,
        )

    def bind_agents(
        self,
        role: str,
        workflow_id: str,
        work_item: str,
        *,
        limit: Optional[int] = None,
        required_skill: Optional[str] = None,
        required_skills: Optional[Iterable[str]] = None,
        required_model: Optional[str] = None,
    ) -> list:
        """Bind (lease) all matching available agents for fan-out tasks."""
        return self.lease_manager.acquire_all(
            role,
            workflow_id,
            work_item,
            limit=limit,
            required_skill=required_skill,
            required_skills=required_skills,
            required_model=required_model,
        )

    def release(self, lease, **kwargs) -> None:
        self.lease_manager.release(lease, **kwargs)

    def release_workflow(self, workflow_id: str) -> None:
        self.lease_manager.release_workflow(workflow_id)

    # ----- task dispatch / result -------------------------------------------------
    def _client_for_target(self, target: dict):
        if self._client_factory is not None:
            return self._client_factory(target)
        from a2a_protocol.client import A2AClient

        return A2AClient(target.get("ip"), target.get("port"))

    def dispatch_task(self, target: dict, payload: dict):
        """Task dispatch: send a sub-task to a specific agent instance."""
        client = self._client_for_target(target)
        return client.send_message(payload)

    def dispatch_to_lease(self, lease, payload: dict):
        """Task dispatch to a bound lease's target agent."""
        return self.dispatch_task(lease.target, payload)

    # ----- exception handling -----------------------------------------------------
    def classify_error(self, error: Any):
        """Classify an execution/link/resource/model error into an error code."""
        from commander_agent.error_classification import classify_agent_error

        return classify_agent_error(error)

    # ----- recovery notification --------------------------------------------------
    def notify_recovery(self, target: dict, notice: dict):
        """Notify one agent to continue after topology rebuild / re-planning."""
        client = self._client_for_target(target)
        return client.notify_recovery(notice)

    def notify_recovery_all(self, targets: Iterable[dict], notice: dict) -> list:
        """Notify a set of agents after re-planning; returns per-agent acks."""
        results = []
        for target in targets:
            try:
                results.append(self.notify_recovery(target, notice))
            except Exception as exc:  # keep notifying the rest
                results.append({"acknowledged": False, "target": target, "error": str(exc)})
        return results

    def close(self) -> None:
        if self._lease_manager is not None and hasattr(self._lease_manager, "close"):
            self._lease_manager.close()
