"""Shared algorithm-library bridge for zh agents.

Supports two transports (env ``ALGOLIB_TRANSPORT``):

- ``gateway``: lzh-style ``GET /algorithms`` + ``POST /run`` against ``ALGOLIB_BASE_URL``
- ``direct``: POST to each package ``/predict`` endpoint (default ``:901x``)

Backend selection (default ``local``):

- ``A2A_ALGORITHM_BACKEND=local|algolib``
- per-agent overrides: ``EXECUTION_CONTROL_BACKEND``, ``CLOSED_LOOP_BACKEND``

When algolib fails and ``ALGOLIB_FALLBACK_LOCAL`` is true (default), callers
should fall back to in-process algorithms and surface warnings.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Optional


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


DEFAULT_DIRECT_ENDPOINTS = {
    "execution_control_planner": "http://127.0.0.1:9012/predict",
    "mission_feature_adapter": "http://127.0.0.1:9013/predict",
    "mission_completion_scorer": "http://127.0.0.1:9014/predict",
    "closed_loop_decision_advisor": "http://127.0.0.1:9015/predict",
    "xbd_damage_assessor": "http://127.0.0.1:9016/predict",
}


@dataclass(frozen=True)
class AlgolibSettings:
    backend: str
    transport: str
    base_url: str
    timeout_seconds: float
    fallback_local: bool
    default_version: str
    default_backend_type: str

    @classmethod
    def load(cls, *, agent_backend_env: Optional[str] = None) -> "AlgolibSettings":
        # Priority: per-agent env > global A2A_ALGORITHM_BACKEND > local
        backend = ""
        if agent_backend_env:
            backend = os.environ.get(agent_backend_env, "").strip().lower()
        if not backend:
            backend = os.environ.get("A2A_ALGORITHM_BACKEND", "local").strip().lower() or "local"
        transport = os.environ.get("ALGOLIB_TRANSPORT", "direct").strip().lower() or "direct"
        if transport not in {"gateway", "direct"}:
            transport = "direct"
        if backend not in {"local", "algolib"}:
            backend = "local"
        return cls(
            backend=backend,
            transport=transport,
            base_url=os.environ.get("ALGOLIB_BASE_URL", "http://127.0.0.1:8088").rstrip("/"),
            timeout_seconds=float(os.environ.get("ALGOLIB_TIMEOUT_SECONDS", "15")),
            fallback_local=_env_bool("ALGOLIB_FALLBACK_LOCAL", True),
            default_version=os.environ.get("ALGOLIB_DEFAULT_VERSION", "1.0.0"),
            default_backend_type=os.environ.get("ALGOLIB_BACKEND_TYPE", "python_http_service"),
        )


def use_algolib_backend(*, agent_backend_env: Optional[str] = None) -> bool:
    return AlgolibSettings.load(agent_backend_env=agent_backend_env).backend == "algolib"


def resolve_direct_endpoint(algorithm_id: str) -> str:
    env_key = f"ALGOLIB_ENDPOINT_{algorithm_id.upper()}"
    override = os.environ.get(env_key, "").strip()
    if override:
        return override
    return DEFAULT_DIRECT_ENDPOINTS.get(algorithm_id, f"http://127.0.0.1:9010/predict")


def direct_endpoint_map() -> dict[str, str]:
    return {algorithm_id: resolve_direct_endpoint(algorithm_id) for algorithm_id in DEFAULT_DIRECT_ENDPOINTS}
