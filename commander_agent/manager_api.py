from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

from commander_agent.workflow_manager import CommanderWorkflowManager
from monitoring import SUPERVISOR_HTML, SupervisorMonitor


class WorkflowSubmitRequest(BaseModel):
    workflow: Literal["bpel", "dynamic"] = "bpel"
    workflow_file: Optional[str] = None
    workflow_id: Optional[str] = None
    resume: bool = False
    max_steps: int = Field(default=10, ge=1)
    max_workers: Optional[int] = Field(default=None, ge=1)
    max_activity_workers: Optional[int] = Field(default=None, ge=1)
    max_agent_workers: Optional[int] = Field(default=None, ge=1)
    max_retries: int = Field(default=1, ge=0)
    retry_backoff: float = Field(default=0.2, ge=0)
    request_timeout: float = Field(default=5.0, gt=0)
    mock_eval_score: Optional[int] = None
    mock_decision: Optional[Literal["ASSAULT", "RE-PLAN"]] = None
    initial_context: Dict[str, Any] = Field(default_factory=dict)
    attachments: list[Dict[str, Any]] = Field(default_factory=list)


def _request_payload(request: BaseModel) -> dict:
    if hasattr(request, "model_dump"):
        return request.model_dump()
    return request.dict()


def build_workflow_manager_app(
    *,
    mode: str = "remote",
    state_dir: Optional[str] = None,
    max_workflows: int = 4,
    manager: Optional[CommanderWorkflowManager] = None,
) -> FastAPI:
    app = FastAPI(title="A2A Commander Workflow Manager")
    app.state.workflow_manager = manager or CommanderWorkflowManager(
        mode=mode,
        state_dir=state_dir,
        max_workflows=max_workflows,
    )
    app.state.supervisor_monitor = SupervisorMonitor()

    @app.on_event("shutdown")
    async def shutdown_manager():
        app.state.workflow_manager.shutdown()

    @app.get("/health")
    async def health():
        workflow_manager = app.state.workflow_manager
        return {
            "status": "ok",
            "mode": workflow_manager.mode,
            "max_workflows": workflow_manager.max_workflows,
            "workflow_count": len(workflow_manager.list_workflows()),
            "active_leases": len(workflow_manager.list_agent_leases()),
            "agent_count": len(workflow_manager.list_agents()),
        }

    @app.get("/supervisor", response_class=HTMLResponse)
    async def supervisor_dashboard():
        return SUPERVISOR_HTML

    @app.get("/supervisor/snapshot")
    async def supervisor_snapshot():
        return app.state.supervisor_monitor.snapshot(app.state.workflow_manager)

    @app.get("/alerts")
    async def active_alerts():
        return app.state.supervisor_monitor.snapshot(app.state.workflow_manager)["alerts"]

    @app.get("/metrics")
    async def prometheus_metrics():
        payload = app.state.supervisor_monitor.prometheus(app.state.workflow_manager)
        return Response(payload, media_type="text/plain; version=0.0.4; charset=utf-8")

    @app.get("/workflows")
    async def list_workflows():
        return app.state.workflow_manager.list_workflows()

    @app.post("/workflows", status_code=202)
    async def submit_workflow(request: WorkflowSubmitRequest):
        try:
            return app.state.workflow_manager.submit_workflow(**_request_payload(request))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/workflows/{workflow_id}")
    async def get_workflow(workflow_id: str, checkpoint: bool = False):
        try:
            return app.state.workflow_manager.get_workflow(
                workflow_id,
                include_checkpoint=checkpoint,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/workflows/{workflow_id}/resume", status_code=202)
    async def resume_workflow(workflow_id: str, request: WorkflowSubmitRequest):
        payload = _request_payload(request)
        payload.pop("workflow_id", None)
        payload.pop("resume", None)
        try:
            return app.state.workflow_manager.resume_workflow(workflow_id, **payload)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/leases")
    async def list_agent_leases():
        return app.state.workflow_manager.list_agent_leases()

    @app.get("/agents")
    async def list_agents():
        return app.state.workflow_manager.list_agents()

    @app.get("/workflows/{workflow_id}/checkpoint")
    async def get_checkpoint(workflow_id: str):
        try:
            return app.state.workflow_manager.get_checkpoint(workflow_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/workflows/{workflow_id}/work-list")
    async def get_work_list(workflow_id: str):
        try:
            return {
                "workflow_id": workflow_id,
                "work_list": app.state.workflow_manager.get_work_list(workflow_id),
            }
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/workflows/{workflow_id}/trace")
    async def get_trace(workflow_id: str):
        try:
            return {
                "workflow_id": workflow_id,
                "trace": app.state.workflow_manager.get_trace(workflow_id),
            }
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return app
