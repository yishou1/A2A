"""Dependency accessors for API route modules."""

from __future__ import annotations

from typing import Any


def get_engine() -> Any:
    """Return the shared simulation engine instance."""
    return get_runtime().get_engine()

def get_bridge() -> Any:
    """Return the shared A2A analysis adapter."""
    return get_runtime().get_bridge()


def get_director() -> Any:
    """Return the shared server-side demonstration director."""
    return get_runtime().get_director()


def get_scenario_support() -> dict[str, Any]:
    """Return the small display catalog for the active scenario."""
    return get_runtime().get_scenario_support()


def get_runtime() -> Any:
    """Return the shared PlatformRuntime instance."""
    from amos_platform.runtime.platform_runtime import get_platform_runtime

    return get_platform_runtime()
__all__ = ["get_bridge", "get_director", "get_engine", "get_runtime", "get_scenario_support"]
