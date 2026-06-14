from __future__ import annotations

import os
from typing import Any, Dict, Literal, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from commander_agent.main import CommanderAgent, PROJECT_ROOT
from workflow_payloads import normalize_attachments
from workflow_state_store import WorkflowStateStore


def _default_state_dir() -> str:
    return os.environ.get("A2A_STATE_DIR", os.path.join(PROJECT_ROOT, ".a2a_state", "workflows"))


class RecoveryRequest(BaseModel):
    mode: Literal["local", "remote"] = "local"
    workflow: Literal["bpel", "dynamic"] = "bpel"
    workflow_file: Optional[str] = None
    state_dir: Optional[str] = None
    max_steps: int = Field(default=10, ge=1)
    max_workers: int = Field(default=4, ge=1)
    resume: bool = True
    strict: bool = True
    mock_eval_score: Optional[int] = None
    mock_decision: Optional[Literal["ASSAULT", "RE-PLAN"]] = None
    initial_context: Dict[str, Any] = Field(default_factory=dict)
    attachments: list[Dict[str, Any]] = Field(default_factory=list)


def get_state_store(state_dir: Optional[str] = None) -> WorkflowStateStore:
    return WorkflowStateStore(state_dir or _default_state_dir())


def load_workflow_state(workflow_id: str, state_dir: Optional[str] = None) -> Dict[str, Any]:
    store = get_state_store(state_dir)
    if not store.exists(workflow_id):
        raise FileNotFoundError(f"Workflow checkpoint not found: {workflow_id}")
    return store.load(workflow_id)


def takeover_workflow(workflow_id: str, request: RecoveryRequest) -> Dict[str, Any]:
    store = get_state_store(request.state_dir)
    if request.strict and not store.exists(workflow_id):
        raise FileNotFoundError(f"Workflow checkpoint not found: {workflow_id}")

    commander = CommanderAgent(
        mode=request.mode,
        workflow=request.workflow,
        workflow_file=request.workflow_file,
        workflow_id=workflow_id,
        state_dir=request.state_dir,
        resume=request.resume,
        mock_eval_score=request.mock_eval_score,
        mock_decision=request.mock_decision,
        max_workers=request.max_workers,
        initial_context=request.initial_context,
    )

    attachments = normalize_attachments(request.attachments)
    if attachments:
        commander.merge_external_attachments(attachments)

    if request.workflow == "bpel":
        result_context = commander.run_bpel_workflow()
    else:
        result_context = commander.run_dynamic_battle_scenario(max_steps=request.max_steps)
    return {
        "workflow_id": workflow_id,
        "workflow": request.workflow,
        "mode": request.mode,
        "state_path": str(commander.state_store.state_path(workflow_id)),
        "context": result_context,
        "workflow_status": result_context.get("workflow_status"),
    }


def build_recovery_app(
    *,
    default_mode: str = "local",
    default_workflow: str = "bpel",
    default_state_dir: Optional[str] = None,
) -> FastAPI:
    app = FastAPI(title="A2A Commander Recovery API")
    app.state.default_mode = default_mode
    app.state.default_workflow = default_workflow
    app.state.default_state_dir = default_state_dir or _default_state_dir()

    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "default_mode": app.state.default_mode,
            "default_workflow": app.state.default_workflow,
            "default_state_dir": app.state.default_state_dir,
        }

    @app.get("/workflows/{workflow_id}")
    async def get_workflow_state(workflow_id: str):
        try:
            state = load_workflow_state(workflow_id, app.state.default_state_dir)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return state

    @app.post("/workflows/{workflow_id}/resume")
    @app.post("/workflows/{workflow_id}/takeover")
    async def resume_workflow(workflow_id: str, request: RecoveryRequest):
        try:
            return takeover_workflow(workflow_id, request)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return app
