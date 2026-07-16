"""Optional Nacos service registration for A2A discovery."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict
from urllib import parse, request

from .agent_model_registry import configured_model_metadata
from .skills import nacos_skill_ids


LOGGER = logging.getLogger(__name__)

_LEASE_METADATA_PREFIXES = ("lease_",)
_CIRCUIT_METADATA_PREFIXES = ("circuit_",)
_UNAVAILABLE_METADATA_PREFIXES = ("unavailable_",)
_SCHEDULER_STATUS_VALUES = {"busy", "unavailable"}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _embedded_model_dir(name: str) -> Path:
    return Path(__file__).resolve().parents[1] / "models" / "track_threat" / name


def _st_gnn_aircraft_configured() -> bool:
    return bool(
        os.getenv("ST_GNN_AIRCRAFT_MODEL_DIR")
        or os.getenv("ST_GNN_MODEL_DIR")
        or _embedded_model_dir("st_gnn_aircraft_kaggle_v1").is_dir()
        or _embedded_model_dir("st_gnn_aircraft_kaggle_v1_candidate").is_dir()
        or _embedded_model_dir("st_gnn_aircraft_v1").is_dir()
    )


def _st_gnn_ship_configured() -> bool:
    return bool(
        os.getenv("ST_GNN_SHIP_MODEL_DIR")
        or _embedded_model_dir("st_gnn_ship_kaggle_v1").is_dir()
        or _embedded_model_dir("st_gnn_ship_v1").is_dir()
    )


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
        model_status = os.getenv("MODEL_STATUS", "no_model")
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
            "state_summary_endpoint": f"{base_url}/state/summary",
            "agent_card": f"{base_url}/.well-known/agent-card.json",
            "a2a_agent_card": f"{base_url}/.well-known/agent-card",
            "legacy_agent_card": f"{base_url}/agent-card",
            "work_list_endpoint": f"{base_url}/workflows/{{workflow_id}}/work-list",
            "input_schema_url": f"{base_url}/schema/input",
            "output_schema_url": f"{base_url}/schema/output",
            "state_schema_url": f"{base_url}/schema/state",
            "capability_version": "track_threat_agent_v1",
            "artifact_schema_version": "track_threat_group_artifact/v1",
            "input_schema_version": "perception_result/v1",
            "algorithm_profile": "kalman_imm_stgnn_dbn_asset_xai",
            "model_status": model_status,
            "preferred_transport": "A2A_HTTP_JSON,A2A_SSE,HTTP+JSON",
            "skills": nacos_skill_ids(),
            "algorithm_family": "kalman,imm,st_gnn,dbn,asset_impact,group_detection,xai",
            "runtime_providers": "covariance_kalman_cv_filter,imm_multi_model_motion_prediction,local_numpy_st_gnn_message_passing,dbn_risk_state_calibration_runtime,asset_track_relation_graph",
            "fallback_providers": "baseline_motion_provider",
            "algorithm_levels": "small,medium,large",
            "object_types": "aircraft,ship,uav,unknown",
            "input_message_types": "perception_result,tactical_intelligence_result,a2a_task",
            "output_message_types": "track_threat_group_artifact,track.updated,threat.updated,track.group.updated,threat.group.updated,threat.ranking.updated,asset.impact.updated",
            "ranking_item_types": "track,group,asset_impact",
            "scene_contract": "protected_zone_lat,protected_zone_lon,protected_radius_m,protected_assets",
            "minimum_detection_fields": "detection_id,object_type,timestamp,lat,lon,speed,heading,confidence",
            "st_gnn_aircraft_model_configured": str(_st_gnn_aircraft_configured()).lower(),
            "st_gnn_ship_model_configured": str(_st_gnn_ship_configured()).lower(),
            "st_gnn_required": os.getenv("ST_GNN_REQUIRED", "false"),
            "st_gnn_max_inference_ms": os.getenv("ST_GNN_MAX_INFERENCE_MS", "200"),
            "asset_events": "asset.updated,asset.relationship.updated",
            "artifact_events": "track.updated,threat.updated,track.group.updated,threat.group.updated,threat.ranking.updated,protected.asset.updated,asset.impact.updated",
            "algorithm_execution_location": "agent_process",
            "algorithm_library_transport": "none",
            "internal_workflow_engine": "false",
            "heartbeat_ts": str(int(time.time())),
            "heartbeat_at": _utc_now_iso(),
        }
        metadata.update(configured_model_metadata())
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

    def set_model_registry(self, model_registry: Any) -> None:
        """Publish locally loaded model state; Nacos never executes the models."""
        self.settings.metadata.update(
            {key: str(value) for key, value in model_registry.metadata().items()}
        )
        self.settings.metadata["model_status"] = (
            "model_loaded"
            if model_registry.ready_model_ids()
            else "no_model"
        )

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
        """Build heartbeat metadata without clobbering Commander scheduler state.

        Commander may mark this instance busy/unavailable directly in Nacos,
        attach lease fields, and now attach circuit-breaker fields as well.
        The heartbeat must refresh liveness fields while preserving those
        scheduler-owned fields. It must also stop replaying stale lease fields
        after Commander releases the instance back to idle.
        """

        metadata = dict(self.settings.metadata)
        current = self._fetch_current_instance_metadata_http()
        if current:
            current_status = str(current.get("status", "")).lower()
            local_status = str(metadata.get("status", "")).lower()
            local_ready_false = (
                local_status == "unavailable"
                and str(metadata.get("unavailable_reason", "")) == "ready=false"
            )

            self._sync_metadata_prefixes(metadata, current, _LEASE_METADATA_PREFIXES)
            self._sync_metadata_prefixes(metadata, current, _CIRCUIT_METADATA_PREFIXES)

            if not local_ready_false:
                self._sync_metadata_prefixes(metadata, current, _UNAVAILABLE_METADATA_PREFIXES)
                scheduler_released_instance = current_status == "idle" and local_status in _SCHEDULER_STATUS_VALUES
                scheduler_holds_instance = current_status in _SCHEDULER_STATUS_VALUES
                if scheduler_holds_instance or scheduler_released_instance:
                    metadata["status"] = current_status
                    self.settings.status = current_status

        metadata["heartbeat_ts"] = str(int(time.time()))
        metadata["heartbeat_at"] = _utc_now_iso()
        return metadata

    @staticmethod
    def _sync_metadata_prefixes(
        metadata: Dict[str, str],
        current: Dict[str, str],
        prefixes: tuple[str, ...],
    ) -> None:
        for key in list(metadata):
            if key.startswith(prefixes):
                metadata.pop(key, None)
        for key, value in current.items():
            if key.startswith(prefixes):
                metadata[key] = str(value)

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
