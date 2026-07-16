from __future__ import annotations

from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from integrated_system.mission_library import (
    get_default_demo_mission,
    get_demo_mission,
    list_demo_missions,
)
from integrated_system.orchestrator import IntegratedDemoOrchestrator
from integrated_system.demo_dashboard import build_demo_dashboard_html
from integrated_system.schemas import (
    IntegratedMissionRequest,
    MissionAdjustmentRequest,
    MissionControlRequest,
)

def build_integrated_demo_app(
    *,
    state_dir: Optional[str] = None,
    max_workflows: int = 4,
    orchestrator: Optional[IntegratedDemoOrchestrator] = None,
) -> FastAPI:
    app = FastAPI(title="Integrated Demo System")
    app.state.integrated_orchestrator = orchestrator or IntegratedDemoOrchestrator(
        state_dir=state_dir,
        max_workflows=max_workflows,
    )

    @app.on_event("shutdown")
    async def shutdown_orchestrator():
        app.state.integrated_orchestrator.shutdown()

    @app.get("/health")
    async def health():
        orchestrator_instance = app.state.integrated_orchestrator
        return {
            "status": "ok",
            "system": "integrated_demo",
            "max_workflows": orchestrator_instance.max_workflows,
            "mission_count": len(orchestrator_instance.list_missions()),
            "mode": "branch_algorithms_with_simulated_execution",
        }

    @app.get("/demo", response_class=HTMLResponse)
    async def demo_dashboard():
        return HTMLResponse(
            build_demo_dashboard_html(),
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )

    @app.get("/demo/sample-mission")
    async def sample_mission():
        return get_default_demo_mission()

    @app.get("/demo/mission-library")
    async def mission_library():
        return list_demo_missions()

    @app.get("/demo/mission-library/{template_id}")
    async def mission_library_item(template_id: str):
        try:
            return get_demo_mission(template_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/missions")
    async def list_missions():
        return app.state.integrated_orchestrator.list_missions()

    @app.post("/missions", status_code=202)
    async def submit_mission(request: IntegratedMissionRequest):
        payload = request.model_dump() if hasattr(request, "model_dump") else request.dict()
        return app.state.integrated_orchestrator.submit_mission(payload)

    @app.get("/missions/{workflow_id}")
    async def get_mission(workflow_id: str, include_blackboard: bool = True):
        try:
            return app.state.integrated_orchestrator.get_mission(
                workflow_id,
                include_blackboard=include_blackboard,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/missions/{workflow_id}/report")
    async def get_mission_report(workflow_id: str):
        try:
            return app.state.integrated_orchestrator.get_mission_report(workflow_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/missions/{workflow_id}/control")
    async def control_mission(workflow_id: str, request: MissionControlRequest):
        try:
            if request.action == "pause":
                return app.state.integrated_orchestrator.pause_mission(workflow_id)
            if request.action == "resume":
                return app.state.integrated_orchestrator.resume_mission(workflow_id)
            return app.state.integrated_orchestrator.abort_mission(workflow_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/missions/{workflow_id}/adjust")
    async def adjust_mission(workflow_id: str, request: MissionAdjustmentRequest):
        payload = request.model_dump(exclude_none=True) if hasattr(request, "model_dump") else request.dict(exclude_none=True)
        try:
            return app.state.integrated_orchestrator.adjust_mission(workflow_id, payload)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return app
