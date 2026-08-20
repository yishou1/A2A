"""Central route registration for the package API blueprint."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from amos_platform.api.routes.a2a_routes import register_a2a_routes
from amos_platform.api.routes.director_routes import register_director_routes
from amos_platform.api.routes.evidence_routes import register_evidence_routes
from amos_platform.api.routes.health_routes import register_health_routes
from amos_platform.api.routes.run_routes import register_run_routes
from amos_platform.api.routes.scenario_routes import register_scenario_routes
from amos_platform.api.routes.sim_routes import register_sim_routes
from amos_platform.api.routes.support_routes import register_support_routes

RouteRegistrar = Callable[[Any], None]

ROUTE_REGISTRARS: tuple[tuple[str, RouteRegistrar], ...] = (
    ("sim", register_sim_routes),
    ("director", register_director_routes),
    ("evidence", register_evidence_routes),
    ("a2a", register_a2a_routes),
    ("runs", register_run_routes),
    ("health", register_health_routes),
    ("scenario", register_scenario_routes),
    ("support", register_support_routes),
)


def register_all_routes(bp: Any) -> None:
    """Register all package routes on the provided Flask blueprint."""
    for _, register_routes in ROUTE_REGISTRARS:
        register_routes(bp)


__all__ = ["ROUTE_REGISTRARS", "register_all_routes"]
