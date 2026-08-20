"""API blueprint construction for the AMOS simulation platform."""

from __future__ import annotations

from flask import Blueprint

from amos_platform.api.routes.registry import register_all_routes


def create_api_blueprint(name: str = "amos_api") -> Blueprint:
    """Create a Flask blueprint with all package API routes registered."""
    bp = Blueprint(name, __name__)
    register_all_routes(bp)
    return bp


__all__ = ["create_api_blueprint"]
