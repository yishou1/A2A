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
from .models import Detection, ProtectedAsset
from .nacos_register import NacosRegistrar
from .scenario_generator import generate_auto_demo_frame
from .st_gnn_predictor import STGNNTrajectoryPredictor
from .state_store import FileStateStore
from .threat_ranker import ThreatRanker
from .tracker import MultiTargetTracker


BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = BACKEND_DIR.parent
SAMPLE_DATA_DIR = BACKEND_DIR / "sample_data"
DEFAULT_STATE_PATH = PROJECT_DIR / ".a2a_state" / "track_threat_agent_state.json"


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
algorithm_provider = PlanAlgorithmProvider(tracker, graph_predictor, ranker, impact_analyzer, group_detector)
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
        "ready": runtime_snapshot["ready"],
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
        "ready": runtime_snapshot["ready"],
        "agent": runtime.agent_name,
        "role": runtime.role,
        "agent_status": runtime_snapshot["agent_status"],
        "active_tasks": runtime_snapshot["active_task_count"],
        "current_workflow_id": runtime_snapshot["current_workflow_id"],
        "current_work_item": runtime_snapshot["current_work_item"],
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
            "active_track_count": len(tracker.tracks),
            "active_group_count": len(group_detector.groups),
            "algorithm_provider": algorithm_provider.mode,
            "state_snapshot_exists": state_store.path.exists(),
        }
    )
    return snapshot


def runtime_status() -> Dict[str, Any]:
    return runtime.snapshot(algorithm_provider=algorithm_provider.mode)


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
        "capabilities": [
            "trajectory_tracking",
            "trajectory_prediction",
            "st_gnn_dynamic_entity_tracking",
            "threat_ranking",
            "dynamic_bayesian_network_threat_assessment",
            "kg_transformer_semantic_sitrep",
            "group_detection",
            "group_threat_ranking",
            "protected_asset_impact_analysis",
            "xai_evidence_generation",
        ],
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
        "skills": [
            {
                "id": "trajectory-tracking",
                "name": "Trajectory Tracking",
                "description": "Maintain simulated multi-target tracks and short-term predictions.",
                "tags": ["tracking", "trajectory", "simulation"],
                "inputModes": ["application/json"],
                "outputModes": ["application/json"],
            },
            {
                "id": "group-detection",
                "name": "Group Detection",
                "description": "Detect likely formations/groups from spatial, heading, and speed similarity.",
                "tags": ["group", "formation", "asset"],
                "inputModes": ["application/json"],
                "outputModes": ["application/json"],
            },
            {
                "id": "risk-priority-ranking",
                "name": "Risk Priority Ranking",
                "description": "Rank tracks and groups by simulation-only attention priority.",
                "tags": ["ranking", "risk", "threat"],
                "inputModes": ["application/json"],
                "outputModes": ["application/json"],
            },
            {
                "id": "protected-asset-impact-analysis",
                "name": "Protected Asset Impact Analysis",
                "description": "Estimate simulation-only attention priority for protected assets affected by tracked objects.",
                "tags": ["asset", "impact", "simulation"],
                "inputModes": ["application/json"],
                "outputModes": ["application/json"],
            },
        ],
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
        "metricsEndpoint": "/metrics",
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

    if not runtime.ready:
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
        if not runtime.ready:
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
        },
    )
    runtime.set_task_response(work_item, response)
    _save_state_snapshot()
    return response


@app.post("/sendMessageStream")
async def send_message_stream(task_payload: Dict[str, Any], token: str = Depends(verify_a2a_token)) -> StreamingResponse:
    if not runtime.ready:
        raise HTTPException(status_code=503, detail="agent is not ready")

    async def event_stream():
        runtime.capture_work_list(task_payload)
        work_item = runtime.work_item_from_payload(task_payload)
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
    protected_assets = _protected_assets_from_scene(payload.scene)
    tracks = algorithm_provider.update_tracks(payload.detections, algorithm_level=payload.algorithm_level)
    threats = algorithm_provider.rank_threats(tracks, payload.scene)
    asset_impacts = algorithm_provider.analyze_asset_impacts(tracks, threats, protected_assets)
    groups = algorithm_provider.detect_groups(tracks, threats, payload.scene)
    unified_ranking = _unified_ranking(threats, groups, asset_impacts)
    events = build_integration_events(tracks, threats, groups, unified_ranking, protected_assets, asset_impacts)
    artifact = {
        "protected_assets": [asset.model_dump() for asset in protected_assets],
        "tracks": [track.model_dump() for track in tracks],
        "threats": [threat.model_dump() for threat in threats],
        "asset_impacts": [impact.model_dump() for impact in asset_impacts],
        "groups": [group.model_dump() for group in groups],
        "unified_threat_ranking": unified_ranking,
        "events": events,
        "summary": {
            "protected_asset_count": len(protected_assets),
            "track_count": len(tracks),
            "threat_count": len(threats),
            "asset_impact_count": len(asset_impacts),
            "group_count": len(groups),
            "highest_track_score": threats[0].score if threats else 0.0,
            "highest_group_score": max((group.group_threat_score for group in groups), default=0.0),
            "highest_asset_impact_score": asset_impacts[0].score if asset_impacts else 0.0,
            "algorithm_provider": algorithm_provider.algorithm_contract(),
            "prediction_eval": _prediction_eval_summary(tracks),
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
            "algorithm_level": input_payload.get("algorithm_level", task_payload.get("algorithm_level", "medium")),
            "scene": input_payload.get("scene", task_payload.get("scene", {})),
            "detections": input_payload.get("detections", []),
        }
    )


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
        rows.append(
            {
                "entity_type": "track",
                "entity_id": threat.track_id,
                "item_type": "track",
                "item_id": threat.track_id,
                "score": threat.score,
                "level": threat.level,
                "source_id": threat.threat_id,
            }
        )
    for group in groups:
        rows.append(
            {
                "entity_type": "group",
                "entity_id": group.group_id,
                "item_type": "group",
                "item_id": group.group_id,
                "score": group.group_threat_score,
                "level": group.group_threat_level,
                "source_id": group.group_id,
            }
        )
    for impact in asset_impacts or []:
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
            }
        )
    rows.sort(key=lambda item: item["score"], reverse=True)
    for index, row in enumerate(rows, start=1):
        row["rank"] = index
    return rows
