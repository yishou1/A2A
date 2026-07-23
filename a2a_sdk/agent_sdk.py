from __future__ import annotations

from typing import Any, Iterable, Optional

from a2a_protocol.server import A2ABaseAgent, skills_metadata


class AgentRuntimeSDK:
    """Agent-side facade for the distributed-agent interfaces.

    Wraps :class:`A2ABaseAgent` (task dispatch / result / resources / recovery
    endpoints) and the Nacos registry (registration + heartbeat) behind a single
    entry point, so a business Agent only needs a few lines to expose all of the
    required interfaces.

    Example::

        sdk = AgentRuntimeSDK(
            name="Recon_Agent", description="...", role="recon", port=8002,
            models=[build_model("recon_detector_v1", tags=["detect"])],
        )
        sdk.serve()  # register to Nacos + start HTTP server
    """

    def __init__(
        self,
        name: str,
        description: str,
        role: str,
        port: int,
        *,
        skills: Optional[list] = None,
        models: Optional[Iterable[Any]] = None,
        resource_monitor: Any = None,
        service_name: str = "A2A-Agent",
        registry: Any = None,
        heartbeat_interval: Optional[float] = None,
        extra_metadata: Optional[dict] = None,
    ):
        self.agent = A2ABaseAgent(
            name=name,
            description=description,
            role=role,
            port=port,
            skills=skills,
            resource_monitor=resource_monitor,
            models=models,
        )
        self.service_name = service_name
        self.registry = registry
        self.heartbeat_interval = heartbeat_interval
        self.extra_metadata = dict(extra_metadata or {})
        self._registered_ip: Optional[str] = None

    # ----- pass-through accessors -------------------------------------------------
    @property
    def app(self):
        """The underlying FastAPI application (for embedding / testing)."""
        return self.agent.app

    @property
    def model_registry(self):
        return self.agent.model_registry

    @property
    def skills(self) -> list:
        return self.agent.skills

    # ----- capability management --------------------------------------------------
    def register_model(self, model: Any):
        """Register/deploy an algorithm model on this Agent at runtime."""
        return self.agent.model_registry.register(model)

    def set_model_status(self, model_id: str, status: str) -> None:
        self.agent.model_registry.set_status(model_id, status)

    def set_ready(self, ready: bool = True) -> None:
        self.agent.ready = bool(ready)

    # ----- state / heartbeat ------------------------------------------------------
    def resource_snapshot(self) -> dict:
        """State reporting: full CPU/GPU/memory/energy/network snapshot."""
        return self.agent.resource_snapshot()

    def heartbeat_metadata(self) -> dict:
        """Flat heartbeat metadata (resource + model + run/task state)."""
        return self.agent.heartbeat_metadata()

    def build_registration_metadata(self) -> dict:
        """Assemble the identity + capability + model + resource metadata."""
        metadata = {
            "role": self.agent.role,
            "status": "idle",
            **skills_metadata(self.agent.skills),
            **self.agent.heartbeat_metadata(),
        }
        metadata.update(self.extra_metadata)
        return metadata

    # ----- recovery ---------------------------------------------------------------
    def notify_recovery(self, notice: dict) -> dict:
        """Recovery notification handler (also exposed via POST /recovery/notify)."""
        return self.agent.notify_recovery(notice)

    def recovery_notices(self) -> list:
        return self.agent.recovery_notices()

    # ----- registration lifecycle -------------------------------------------------
    @classmethod
    def from_agent(
        cls,
        agent: A2ABaseAgent,
        *,
        service_name: str = "A2A-Agent",
        registry: Any = None,
        heartbeat_interval: Optional[float] = None,
        extra_metadata: Optional[dict] = None,
    ) -> "AgentRuntimeSDK":
        """Wrap an existing A2ABaseAgent without replacing its business behavior."""
        if not isinstance(agent, A2ABaseAgent):
            raise TypeError("from_agent expects an A2ABaseAgent instance")
        runtime = cls.__new__(cls)
        runtime.agent = agent
        runtime.service_name = service_name
        runtime.registry = registry
        runtime.heartbeat_interval = heartbeat_interval
        runtime.extra_metadata = dict(extra_metadata or {})
        runtime._registered_ip = None
        return runtime

    def _ensure_registry(self):
        if self.registry is None:
            # Imported lazily so the SDK can be used without Nacos installed
            # (e.g. embedding the FastAPI app for local testing).
            from registry.nacos_manager import NacosRegistry

            self.registry = NacosRegistry()
        return self.registry

    def register(self, ip: Optional[str] = None) -> dict:
        """Register this Agent to the registry and start periodic heartbeat."""
        registry = self._ensure_registry()
        if ip is None:
            from registry.nacos_manager import get_host_ip

            ip = get_host_ip()

        metadata = self.build_registration_metadata()
        kwargs = {
            "service_name": self.service_name,
            "ip": ip,
            "port": self.agent.port,
            "metadata": metadata,
            "metadata_provider": self.agent.heartbeat_metadata,
        }
        if self.heartbeat_interval is not None:
            kwargs["heartbeat_interval"] = self.heartbeat_interval
        registry.register_service(**kwargs)
        self._registered_ip = ip
        return metadata

    def serve(self, ip: Optional[str] = None) -> None:
        """Register and then start the blocking HTTP server."""
        self.register(ip=ip)
        self.agent.start()

    def close(self) -> None:
        """Stop heartbeats / release registry resources if any."""
        if self.registry is not None and hasattr(self.registry, "close"):
            self.registry.close()
