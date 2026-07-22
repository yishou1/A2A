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
from .resource_monitor import AgentResourceMonitor
from .skills import nacos_skill_ids


LOGGER = logging.getLogger(__name__)
RESOURCE_MONITOR = AgentResourceMonitor()

_LEASE_METADATA_PREFIXES = ("lease_",)
_CIRCUIT_METADATA_PREFIXES = ("circuit_",)
_UNAVAILABLE_METADATA_PREFIXES = ("unavailable_",)
_SCHEDULER_STATUS_VALUES = {"busy", "unavailable"}
_SCHEDULER_METADATA_KEYS = {
    "active_tasks",
    "max_concurrent_tasks",
    "available_task_slots",
    "task_execution_status",
    "scheduling_score",
    "scheduling_reason",
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resource_metadata() -> Dict[str, str]:
    return RESOURCE_MONITOR.heartbeat_metadata()


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
            "resources_endpoint": f"{base_url}/resources",
            "recovery_endpoint": f"{base_url}/recovery/notify",
            "recovery_status_endpoint": f"{base_url}/recovery/status",
            "algorithms_endpoint": f"{base_url}/algorithms",
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
            "algorithm_profile": "kalman_stgnn_dbn_group_asset_xai",
            "model_status": model_status,
            "preferred_transport": "A2A_HTTP_JSON,A2A_SSE,HTTP+JSON",
            "skills": nacos_skill_ids(),
            "algorithm_family": "kalman,st_gnn,dbn,asset_impact,group_detection,xai",
            "runtime_providers": "covariance_kalman_cv_filter,torchscript_st_gnn,dbn_risk_state_calibration_runtime,physical_relation_complete_link_clustering,predicted_path_asset_proximity",
            "fallback_providers": "adaptive_cv_ca_ct_physics",
            "algorithm_levels": "small,medium,large",
            "object_types": "aircraft,ship,uav,unknown",
            "input_message_types": "perception_result,tactical_intelligence_result,a2a_task",
            "output_message_types": "track_threat_group_artifact,track.updated,threat.updated,track.group.updated,threat.group.updated,threat.ranking.updated,asset.impact.updated",
            "ranking_item_types": "track,group,asset_impact",
            "scene_contract": "protected_zone_lat,protected_zone_lon,protected_radius_m,protected_assets",
            "minimum_detection_fields": "detection_id,object_type,timestamp,lat,lon,speed,heading,confidence",
            "dbn_parameter_schema": "dbn_risk_model/v1",
            "dbn_parameter_model": "dbn-risk-attention-v1",
            "group_lifecycle_states": "tentative,confirmed,coasting",
            "tracking_lifecycle_states": "tentative,confirmed,coasting,lost",
            "algorithm_boundary": "tracking,prediction,group_detection,risk_ranking,protected_asset_impact,xai",
            "upstream_boundary": "sensor_processing,perception_fusion",
            "downstream_boundary": "semantic_reasoning,mission_planning,engagement_decision",
            "st_gnn_aircraft_model_configured": str(_st_gnn_aircraft_configured()).lower(),
            "st_gnn_ship_model_configured": str(_st_gnn_ship_configured()).lower(),
            "st_gnn_required": os.getenv("ST_GNN_REQUIRED", "false"),
            "st_gnn_enforce_release_gate": os.getenv("ST_GNN_ENFORCE_RELEASE_GATE", "false"),
            "st_gnn_max_inference_ms": os.getenv("ST_GNN_MAX_INFERENCE_MS", "200"),
            "asset_events": "asset.updated,asset.relationship.updated",
            "artifact_events": "track.updated,threat.updated,track.group.updated,threat.group.updated,threat.ranking.updated,protected.asset.updated,asset.impact.updated",
            "algorithm_execution_location": "agent_process",
            "algorithm_library_transport": "none",
            "algorithm_loading_mode": "agent_local_model_bundle",
            "remote_algorithm_execution": "false",
            "algorithm_contract_version": "track_threat_algorithms/v1",
            "internal_workflow_engine": "false",
            "active_tasks": "0",
            "max_concurrent_tasks": "1",
            "available_task_slots": "1",
            "task_execution_status": "idle",
            "quality_tasks_completed": "0",
            "quality_tasks_failed": "0",
            "quality_success_rate": "1.000000",
            "quality_avg_latency_ms": "0.000",
            "heartbeat_ts": str(int(time.time())),
            "heartbeat_at": _utc_now_iso(),
        }
        metadata.update(_resource_metadata())
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
        self.registered = False
        self.registration_transport: str | None = None
        self.heartbeat_success_count = 0
        self.heartbeat_failure_count = 0
        self.last_heartbeat_success_at: str | None = None
        self.last_heartbeat_error: str | None = None
        self.last_heartbeat_transport: str | None = None

    async def start(self) -> None:
        if not self.settings.enabled:
            LOGGER.info("Nacos registration disabled")
            return

        try:
            try:
                import nacos  # type: ignore

                self.client = nacos.NacosClient(self.settings.server, namespace=self.settings.namespace)
            except Exception as exc:  # pragma: no cover - optional sdk
                LOGGER.warning("Nacos sdk unavailable; trying HTTP registration: %s", exc)
                self.client = None
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
            self.registered = False

    async def stop(self) -> None:
        if self.heartbeat_task and not self.heartbeat_task.done():
            self.heartbeat_task.cancel()
            try:
                await self.heartbeat_task
            except asyncio.CancelledError:
                pass
        self.heartbeat_task = None

        if not self.registered:
            return
        try:
            await asyncio.to_thread(self._deregister_instance)
        except Exception as exc:  # pragma: no cover - depends on external service
            LOGGER.warning("Nacos deregistration failed: %s", exc)

    def status(self) -> Dict[str, Any]:
        return {
            "enabled": self.settings.enabled,
            "registered": self.registered,
            "registration_transport": self.registration_transport,
            "service_name": self.settings.service_name,
            "service_ip": self.settings.service_ip,
            "service_port": self.settings.service_port,
            "server": self.settings.server,
            "namespace": self.settings.namespace,
            "role": self.settings.role,
            "heartbeat_success_count": self.heartbeat_success_count,
            "heartbeat_failure_count": self.heartbeat_failure_count,
            "last_heartbeat_success_at": self.last_heartbeat_success_at,
            "last_heartbeat_error": self.last_heartbeat_error,
            "last_heartbeat_transport": self.last_heartbeat_transport,
            "metadata": self.settings.metadata,
        }

    def set_agent_status(self, status: str, **metadata_updates: str) -> None:
        self.settings.status = status
        self.settings.metadata["status"] = status
        active_tasks = 1 if status == "busy" else 0
        self.settings.metadata["active_tasks"] = str(active_tasks)
        self.settings.metadata["max_concurrent_tasks"] = "1"
        self.settings.metadata["available_task_slots"] = str(1 - active_tasks)
        self.settings.metadata["task_execution_status"] = (
            "saturated" if active_tasks else ("unavailable" if status == "unavailable" else "idle")
        )
        self.settings.metadata.update({key: str(value) for key, value in metadata_updates.items()})
        self.settings.metadata["heartbeat_ts"] = str(int(time.time()))
        self.settings.metadata["heartbeat_at"] = _utc_now_iso()

    def update_runtime_metrics(
        self,
        *,
        tasks_completed: int,
        tasks_failed: int,
        average_latency_ms: float,
        active_tasks: int,
    ) -> None:
        completed = max(0, int(tasks_completed))
        failed = max(0, int(tasks_failed))
        attempts = completed + failed
        active = min(1, max(0, int(active_tasks)))
        self.settings.metadata.update(
            {
                "quality_tasks_completed": str(completed),
                "quality_tasks_failed": str(failed),
                "quality_success_rate": f"{(completed / attempts) if attempts else 1.0:.6f}",
                "quality_avg_latency_ms": f"{max(0.0, float(average_latency_ms)):.3f}",
                "active_tasks": str(active),
                "max_concurrent_tasks": "1",
                "available_task_slots": str(1 - active),
                "task_execution_status": "saturated" if active else "idle",
            }
        )

    def record_task_outcome(self, *, success: bool, latency_ms: float = 0.0) -> None:
        completed = int(self.settings.metadata.get("quality_tasks_completed", "0") or 0)
        failed = int(self.settings.metadata.get("quality_tasks_failed", "0") or 0)
        previous_attempts = completed + failed
        previous_average = float(
            self.settings.metadata.get("quality_avg_latency_ms", "0") or 0.0
        )
        if success:
            completed += 1
        else:
            failed += 1
        average = (
            (previous_average * previous_attempts + max(0.0, float(latency_ms)))
            / (previous_attempts + 1)
        )
        self.update_runtime_metrics(
            tasks_completed=completed,
            tasks_failed=failed,
            average_latency_ms=average,
            active_tasks=0,
        )

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
        if self.client is not None:
            try:
                self.client.add_naming_instance(
                    self.settings.service_name,
                    self.settings.service_ip,
                    self.settings.service_port,
                    cluster_name="DEFAULT",
                    group_name=self.settings.group_name,
                    metadata=self.settings.metadata,
                    ephemeral=True,
                    healthy=True,
                    enable=True,
                )
                self.registered = True
                self.registration_transport = "sdk"
                return
            except Exception as exc:
                LOGGER.warning("Nacos SDK registration failed; trying HTTP fallback: %s", exc)
        self._register_instance_http()
        self.registered = True
        self.registration_transport = "http"

    def _deregister_instance(self) -> None:
        if self.client is not None:
            self.client.remove_naming_instance(
                self.settings.service_name,
                self.settings.service_ip,
                self.settings.service_port,
                cluster_name="DEFAULT",
                group_name=self.settings.group_name,
                ephemeral=True,
            )
        else:
            self._instance_http_request("DELETE", include_metadata=False)
        self.registered = False

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(self.settings.heartbeat_interval)
            if not self.registered:
                return
            try:
                await asyncio.to_thread(self._send_heartbeat)
            except Exception as exc:  # pragma: no cover - depends on external service
                LOGGER.warning("Nacos heartbeat failed: %s", exc)

    def _send_heartbeat(self) -> None:
        self.settings.metadata = self._build_heartbeat_metadata()
        transport = "http"
        try:
            if self.client is not None:
                try:
                    self.client.send_heartbeat(
                        self.settings.service_name,
                        self.settings.service_ip,
                        self.settings.service_port,
                        cluster_name="DEFAULT",
                        group_name=self.settings.group_name,
                        metadata=self.settings.metadata,
                        ephemeral=True,
                    )
                    transport = "sdk"
                except Exception as exc:
                    LOGGER.warning("Nacos SDK heartbeat failed; trying HTTP fallback: %s", exc)
                    self._send_heartbeat_http()
            else:
                self._send_heartbeat_http()
            self._update_instance_metadata_http()
        except Exception as exc:
            self.heartbeat_failure_count += 1
            self.last_heartbeat_error = str(exc)
            raise
        self.heartbeat_success_count += 1
        self.last_heartbeat_success_at = _utc_now_iso()
        self.last_heartbeat_error = None
        self.last_heartbeat_transport = transport

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
            self._sync_metadata_keys(metadata, current, _SCHEDULER_METADATA_KEYS)

            if not local_ready_false:
                self._sync_metadata_prefixes(metadata, current, _UNAVAILABLE_METADATA_PREFIXES)
                scheduler_released_instance = current_status == "idle" and local_status in _SCHEDULER_STATUS_VALUES
                scheduler_holds_instance = current_status in _SCHEDULER_STATUS_VALUES
                if scheduler_holds_instance or scheduler_released_instance:
                    metadata["status"] = current_status
                    self.settings.status = current_status

        metadata.update(_resource_metadata())
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

    @staticmethod
    def _sync_metadata_keys(
        metadata: Dict[str, str],
        current: Dict[str, str],
        keys: set[str],
    ) -> None:
        for key in keys:
            if key in current:
                metadata[key] = str(current[key])
            elif key in {"scheduling_score", "scheduling_reason"}:
                metadata.pop(key, None)

    def _fetch_current_instance_metadata_http(self) -> Dict[str, str]:
        params = {
            "serviceName": self.settings.service_name,
            "ip": self.settings.service_ip,
            "port": str(self.settings.service_port),
            "clusterName": "DEFAULT",
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
            "clusterName": "DEFAULT",
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
        try:
            with request.urlopen(req, timeout=5) as response:
                response.read()
        except Exception as exc:
            LOGGER.warning("Nacos metadata PUT failed; refreshing registration: %s", exc)
            self._register_instance_http()

    def _register_instance_http(self) -> None:
        self._instance_http_request("POST", include_metadata=True)

    def _instance_http_request(self, method: str, *, include_metadata: bool) -> None:
        params = {
            "serviceName": self.settings.service_name,
            "ip": self.settings.service_ip,
            "port": str(self.settings.service_port),
            "clusterName": "DEFAULT",
            "groupName": self.settings.group_name,
            "ephemeral": "true",
            "weight": "1.0",
            "enabled": "true",
            "healthy": "true",
        }
        if include_metadata:
            params["metadata"] = json.dumps(self.settings.metadata, separators=(",", ":"))
        if self.settings.namespace and self.settings.namespace != "public":
            params["namespaceId"] = self.settings.namespace
        req = request.Request(self._nacos_url("/nacos/v1/ns/instance", params), method=method)
        with request.urlopen(req, timeout=5) as response:
            response.read()

    def _send_heartbeat_http(self) -> None:
        beat = {
            "ip": self.settings.service_ip,
            "port": self.settings.service_port,
            "serviceName": self.settings.service_name,
            "cluster": "DEFAULT",
            "weight": 1.0,
            "scheduled": False,
            "metadata": self.settings.metadata,
        }
        params = {
            "serviceName": self.settings.service_name,
            "groupName": self.settings.group_name,
            "beat": json.dumps(beat, separators=(",", ":")),
        }
        if self.settings.namespace and self.settings.namespace != "public":
            params["namespaceId"] = self.settings.namespace
        req = request.Request(self._nacos_url("/nacos/v1/ns/instance/beat", params), method="PUT")
        with request.urlopen(req, timeout=5) as response:
            response.read()

    def _nacos_url(self, path: str, params: Dict[str, str]) -> str:
        address = self.settings.server
        if not address.startswith(("http://", "https://")):
            address = f"http://{address}"
        return f"{address}{path}?{parse.urlencode(params)}"
