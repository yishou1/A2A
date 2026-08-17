"""Demonstration director API routes."""

from __future__ import annotations

from typing import Any

from flask import request

from amos_platform.api.dependencies import get_director
from amos_platform.api.responses import err, ok
from amos_platform.simulation.director import DirectorError


def register_director_routes(bp: Any) -> None:
    """Register configuration, state and action routes for the director."""

    @bp.route("/api/v1/director/configure", methods=["POST"])
    def director_configure():
        data = request.get_json(silent=True) or {}
        scenario_id = str(data.get("scenario_id") or "")
        if not scenario_id:
            return err(400, "scenario_id is required"), 400
        try:
            state = get_director().configure(
                scenario_id=scenario_id,
                mode=str(data.get("mode") or "integration"),
                branch=str(data["branch"]) if data.get("branch") else None,
                seed=data.get("seed"),
            )
        except DirectorError as exc:
            status = 404 if str(exc).startswith("scenario not found") else 400
            return err(status, str(exc)), status
        return ok(state)

    @bp.route("/api/v1/director/state", methods=["GET"])
    def director_state():
        return ok(get_director().state())

    @bp.route("/api/v1/director/action", methods=["POST"])
    def director_action():
        data = request.get_json(silent=True) or {}
        action = str(data.get("action") or "")
        if not action:
            return err(400, "action is required"), 400
        try:
            state = get_director().action(action, step_sec=data.get("step_sec", 1.0))
        except DirectorError as exc:
            return err(409 if "conditions not satisfied" in str(exc) else 400, str(exc)), (
                409 if "conditions not satisfied" in str(exc) else 400
            )
        return ok(state)


__all__ = ["register_director_routes"]
