"""FastAPI service for the standalone A2A-compatible simulation Agent."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from a2a_protocol.messages import build_task_error_response, build_task_response

from .a2a_runtime import A2ARuntimeState
from .agent_model_registry import build_agent_model_registry
from .algorithm_provider import PlanAlgorithmProvider
from .amos_adapter import build_integration_events
from .asset_impact_analyzer import AssetImpactAnalyzer
from .group_detector import GroupDetector
from .intelligence_adapter import (
    convert_intelligence_to_detections,
    extract_scene_from_intelligence,
    is_intelligence_format,
    reset_adapter_cache,
)
from .learned_predictor import LearnedTrajectoryPredictor
from .model_runtime import ModelBundleLoader, TrackSTGNNRuntime
from .models import Detection, ProtectedAsset
from .nacos_register import NacosRegistrar
from .scenario_generator import generate_auto_demo_frame
from .skills import SUPPORTED_SKILLS, agent_card_skills
from .st_gnn_predictor import STGNNTrajectoryPredictor
from .state_store import FileStateStore, STATE_SCHEMA_VERSION
from .threat_ranker import ThreatRanker
from .tracker import MultiTargetTracker


BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = BACKEND_DIR.parent
SAMPLE_DATA_DIR = BACKEND_DIR / "sample_data"
DEFAULT_STATE_PATH = PROJECT_DIR / ".a2a_state" / "track_threat_agent_state.json"
DEFAULT_LEARNED_MODEL_PATH = BACKEND_DIR / "models" / "trajectory_predictor_aircraft_short.json"
INPUT_SCHEMA_VERSION = "perception_result/v1"
ARTIFACT_SCHEMA_VERSION = "track_threat_group_artifact/v1"
STATE_SUMMARY_SCHEMA_VERSION = 1


registrar = NacosRegistrar()
state_store = FileStateStore(os.getenv("TRACK_THREAT_STATE_PATH") or DEFAULT_STATE_PATH)


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    _restore_state_snapshot()
    await registrar.start()
    try:
        yield
    finally:
        await _stop_auto_demo()
        await registrar.stop()


app = FastAPI(
    title="Track Threat Group Agent Demo",
    version="0.1.0",
    description="Standalone simulation-only tracking, prediction, group detection, protected-asset impact analysis, and risk-priority ranking.",
    lifespan=lifespan,
)

tracker = MultiTargetTracker()
ranker = ThreatRanker()
group_detector = GroupDetector()
impact_analyzer = AssetImpactAnalyzer()
graph_predictor = STGNNTrajectoryPredictor()
learned_predictor = LearnedTrajectoryPredictor(os.getenv("TRACK_THREAT_LEARNED_MODEL_PATH") or DEFAULT_LEARNED_MODEL_PATH)
st_gnn_model_bundle = ModelBundleLoader(os.getenv("ST_GNN_MODEL_DIR"))
trained_st_gnn_runtime = TrackSTGNNRuntime.from_env(BACKEND_DIR / "models" / "track_threat")
algorithm_provider = PlanAlgorithmProvider(
    tracker,
    graph_predictor,
    ranker,
    impact_analyzer,
    group_detector,
    learned_predictor=learned_predictor,
    trained_st_gnn_runtime=trained_st_gnn_runtime,
)
model_registry = build_agent_model_registry(learned_predictor, trained_st_gnn_runtime)
registrar.set_model_registry(model_registry)
runtime = A2ARuntimeState(agent_name="track-threat-group-agent", role=registrar.settings.role)
processing_lock = asyncio.Lock()
auto_demo_task: asyncio.Task | None = None
last_artifact: Dict[str, Any] = {
    "protected_assets": [],
    "tracks": [],
    "threats": [],
    "asset_impacts": [],
    "groups": [],
    "unified_threat_ranking": [],
    "events": [],
    "summary": {"track_count": 0, "threat_count": 0, "group_count": 0, "protected_asset_count": 0},
}

if SAMPLE_DATA_DIR.exists():
    app.mount("/sample-data", StaticFiles(directory=SAMPLE_DATA_DIR), name="sample-data")


class PerceptionResultRequest(BaseModel):
    task_id: str
    message_type: Literal["perception_result"] = "perception_result"
    algorithm_level: Literal["small", "medium", "large"] = "medium"
    scene: Dict[str, Any] = Field(default_factory=dict)
    detections: List[Detection] = Field(default_factory=list)


def verify_a2a_token(authorization: str | None = Header(default=None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return authorization.split("Bearer ", 1)[1]


class ConnectionManager:
    def __init__(self) -> None:
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        with contextlib.suppress(ValueError):
            self.active_connections.remove(websocket)

    async def broadcast(self, message: Dict[str, Any]) -> None:
        disconnected = []
        for websocket in list(self.active_connections):
            try:
                await websocket.send_json(message)
            except RuntimeError:
                disconnected.append(websocket)
        for websocket in disconnected:
            self.disconnect(websocket)


manager = ConnectionManager()


@app.get("/")
def root() -> Dict[str, Any]:
    return {
        "service": "amos-track-threat-demo",
        "mode": "backend-agent-only",
        "agent_card": "/.well-known/agent-card.json",
        "a2a_endpoint": "/a2a/perception-result",
        "health_endpoint": "/health",
        "note": "Standalone backend Agent. Use HTTP/A2A outputs directly; AMOS visualization is optional via a separate adapter.",
    }


@app.get("/health")
def health() -> Dict[str, Any]:
    runtime_snapshot = runtime_status()
    return {
        "status": "ok",
        "ready": _effective_ready(),
        "agent": runtime.agent_name,
        "role": runtime.role,
        "agent_status": runtime_snapshot["agent_status"],
        "active_track_count": len(tracker.tracks),
        "active_group_count": len(group_detector.groups),
        "processed_task_count": runtime_snapshot["processed_task_count"],
        "failed_task_count": runtime_snapshot["failed_task_count"],
        "cached_work_item_count": runtime_snapshot["cached_work_item_count"],
        "current_workflow_id": runtime_snapshot["current_workflow_id"],
        "current_work_item": runtime_snapshot["current_work_item"],
        "algorithm_provider": runtime_snapshot["algorithm_provider"],
        "model_registry": model_registry.snapshot(),
        "state_snapshot": {
            "path": str(state_store.path),
            "exists": state_store.path.exists(),
        },
        "nacos": registrar.status(),
        "safety_boundary": "simulation-only risk priority, no weapon control",
    }


@app.get("/ready")
def ready() -> Dict[str, Any]:
    runtime_snapshot = runtime_status()
    return {
        "ready": _effective_ready(),
        "agent": runtime.agent_name,
        "role": runtime.role,
        "agent_status": runtime_snapshot["agent_status"],
        "active_tasks": runtime_snapshot["active_task_count"],
        "current_workflow_id": runtime_snapshot["current_workflow_id"],
        "current_work_item": runtime_snapshot["current_work_item"],
        "model_status": trained_st_gnn_runtime.status(),
        "model_registry": model_registry.snapshot(),
    }


@app.post("/lifecycle/ready")
def set_ready(payload: Dict[str, Any]) -> Dict[str, Any]:
    is_ready = bool(payload.get("ready", True))
    runtime.set_ready(is_ready)
    if not is_ready:
        registrar.set_agent_status("unavailable", unavailable_reason="ready=false")
    elif runtime.agent_status != "busy":
        runtime.mark_idle()
        registrar.set_agent_status("idle", unavailable_reason="")
    return ready()


@app.get("/metrics")
def metrics() -> Dict[str, Any]:
    snapshot = runtime.metrics_snapshot()
    snapshot.update(
        {
            "total_requests": snapshot.get("processed_task_count", 0) + snapshot.get("failed_task_count", 0),
            "successful_requests": snapshot.get("processed_task_count", 0),
            "failed_requests": snapshot.get("failed_task_count", 0),
            "active_track_count": len(tracker.tracks),
            "active_group_count": len(group_detector.groups),
            "active_asset_impact_count": len(last_artifact.get("asset_impacts", [])),
            "algorithm_provider": algorithm_provider.mode,
            "model_status": _model_status(),
            "last_task_id": last_artifact.get("task_id"),
            "state_snapshot_exists": state_store.path.exists(),
        }
    )
    return snapshot


def runtime_status() -> Dict[str, Any]:
    return runtime.snapshot(algorithm_provider=algorithm_provider.mode)


def _effective_ready() -> bool:
    return bool(runtime.ready and trained_st_gnn_runtime.ready)


def _agent_card_payload() -> Dict[str, Any]:
    service_url = f"http://{registrar.settings.service_ip}:{registrar.settings.service_port}"
    return {
        "name": "track-threat-group-agent",
        "agent_name": "track-threat-group-agent-demo",
        "description": "Standalone simulation-only multi-target tracking, prediction, protected-asset impact analysis, grouping, and risk-priority ranking.",
        "url": f"{service_url}/a2a/perception-result",
        "preferredTransport": "HTTP+JSON",
        "additionalInterfaces": [
            {"url": f"{service_url}/a2a/perception-result", "transport": "HTTP+JSON"},
            {"url": f"{service_url}/a2a/intelligence-result", "transport": "HTTP+JSON", "note": "Accepts TacticalIntelligenceAgent format with targets array"},
            {"url": f"{service_url}/sendMessage", "transport": "A2A_HTTP_JSON"},
            {"url": f"{service_url}/sendMessageStream", "transport": "A2A_SSE"},
            {"url": f"{service_url}/ws", "transport": "WEBSOCKET"},
        ],
        "version": "0.2.0",
        "protocolVersion": "0.3.0",
        "provider": {
            "organization": "Track Threat Demo",
            "url": service_url,
        },
        "capabilities": list(SUPPORTED_SKILLS),
        "a2a_capabilities": {
            "streaming": True,
            "pushNotifications": False,
            "stateTransitionHistory": False,
        },
        "algorithm_levels": ["small", "medium", "large"],
        "input_message_types": ["perception_result", "tactical_intelligence"],
        "output_message_types": ["track_threat_group_artifact"],
        "defaultInputModes": ["application/json"],
        "defaultOutputModes": ["application/json"],
        "skills": agent_card_skills(),
        "a2a": {
            "endpoint": "/a2a/perception-result",
            "method": "POST",
            "sendMessageEndpoint": "/sendMessage",
            "sendMessageStreamEndpoint": "/sendMessageStream",
            "workListEndpoint": "/workflows/{workflow_id}/work-list",
            "artifact_events": [
                "asset.updated",
                "asset.relationship.updated",
                "track.updated",
                "threat.updated",
                "track.group.updated",
                "threat.group.updated",
                "threat.ranking.updated",
                "protected.asset.updated",
                "asset.impact.updated",
            ],
        },
        "role": registrar.settings.role,
        "sendMessageEndpoint": "/sendMessage",
        "sendMessageStreamEndpoint": "/sendMessageStream",
        "workListEndpoint": "/workflows/{workflow_id}/work-list",
        "healthEndpoint": "/health",
        "readyEndpoint": "/ready",
        "modelsEndpoint": "/models",
            "metricsEndpoint": "/metrics",
            "stateSummaryEndpoint": "/state/summary",
            "inputSchemaEndpoint": "/schema/input",
            "outputSchemaEndpoint": "/schema/output",
            "securitySchemes": {
            "openIdConnect": {
                "type": "openIdConnect",
                "authorizationUrl": "http://127.0.0.1:8080/auth",
                "tokenUrl": "http://127.0.0.1:8080/post",
            }
        },
        "discovery": {
            "nacos_service_name": registrar.settings.service_name,
            "nacos_enabled": registrar.settings.enabled,
            "nacos_metadata": registrar.settings.metadata,
        },
        "schema": {
            "input_schema_version": INPUT_SCHEMA_VERSION,
            "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
            "state_summary_schema_version": STATE_SUMMARY_SCHEMA_VERSION,
        },
        "model_status": _model_status(),
        "model_registry": model_registry.snapshot(),
        "execution": {
            "mode": "in_process_model_execution",
            "model_ownership": "track_threat_agent",
            "internal_workflow_engine": False,
            "network_algorithm_calls": False,
            "note": "workflow_id/work_item are accepted only as external A2A correlation fields",
        },
        "safety_boundary": [
            "no real weapon control",
            "no attack recommendation",
            "no guidance or engagement decision",
            "threat/risk means demo situation-awareness priority only",
        ],
    }


@app.get("/agent-card")
def agent_card() -> Dict[str, Any]:
    return _agent_card_payload()


@app.get("/.well-known/agent-card.json")
def well_known_agent_card() -> Dict[str, Any]:
    return _agent_card_payload()


@app.get("/.well-known/agent-card")
def well_known_a2a_agent_card() -> Dict[str, Any]:
    return _agent_card_payload()


@app.get("/.well-known/agent.json")
def well_known_agent_json() -> Dict[str, Any]:
    return _agent_card_payload()


@app.get("/models")
def models() -> Dict[str, Any]:
    """Report models loaded by this Agent; execution remains in-process."""
    return model_registry.snapshot()


@app.get("/schema/input")
def input_schema() -> Dict[str, Any]:
    return {
        "schema_version": INPUT_SCHEMA_VERSION,
        "message_type": "perception_result",
        "required_top_level_fields": ["task_id", "message_type", "algorithm_level", "scene", "detections"],
        "scene_fields": [
            "protected_zone_lat",
            "protected_zone_lon",
            "protected_radius_m",
            "protected_assets",
        ],
        "protected_asset_fields": [
            "asset_id",
            "asset_name",
            "asset_type",
            "lat",
            "lon",
            "protection_radius_m",
            "criticality",
            "priority",
            "vulnerability",
        ],
        "minimum_detection_fields": [
            "detection_id",
            "object_type",
            "timestamp",
            "lat",
            "lon",
            "speed",
            "heading",
            "confidence",
        ],
        "json_schema": PerceptionResultRequest.model_json_schema(),
        "safety_boundary": "simulation-only situation-awareness input; no weapon-control command accepted",
    }


@app.get("/schema/output")
def output_schema() -> Dict[str, Any]:
    return {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "message_type": "track_threat_group_artifact",
        "artifact_fields": [
            "task_id",
            "artifact_schema_version",
            "trace",
            "protected_assets",
            "tracks",
            "threats",
            "asset_impacts",
            "groups",
            "unified_threat_ranking",
            "decision_risk_assessments",
            "events",
            "summary",
        ],
        "unified_threat_ranking_fields": [
            "rank",
            "item_type",
            "item_id",
            "score",
            "level",
            "reason",
            "evidence",
            "factors",
        ],
        "decision_risk_assessment_fields": [
            "target_id",
            "source_id",
            "source_item_type",
            "priority",
            "risk",
            "threat_score",
            "probability",
            "rationale",
            "triggered_rules",
            "evidence",
        ],
        "asset_impact_fields": [
            "impact_id",
            "protected_asset_id",
            "source_track_id",
            "score",
            "level",
            "closest_distance_m",
            "predicted_closest_distance_m",
            "eta_to_protected_radius_s",
            "will_enter_protection_radius",
            "predicted_min_distance_margin_m",
        ],
        "event_types": [
            "asset.updated",
            "asset.relationship.updated",
            "track.updated",
            "threat.updated",
            "track.group.updated",
            "threat.group.updated",
            "threat.ranking.updated",
            "protected.asset.updated",
            "asset.impact.updated",
        ],
        "safety_boundary": "simulation-only situation-awareness artifact; threat/risk means attention priority only",
    }


@app.get("/schema/state")
def state_schema() -> Dict[str, Any]:
    return {
        "schema_version": STATE_SUMMARY_SCHEMA_VERSION,
        "state_store_schema_version": STATE_SCHEMA_VERSION,
        "fields": [
            "status",
            "agent",
            "role",
            "runtime",
            "tracks",
            "groups",
            "protected_assets",
            "asset_impacts",
            "last_artifact",
            "model_status",
            "state_snapshot",
            "schema",
        ],
    }


@app.post("/a2a/perception-result")
async def perception_result(payload: PerceptionResultRequest) -> Dict[str, Any]:
    async with processing_lock:
        result = _process_payload(payload)
    await _broadcast_events(result["artifact"]["events"], result["artifact"])
    return result


@app.post("/a2a/intelligence-result")
async def intelligence_result(payload: Dict[str, Any]) -> Dict[str, Any]:
    """接收同门 TacticalIntelligenceAgent 格式的情报数据。

    输入格式::

        {
          "targets": [
            {
              "track_id": "T-0001",
              "class": "bus",
              "geo": {"lat": 30.512, "lon": 114.381, "alt_m": 120.0},
              "confidence": 0.9882,
              ...
            }
          ],
          "mission_id": "OP-IRON-VALLEY-2026",
          "scene": {...}        // 可选，作战场景覆盖
          "algorithm_level": "medium"  // 可选
        }

    适配器自动完成:
    - targets → detections 格式转换
    - geo 嵌套坐标平铺
    - class → object_type 映射
    - 连续帧 speed/heading 推算
    - scene 场景信息提取
    """
    if not is_intelligence_format(payload):
        raise HTTPException(
            status_code=400,
            detail="Payload must contain a 'targets' array (TacticalIntelligenceAgent format)",
        )

    algorithm_level = payload.get("algorithm_level", "medium")
    scene = extract_scene_from_intelligence(payload, override_scene=payload.get("scene"))
    detections = convert_intelligence_to_detections(payload)

    perception = PerceptionResultRequest(
        task_id=payload.get("mission_id", f"intel-{payload.get('packet_id', 'task')}"),
        message_type="perception_result",
        algorithm_level=algorithm_level if algorithm_level in ("small", "medium", "large") else "medium",
        scene=scene,
        detections=detections,
    )

    async with processing_lock:
        result = _process_payload(perception)
    await _broadcast_events(result["artifact"]["events"], result["artifact"])
    return {
        "status": "completed",
        "message": "Intelligence data adapted and processed",
        "adapted_detection_count": len(detections),
        "artifact": result["artifact"],
    }


@app.post("/demo/frame")
async def demo_frame(payload: PerceptionResultRequest) -> Dict[str, Any]:
    async with processing_lock:
        result = _process_payload(payload)
    await _broadcast_events(result["artifact"]["events"], result["artifact"])
    return result


@app.post("/sendMessage")
async def send_message(task_payload: Dict[str, Any], token: str = Depends(verify_a2a_token)) -> Dict[str, Any]:
    runtime.capture_work_list(task_payload)
    work_item = runtime.work_item_from_payload(task_payload)
    workflow_id = task_payload.get("workflow_id")
    requested_skills = _requested_skills(task_payload)
    unsupported_skills = sorted(set(requested_skills) - set(SUPPORTED_SKILLS))
    if unsupported_skills:
        return build_task_error_response(
            workflow_id=workflow_id,
            work_item=work_item,
            agent=runtime.agent_name,
            role=runtime.role,
            command=task_payload.get("command"),
            error=f"unsupported skill(s): {', '.join(unsupported_skills)}",
            error_code="UNSUPPORTED_SKILL",
        )

    if not _effective_ready():
        return build_task_error_response(
            workflow_id=workflow_id,
            work_item=work_item,
            agent=runtime.agent_name,
            role=runtime.role,
            command=task_payload.get("command"),
            error="agent is not ready",
            error_code="AGENT_NOT_READY",
        )

    cached = runtime.get_task_response(work_item)
    if cached is not None:
        return cached

    async with processing_lock:
        cached = runtime.get_task_response(work_item)
        if cached is not None:
            return cached
        if not _effective_ready():
            return build_task_error_response(
                workflow_id=workflow_id,
                work_item=work_item,
                agent=runtime.agent_name,
                role=runtime.role,
                command=task_payload.get("command"),
                error="agent is not ready",
                error_code="AGENT_NOT_READY",
            )
        runtime.mark_busy(workflow_id, work_item)
        registrar.set_agent_status("busy", lease_workflow_id=workflow_id or "", lease_work_item=work_item)
        started = time.perf_counter()
        try:
            payload = _perception_from_a2a_task(task_payload)
            result = _process_payload(payload)
        except Exception as exc:
            runtime.mark_error(str(exc))
            runtime.mark_idle()
            registrar.set_agent_status("idle", last_error="TRACK_THREAT_AGENT_FAILED")
            duration_ms = round((time.perf_counter() - started) * 1000, 3)
            return build_task_error_response(
                workflow_id=workflow_id,
                work_item=work_item,
                agent=runtime.agent_name,
                role=runtime.role,
                command=task_payload.get("command"),
                error=str(exc),
                error_code="AGENT_BUSINESS_ERROR",
                metrics={"latency_ms": duration_ms, "duration_ms": duration_ms},
            )
        runtime.mark_idle()
        registrar.set_agent_status("idle", lease_workflow_id="", lease_work_item="")
    await _broadcast_events(result["artifact"]["events"], result["artifact"])
    output = {
        "task_id": payload.task_id,
        "message_type": result["message_type"],
        "artifact": result["artifact"],
        "safety_boundary": "simulation-only situation-awareness priority; no weapon control",
    }
    duration_ms = round((time.perf_counter() - started) * 1000, 3)
    response = build_task_response(
        workflow_id=workflow_id,
        work_item=work_item,
        agent=runtime.agent_name,
        role=runtime.role,
        command=task_payload.get("command"),
        status="completed",
        output=output,
        metrics={
            "latency_ms": duration_ms,
            "duration_ms": duration_ms,
            "track_count": result["artifact"]["summary"].get("track_count", 0),
            "group_count": result["artifact"]["summary"].get("group_count", 0),
            "ranking_count": len(result["artifact"].get("unified_threat_ranking", [])),
        },
        message="Track/threat situation analysis completed",
        work_list_size=len(runtime.get_work_list(workflow_id)) if workflow_id else None,
        cached=False,
        extra={
            "task_id": payload.task_id,
            "artifact_summary": result["artifact"]["summary"],
            "artifact": result["artifact"],
            "safety_boundary": "simulation-only situation-awareness priority; no weapon control",
            "token_accepted": bool(token),
            "executed_skills": requested_skills,
            "output_hint_acknowledged": task_payload.get("output_hint"),
        },
    )
    runtime.set_task_response(work_item, response)
    _save_state_snapshot()
    return response


@app.post("/sendMessageStream")
async def send_message_stream(task_payload: Dict[str, Any], token: str = Depends(verify_a2a_token)) -> StreamingResponse:
    if not _effective_ready():
        raise HTTPException(status_code=503, detail="agent is not ready")

    async def event_stream():
        runtime.capture_work_list(task_payload)
        work_item = runtime.work_item_from_payload(task_payload)
        requested_skills = _requested_skills(task_payload)
        unsupported_skills = sorted(set(requested_skills) - set(SUPPORTED_SKILLS))
        if unsupported_skills:
            yield _sse(
                {
                    "workflow_id": task_payload.get("workflow_id"),
                    "work_item": work_item,
                    "status": "Failed",
                    "progress": 100,
                    "error": {
                        "code": "UNSUPPORTED_SKILL",
                        "message": f"unsupported skill(s): {', '.join(unsupported_skills)}",
                    },
                }
            )
            return
        cached_events = runtime.get_stream_events(work_item)
        if cached_events is not None:
            for event in cached_events:
                yield event
            return

        workflow_id = task_payload.get("workflow_id")
        buffered_events: List[str] = []

        async def emit(payload: Dict[str, Any]):
            event = _sse({"workflow_id": workflow_id, "work_item": work_item, **payload})
            buffered_events.append(event)
            return event

        yield await emit({"status": "Working", "progress": 10, "message": "received perception result"})
        await asyncio.sleep(0)
        async with processing_lock:
            runtime.mark_busy(workflow_id, work_item)
            registrar.set_agent_status("busy", lease_workflow_id=workflow_id or "", lease_work_item=work_item)
            try:
                payload = _perception_from_a2a_task(task_payload)
                yield await emit({"status": "Working", "progress": 25, "message": "updating tracks and adaptive predictions"})
                await asyncio.sleep(0)
                result = _process_payload(payload)
            except Exception as exc:
                runtime.mark_error()
                runtime.mark_idle()
                registrar.set_agent_status("idle", last_error=str(exc))
                yield await emit(
                    {
                        "status": "Failed",
                        "progress": 100,
                        "error": {"code": "TRACK_THREAT_AGENT_FAILED", "message": str(exc)},
                    }
                )
                runtime.set_stream_events(work_item, buffered_events)
                return
            runtime.mark_idle()
            registrar.set_agent_status("idle", lease_workflow_id="", lease_work_item="")
        artifact = result["artifact"]
        yield await emit({"status": "Working", "progress": 45, "message": "local ST-GNN message-passing trajectory prediction completed"})
        yield await emit({"status": "Working", "progress": 65, "message": "groups and protected-asset impacts analyzed"})
        yield await emit({"status": "Working", "progress": 85, "message": "DBN threat assessment and XAI evidence generated"})
        yield await emit(
            {
                "status": "Artifact",
                "progress": 95,
                "message": "track_threat_group_artifact ready",
                "artifact_summary": artifact["summary"],
                "events": artifact["events"],
            }
        )
        await _broadcast_events(artifact["events"], artifact)
        yield await emit(
            {
                "status": "Completed",
                "progress": 100,
                "message": "Track/threat situation analysis completed",
                "artifact": artifact,
                "token_accepted": bool(token),
                "executed_skills": requested_skills,
                "output_hint_acknowledged": task_payload.get("output_hint"),
            }
        )
        runtime.set_stream_events(work_item, buffered_events)
        _save_state_snapshot()

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/workflows/{workflow_id}/work-list")
def workflow_work_list(workflow_id: str) -> Dict[str, Any]:
    return {
        "workflow_id": workflow_id,
        "agent": runtime.agent_name,
        "role": runtime.role,
        "work_list": runtime.get_work_list(workflow_id),
    }


@app.get("/demo/state")
def demo_state() -> Dict[str, Any]:
    return {"status": "ok", "artifact": last_artifact}


@app.get("/state/summary")
def state_summary() -> Dict[str, Any]:
    runtime_snapshot = runtime_status()
    return {
        "status": "ok",
        "agent": runtime.agent_name,
        "role": runtime.role,
        "runtime": runtime_snapshot,
        "tracks": {
            "active_count": len(tracker.tracks),
            "ids": sorted(tracker.tracks),
        },
        "groups": {
            "active_count": len(group_detector.groups),
            "ids": sorted(group_detector.groups),
        },
        "protected_assets": {
            "count": len(last_artifact.get("protected_assets", [])),
            "ids": [asset.get("asset_id") for asset in last_artifact.get("protected_assets", [])],
        },
        "asset_impacts": {
            "count": len(last_artifact.get("asset_impacts", [])),
            "top": (last_artifact.get("asset_impacts") or [None])[0],
        },
        "last_artifact": {
            "task_id": last_artifact.get("task_id"),
            "artifact_schema_version": last_artifact.get("artifact_schema_version"),
            "track_count": last_artifact.get("summary", {}).get("track_count", 0),
            "group_count": last_artifact.get("summary", {}).get("group_count", 0),
            "asset_impact_count": last_artifact.get("summary", {}).get("asset_impact_count", 0),
            "ranking_count": len(last_artifact.get("unified_threat_ranking", [])),
        },
        "model_status": _model_status(),
        "state_snapshot": {
            "path": str(state_store.path),
            "exists": state_store.path.exists(),
        },
        "schema": {
            "state_schema_version": STATE_SUMMARY_SCHEMA_VERSION,
            "state_store_schema_version": STATE_SCHEMA_VERSION,
            "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        },
        "safety_boundary": "simulation-only state summary; no weapon control",
    }


@app.post("/demo/reset")
async def demo_reset() -> Dict[str, Any]:
    await _stop_auto_demo()
    algorithm_provider.reset()
    runtime.reset_runtime()
    reset_adapter_cache()
    message = {
        "event_type": "demo.reset",
        "artifact": {
            "tracks": [],
            "threats": [],
            "protected_assets": [],
            "asset_impacts": [],
            "groups": [],
            "unified_threat_ranking": [],
            "events": [],
            "summary": {"track_count": 0, "group_count": 0, "protected_asset_count": 0},
        },
    }
    await manager.broadcast(message)
    global last_artifact
    last_artifact = message["artifact"]
    state_store.clear()
    return {"status": "reset", "active_track_count": 0, "active_group_count": 0}


@app.post("/demo/start")
async def demo_start() -> Dict[str, Any]:
    global auto_demo_task
    if auto_demo_task and not auto_demo_task.done():
        return {"status": "already_running"}
    tracker.reset()
    group_detector.reset()
    state_store.clear()
    auto_demo_task = asyncio.create_task(_run_auto_demo())
    return {"status": "started", "frames": 90, "interval_s": 1}


@app.post("/demo/stop")
async def demo_stop() -> Dict[str, Any]:
    await _stop_auto_demo()
    await manager.broadcast({"event_type": "demo.stopped"})
    return {"status": "stopped"}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await manager.connect(websocket)
    await websocket.send_json(
        {
            "event_type": "demo.connected",
            "active_track_count": len(tracker.tracks),
            "active_group_count": len(group_detector.groups),
        }
    )
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


@app.post("/debug/reset")
async def reset() -> Dict[str, Any]:
    reset_adapter_cache()
    return await demo_reset()


def _process_payload(payload: PerceptionResultRequest) -> Dict[str, Any]:
    global last_artifact
    processed_at = time.time()
    protected_assets = _protected_assets_from_scene(payload.scene)
    tracks = algorithm_provider.update_tracks(payload.detections, algorithm_level=payload.algorithm_level)
    threats = algorithm_provider.rank_threats(tracks, payload.scene)
    asset_impacts = algorithm_provider.analyze_asset_impacts(tracks, threats, protected_assets)
    groups = algorithm_provider.detect_groups(tracks, threats, payload.scene)
    unified_ranking = _unified_ranking(threats, groups, asset_impacts)
    decision_risk_assessments = _decision_risk_assessments(unified_ranking)
    events = build_integration_events(tracks, threats, groups, unified_ranking, protected_assets, asset_impacts)
    artifact = {
        "task_id": payload.task_id,
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "input_schema_version": INPUT_SCHEMA_VERSION,
        "trace": {
            "task_id": payload.task_id,
            "message_type": payload.message_type,
            "algorithm_level": payload.algorithm_level,
            "detection_count": len(payload.detections),
            "processed_at": processed_at,
            "agent": runtime.agent_name,
            "role": runtime.role,
        },
        "protected_assets": [asset.model_dump() for asset in protected_assets],
        "tracks": [track.model_dump() for track in tracks],
        "threats": [threat.model_dump() for threat in threats],
        "asset_impacts": [impact.model_dump() for impact in asset_impacts],
        "groups": [group.model_dump() for group in groups],
        "unified_threat_ranking": unified_ranking,
        "decision_risk_assessments": decision_risk_assessments,
        "events": events,
        "summary": {
            "protected_asset_count": len(protected_assets),
            "track_count": len(tracks),
            "threat_count": len(threats),
            "asset_impact_count": len(asset_impacts),
            "decision_risk_assessment_count": len(decision_risk_assessments),
            "group_count": len(groups),
            "highest_track_score": threats[0].score if threats else 0.0,
            "highest_group_score": max((group.group_threat_score for group in groups), default=0.0),
            "highest_asset_impact_score": asset_impacts[0].score if asset_impacts else 0.0,
            "algorithm_provider": algorithm_provider.algorithm_contract(),
            "model_status": _model_status(),
            "model_registry": model_registry.snapshot(),
            "execution": {
                "mode": "in_process_model_execution",
                "model_ownership": "track_threat_agent",
                "internal_workflow_engine": False,
                "network_algorithm_calls": False,
            },
            "prediction_eval": _prediction_eval_summary(tracks),
            "schema": {
                "input_schema_version": INPUT_SCHEMA_VERSION,
                "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
            },
            "safety_boundary": "Simulation-only situation-awareness priority; no weapon control or engagement advice.",
        },
    }
    last_artifact = artifact
    _save_state_snapshot()
    return {
        "task_id": payload.task_id,
        "message_type": "track_threat_group_artifact",
        "status": "completed",
        "artifact": artifact,
    }


def _model_status() -> Dict[str, Any]:
    learned_status = (
        learned_predictor.status()
        if learned_predictor is not None
        else {"loaded": False, "model_path": None, "model_type": None}
    )
    bundle_status = st_gnn_model_bundle.status()
    trained_status = trained_st_gnn_runtime.status()
    trained_loaded = any(
        model.get("loaded") for model in trained_status.get("models", {}).values()
    )
    return {
        "overall": (
            "model_loaded"
            if learned_status.get("loaded") or bundle_status.get("loaded") or trained_loaded
            else trained_status["overall"]
        ),
        "learned_trajectory_predictor": learned_status,
        "st_gnn_model_bundle": bundle_status,
        "st_gnn_runtime": trained_status,
        "model_registry": model_registry.snapshot(),
        "local_graph_fallback": {
            "loaded": True,
            "provider": "local_numpy_message_passing",
        },
    }


def _prediction_eval_summary(tracks: List[Any]) -> Dict[str, Any]:
    evals = [
        track.metadata.get("prediction_eval")
        for track in tracks
        if track.metadata.get("prediction_eval") and track.metadata["prediction_eval"].get("sample_count", 0) > 0
    ]
    if not evals:
        return {
            "sample_count": 0,
            "mean_ade_m": None,
            "mean_fde_m": None,
            "note": "No previous prediction was available for this frame.",
        }
    return {
        "sample_count": sum(int(item.get("sample_count", 0)) for item in evals),
        "track_count": len(evals),
        "mean_ade_m": round(sum(float(item.get("ade_m", 0.0)) for item in evals) / len(evals), 2),
        "mean_fde_m": round(sum(float(item.get("fde_m", 0.0)) for item in evals) / len(evals), 2),
    }


def _save_state_snapshot() -> None:
    state_store.save(
        tracks=tracker.tracks,
        groups=group_detector.groups,
        last_artifact=last_artifact,
        runtime_state=runtime.export_persistent_state(),
    )


def _restore_state_snapshot() -> bool:
    global last_artifact
    restored = state_store.load()
    if restored is None:
        return False
    tracker.tracks = restored.tracks
    group_detector.groups = restored.groups
    last_artifact = restored.last_artifact or last_artifact
    runtime.restore_persistent_state(restored.runtime_state)
    return True


def _perception_from_a2a_task(task_payload: Dict[str, Any]) -> PerceptionResultRequest:
    input_payload = task_payload.get("payload") or task_payload.get("input") or {}
    context = task_payload.get("context") or {}
    if "detections" not in input_payload and "perception_result" in input_payload:
        input_payload = input_payload["perception_result"]
    if "detections" not in input_payload and "artifact" in task_payload:
        input_payload = task_payload["artifact"]

    # ── 自动检测同门 TacticalIntelligenceAgent 格式 ──
    if is_intelligence_format(input_payload):
        scene = extract_scene_from_intelligence(
            input_payload, override_scene=input_payload.get("scene")
        )
        detections = convert_intelligence_to_detections(input_payload)
        return PerceptionResultRequest.model_validate(
            {
                "task_id": task_payload.get("task_id", "a2a-track-threat-task"),
                "message_type": "perception_result",
                "algorithm_level": input_payload.get("algorithm_level", task_payload.get("algorithm_level", "medium")),
                "scene": scene,
                "detections": [d.model_dump() for d in detections],
            }
        )

    return PerceptionResultRequest.model_validate(
        {
            "task_id": task_payload.get("task_id", "a2a-track-threat-task"),
            "message_type": "perception_result",
            "algorithm_level": input_payload.get(
                "algorithm_level",
                context.get("algorithm_level", task_payload.get("algorithm_level", "medium")),
            ),
            "scene": input_payload.get(
                "scene",
                context.get("scene", task_payload.get("scene", {})),
            ),
            "detections": input_payload.get("detections", []),
        }
    )


def _requested_skills(task_payload: Dict[str, Any]) -> List[str]:
    raw_many = task_payload.get("required_skills") or task_payload.get("requiredSkills")
    if isinstance(raw_many, str):
        values = [item.strip() for item in raw_many.split(",") if item.strip()]
    elif isinstance(raw_many, list):
        values = [str(item).strip() for item in raw_many if str(item).strip()]
    else:
        value = str(
            task_payload.get("required_skill")
            or task_payload.get("requiredSkill")
            or ""
        ).strip()
        values = [value] if value else ["track_threat_situation_analysis"]
    return list(dict.fromkeys(values))


def _sse(payload: Dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _broadcast_events(events: List[Dict[str, Any]], artifact: Dict[str, Any]) -> None:
    await manager.broadcast({"event_type": "artifact.updated", "artifact": artifact})
    for event in events:
        await manager.broadcast(event)


async def _run_auto_demo() -> None:
    try:
        for frame_no in range(90):
            payload = PerceptionResultRequest.model_validate(generate_auto_demo_frame(frame_no))
            result = _process_payload(payload)
            await _broadcast_events(result["artifact"]["events"], result["artifact"])
            await asyncio.sleep(1)
        await manager.broadcast({"event_type": "demo.finished", "frames": 90})
    except asyncio.CancelledError:
        await manager.broadcast({"event_type": "demo.stopped"})
        raise


async def _stop_auto_demo() -> None:
    global auto_demo_task
    if auto_demo_task and not auto_demo_task.done():
        auto_demo_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await auto_demo_task
    auto_demo_task = None


def _protected_assets_from_scene(scene: Dict[str, Any]) -> List[ProtectedAsset]:
    raw_assets = scene.get("protected_assets") or []
    assets = []
    for raw in raw_assets:
        try:
            assets.append(ProtectedAsset.model_validate(raw))
        except Exception:
            continue
    if assets:
        return assets

    if "protected_zone_lat" in scene and "protected_zone_lon" in scene:
        return [
            ProtectedAsset(
                asset_id="protected-zone-center",
                asset_name="默认保护区中心",
                asset_type="civil_infrastructure",
                lat=float(scene.get("protected_zone_lat", 0.0)),
                lon=float(scene.get("protected_zone_lon", 0.0)),
                protection_radius_m=float(scene.get("protected_radius_m", 30_000.0)),
                criticality=0.75,
                metadata={"source": "scene.protected_zone", "display_hint": "fallback protected asset"},
            )
        ]
    return []


def _unified_ranking(
    threats: List[Any],
    groups: List[Any],
    asset_impacts: List[Any] | None = None,
) -> List[Dict[str, Any]]:
    rows = []
    for threat in threats:
        reason = _ranking_reason("track", threat.level, threat.score, threat.evidence)
        rows.append(
            {
                "entity_type": "track",
                "entity_id": threat.track_id,
                "item_type": "track",
                "item_id": threat.track_id,
                "score": threat.score,
                "level": threat.level,
                "source_id": threat.threat_id,
                "reason": reason,
                "evidence": list(threat.evidence)[:5],
                "factors": dict(threat.factors),
            }
        )
    for group in groups:
        reason = _ranking_reason("group", group.group_threat_level, group.group_threat_score, group.evidence)
        rows.append(
            {
                "entity_type": "group",
                "entity_id": group.group_id,
                "item_type": "group",
                "item_id": group.group_id,
                "score": group.group_threat_score,
                "level": group.group_threat_level,
                "source_id": group.group_id,
                "reason": reason,
                "evidence": list(group.evidence)[:5],
                "factors": {
                    "cohesion_score": group.cohesion_score,
                    "member_count": float(len(group.member_track_ids)),
                    "group_threat_score": group.group_threat_score,
                },
            }
        )
    for impact in asset_impacts or []:
        reason = _ranking_reason("asset_impact", impact.level, impact.score, impact.evidence)
        rows.append(
            {
                "entity_type": "asset_impact",
                "entity_id": impact.impact_id,
                "item_type": "asset_impact",
                "item_id": impact.protected_asset_id,
                "score": impact.score,
                "level": impact.level,
                "source_id": impact.source_track_id,
                "protected_asset_name": impact.protected_asset_name,
                "source_track_id": impact.source_track_id,
                "reason": reason,
                "evidence": list(impact.evidence)[:5],
                "factors": dict(impact.factors),
                "eta_to_protected_radius_s": impact.eta_to_protected_radius_s,
                "will_enter_protection_radius": impact.will_enter_protection_radius,
                "predicted_min_distance_margin_m": impact.predicted_min_distance_margin_m,
                "predicted_closest_distance_m": impact.predicted_closest_distance_m,
            }
        )
    rows.sort(key=lambda item: item["score"], reverse=True)
    for index, row in enumerate(rows, start=1):
        row["rank"] = index
    return rows


def _ranking_reason(item_type: str, level: str, score: float, evidence: List[str]) -> str:
    label = {
        "track": "单体目标",
        "group": "疑似编组",
        "asset_impact": "保护资产影响",
    }.get(item_type, item_type)
    first_evidence = evidence[0] if evidence else "由距离、接近趋势、航迹质量和异常因子综合计算"
    return f"{label}关注等级为 {level}，综合分数 {score:.2f}；{first_evidence}"


def _decision_risk_assessments(unified_ranking: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Adapt local ranking rows to the downstream decision-agent risk schema.

    The lzh decision agents consume risk summaries rather than raw detections.
    This adapter keeps our agent focused on situation analysis while making the
    handoff explicit and stable.
    """

    assessments = []
    for row in unified_ranking:
        evidence = [str(item) for item in row.get("evidence", []) if str(item)]
        triggered_rules = _triggered_rules(row)
        assessments.append(
            {
                "target_id": str(row.get("entity_id") or row.get("item_id") or row.get("source_id")),
                "source_id": str(row.get("source_id") or row.get("entity_id") or ""),
                "source_item_type": str(row.get("item_type", "track")),
                "priority": int(row.get("rank", len(assessments) + 1)),
                "risk": str(row.get("level", "low")),
                "threat_score": round(float(row.get("score", 0.0)) * 100.0, 2),
                "probability": round(float(row.get("score", 0.0)), 4),
                "rationale": str(row.get("reason") or (evidence[0] if evidence else "由航迹预测和态势关注排序生成")),
                "triggered_rules": triggered_rules,
                "evidence": evidence[:5],
                "safety_note": "simulation-only risk summary for downstream planning agents; no engagement advice",
            }
        )
    return assessments


def _triggered_rules(row: Dict[str, Any]) -> List[str]:
    rules = [f"ranking_item:{row.get('item_type', 'unknown')}"]
    factors = row.get("factors", {}) or {}
    if float(factors.get("distance_factor", 0.0)) > 0.55:
        rules.append("asset_proximity")
    if float(factors.get("closing_factor", 0.0)) > 0.55:
        rules.append("closing_to_protected_area")
    if float(factors.get("anomaly_factor", 0.0)) > 0.0:
        rules.append("anomaly_detected")
    if row.get("item_type") == "group":
        rules.append("group_detected")
    if row.get("item_type") == "asset_impact":
        rules.append("protected_asset_impact")
        if row.get("will_enter_protection_radius"):
            rules.append("predicted_radius_entry")
    return list(dict.fromkeys(rules))
