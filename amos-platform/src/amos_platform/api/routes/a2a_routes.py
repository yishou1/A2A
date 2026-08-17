"""AMOS analysis routes backed by the configured A2A transport."""

from __future__ import annotations

from typing import Any

from flask import request

from amos_platform.agents.a2a.workflow_submission import (
    WorkflowSubmissionError,
    normalize_upstream_error,
    submit_current_workflow,
)
from amos_platform.api.dependencies import get_bridge, get_engine, get_runtime, get_scenario_support
from amos_platform.api.responses import err, ok

# Compatibility for focused route tests and downstream diagnostic imports.
_upstream_error = normalize_upstream_error


def register_a2a_routes(bp: Any) -> None:
    """Register the stable browser-facing analysis endpoints."""

    @bp.route("/api/v1/a2a/backend/health", methods=["GET"])
    def a2a_backend_health():
        """Report the configured integration boundary and upstream state."""
        return ok(get_bridge().health())

    @bp.route("/api/v1/a2a/algorithms", methods=["GET"])
    def a2a_runtime_algorithms():
        """Return active algorithms verified against their runtime health endpoints."""
        return ok(get_bridge().algorithm_catalog())

    @bp.route("/api/v1/a2a/workflows/submit", methods=["POST"])
    def a2a_workflow_submit():
        """Submit the current causal snapshot to the configured A2A backend.

        Request body can override bounded workflow parameters such as workflow,
        max_steps and max_workers. The workflow file is server-controlled.

        ``sim_context: true`` freezes the current AMOS input for audit. Gateway
        mode sends the run/chain reference; diagnostic direct mode sends the
        compatible mission attachment structure.
        """
        data = request.get_json(silent=True) or {}
        try:
            result = submit_current_workflow(
                data,
                engine=get_engine(),
                bridge=get_bridge(),
                scenario_support=get_scenario_support(),
                run_manifest_store=get_runtime().get_run_manifest_store(),
            )
        except WorkflowSubmissionError as exc:
            return err(
                exc.status_code,
                exc.message,
                exc.details,
            ), exc.status_code
        return ok(result)

    @bp.route("/api/v1/a2a/workflows/<workflow_id>/view", methods=["GET"])
    def a2a_workflow_view(workflow_id: str):
        """Return the stable AMOS workflow-display contract."""
        return ok(get_runtime().get_workflow_view(workflow_id))

    @bp.route("/api/v1/a2a/workflows/<workflow_id>/resume", methods=["POST"])
    def a2a_workflow_resume(workflow_id: str):
        """Resume a paused/failed workflow."""
        data = request.get_json(silent=True) or {}
        bridge = get_bridge()
        result = bridge.resume_workflow(workflow_id, **data)
        if result.get("error"):
            status, message = normalize_upstream_error(result, "分析任务恢复失败")
            return err(status, message, {"backend": result}), status
        return ok(result)
