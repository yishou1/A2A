"""Display-support data for the active scenario."""

from __future__ import annotations

from typing import Any

from amos_platform.api.dependencies import get_scenario_support
from amos_platform.api.responses import ok


def register_support_routes(bp: Any) -> None:
    @bp.route("/api/v1/scenario-support", methods=["GET"])
    def scenario_support():
        support = get_scenario_support()
        return ok({
            "sensor_models": support.get("sensor_models", {}),
        })


__all__ = ["register_support_routes"]
