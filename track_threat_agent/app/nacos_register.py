"""Optional Nacos service registration for A2A discovery."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict
from urllib import parse, request


LOGGER = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class NacosSettings:
    enabled: bool = False
    server: str = "127.0.0.1:8848"
    namespace: str = "public"
    service_name: str = "A2A-Agent"
    service_ip: str = "127.0.0.1"
    service_port: int = 8102
    heartbeat_interval: float = 5.0
    group_name: str = "DEFAULT_GROUP"
    agent_id: str = "track-threat-group-agent-01"
    role: str = "track_threat"
    status: str = "idle"
    metadata: Dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "NacosSettings":
        service_ip = os.getenv("SERVICE_IP", "127.0.0.1")
        service_port = int(os.getenv("SERVICE_PORT", "8102"))
        service_name = os.getenv("SERVICE_NAME", "A2A-Agent")
        role = os.getenv("AGENT_ROLE", "track_threat")
        status = os.getenv("AGENT_STATUS", "idle")
        enabled = os.getenv("NACOS_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
        base_url = f"http://{service_ip}:{service_port}"
        metadata = {
            "agent_id": os.getenv("AGENT_ID", "track-threat-group-agent-01"),
            "role": role,
            "status": status,
            "agent_type": "situation_awareness_agent",
            "a2a_endpoint": f"{base_url}/a2a/perception-result",
            "send_message_endpoint": f"{base_url}/sendMessage",
            "send_message_stream_endpoint": f"{base_url}/sendMessageStream",
            "health_endpoint": f"{base_url}/health",
            "ready_endpoint": f"{base_url}/ready",
            "metrics_endpoint": f"{base_url}/metrics",
            "agent_card": f"{base_url}/.well-known/agent-card.json",
            "a2a_agent_card": f"{base_url}/.well-known/agent-card",
            "legacy_agent_card": f"{base_url}/agent-card",
            "work_list_endpoint": f"{base_url}/workflows/{{workflow_id}}/work-list",
            "preferred_transport": "A2A_HTTP_JSON,A2A_SSE,HTTP+JSON",
            "skills": "trajectory_tracking,trajectory_prediction,st_gnn_inspired_trajectory_prediction,threat_ranking,dbn_inspired_threat_assessment,group_detection,group_threat_ranking,protected_asset_impact_analysis,xai_evidence_generation",
            "algorithm_family": "adaptive_motion,stgnn_inspired,dbn_inspired,xai",
            "algorithm_levels": "small,medium,large",
            "input_message_types": "perception_result,a2a_task",
            "output_message_types": "track_threat_group_artifact",
            "asset_events": "asset.updated,asset.relationship.updated",
            "artifact_events": "track.updated,threat.updated,track.group.updated,threat.group.updated,threat.ranking.updated,protected.asset.updated,asset.impact.updated",
            "heartbeat_ts": str(int(time.time())),
            "heartbeat_at": _utc_now_iso(),
        }
        return cls(
            enabled=enabled,
            server=os.getenv("NACOS_SERVER", "127.0.0.1:8848"),
            namespace=os.getenv("NACOS_NAMESPACE", "public"),
            service_name=service_name,
            service_ip=service_ip,
            service_port=service_port,
            heartbeat_interval=float(os.getenv("HEARTBEAT_INTERVAL", "5")),
            group_name=os.getenv("NACOS_GROUP", "DEFAULT_GROUP"),
            agent_id=metadata["agent_id"],
            role=role,
            status=status,
            metadata=metadata,
        )


class NacosRegistrar:
    """Best-effort Nacos registration that never blocks service startup."""

    def __init__(self, settings: NacosSettings | None = None) -> None:
        self.settings = settings or NacosSettings.from_env()
        self.client: Any | None = None
        self.heartbeat_task: asyncio.Task | None = None

    async def start(self) -> None:
        if not self.settings.enabled:
            LOGGER.info("Nacos registration disabled")
            return

        try:
            import nacos  # type: ignore
        except Exception as exc:  # pragma: no cover - depends on optional sdk
            LOGGER.warning("Nacos enabled but sdk is not installed: %s", exc)
            return

        try:
            self.client = nacos.NacosClient(self.settings.server, namespace=self.settings.namespace)
            await asyncio.to_thread(self._register_instance)
            self.heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            LOGGER.info(
                "Registered service %s at %s:%s with Nacos %s",
                self.settings.service_name,
                self.settings.service_ip,
                self.settings.service_port,
                self.settings.server,
            )
        except Exception as exc:  # pragma: no cover - depends on external service
            LOGGER.warning("Nacos registration failed; service will continue without registry: %s", exc)
            self.client = None

    async def stop(self) -> None:
        if self.heartbeat_task and not self.heartbeat_task.done():
            self.heartbeat_task.cancel()
            try:
                await self.heartbeat_task
            except asyncio.CancelledError:
                pass
        self.heartbeat_task = None

        if not self.client:
            return
        try:
            await asyncio.to_thread(self._deregister_instance)
        except Exception as exc:  # pragma: no cover - depends on external service
            LOGGER.warning("Nacos deregistration failed: %s", exc)

    def status(self) -> Dict[str, Any]:
        return {
            "enabled": self.settings.enabled,
            "registered": self.client is not None,
            "service_name": self.settings.service_name,
            "service_ip": self.settings.service_ip,
            "service_port": self.settings.service_port,
            "server": self.settings.server,
            "namespace": self.settings.namespace,
            "role": self.settings.role,
            "metadata": self.settings.metadata,
        }

    def set_agent_status(self, status: str, **metadata_updates: str) -> None:
        self.settings.status = status
        self.settings.metadata["status"] = status
        self.settings.metadata.update({key: str(value) for key, value in metadata_updates.items()})
        self.settings.metadata["heartbeat_ts"] = str(int(time.time()))
        self.settings.metadata["heartbeat_at"] = _utc_now_iso()

    def _register_instance(self) -> None:
        assert self.client is not None
        self.client.add_naming_instance(
            self.settings.service_name,
            self.settings.service_ip,
            self.settings.service_port,
            group_name=self.settings.group_name,
            metadata=self.settings.metadata,
            ephemeral=True,
            healthy=True,
            enable=True,
        )

    def _deregister_instance(self) -> None:
        assert self.client is not None
        self.client.remove_naming_instance(
            self.settings.service_name,
            self.settings.service_ip,
            self.settings.service_port,
            group_name=self.settings.group_name,
            ephemeral=True,
        )

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(self.settings.heartbeat_interval)
            if not self.client:
                return
            try:
                await asyncio.to_thread(self._send_heartbeat)
            except Exception as exc:  # pragma: no cover - depends on external service
                LOGGER.warning("Nacos heartbeat failed: %s", exc)

    def _send_heartbeat(self) -> None:
        assert self.client is not None
        self.settings.metadata = self._build_heartbeat_metadata()
        self.client.send_heartbeat(
            self.settings.service_name,
            self.settings.service_ip,
            self.settings.service_port,
            group_name=self.settings.group_name,
            metadata=self.settings.metadata,
            ephemeral=True,
        )
        self._update_instance_metadata_http()

    def _build_heartbeat_metadata(self) -> Dict[str, str]:
        """Build heartbeat metadata without clobbering Commander lease state.

        Commander may mark this instance busy/unavailable directly in Nacos.
        The heartbeat must refresh liveness fields while preserving those
        scheduler-owned status fields.
        """

        metadata = dict(self.settings.metadata)
        current = self._fetch_current_instance_metadata_http()
        if current:
            current_status = current.get("status")
            local_status = metadata.get("status")
            scheduler_owns_state = current_status in {"busy", "unavailable"} and local_status != current_status
            if scheduler_owns_state:
                metadata["status"] = str(current_status)
            for key, value in current.items():
                if key.startswith("lease_") or key.startswith("unavailable_"):
                    metadata[key] = str(value)
            if current_status in {"busy", "unavailable"}:
                self.settings.status = str(current_status)

        metadata["heartbeat_ts"] = str(int(time.time()))
        metadata["heartbeat_at"] = _utc_now_iso()
        return metadata

    def _fetch_current_instance_metadata_http(self) -> Dict[str, str]:
        params = {
            "serviceName": self.settings.service_name,
            "ip": self.settings.service_ip,
            "port": str(self.settings.service_port),
            "groupName": self.settings.group_name,
            "ephemeral": "true",
        }
        if self.settings.namespace and self.settings.namespace != "public":
            params["namespaceId"] = self.settings.namespace
        address = self.settings.server
        if not address.startswith(("http://", "https://")):
            address = f"http://{address}"
        url = f"{address}/nacos/v1/ns/instance?{parse.urlencode(params)}"
        try:
            with request.urlopen(url, timeout=2) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception:
            return {}
        metadata = payload.get("metadata") if isinstance(payload, dict) else {}
        return {str(key): str(value) for key, value in (metadata or {}).items()}

    def _update_instance_metadata_http(self) -> None:
        params = {
            "serviceName": self.settings.service_name,
            "ip": self.settings.service_ip,
            "port": str(self.settings.service_port),
            "clusterName": "None",
            "groupName": self.settings.group_name,
            "metadata": json.dumps(self.settings.metadata, separators=(",", ":")),
            "ephemeral": "true",
            "weight": "1.0",
            "enabled": "true",
            "healthy": "true",
        }
        if self.settings.namespace and self.settings.namespace != "public":
            params["namespaceId"] = self.settings.namespace
        address = self.settings.server
        if not address.startswith(("http://", "https://")):
            address = f"http://{address}"
        url = f"{address}/nacos/v1/ns/instance?{parse.urlencode(params)}"
        req = request.Request(url, method="PUT")
        with request.urlopen(req, timeout=5) as response:
            response.read()
