"""Health and runtime status routes."""

from __future__ import annotations

from typing import Any

import os

from amos_platform.api.dependencies import get_bridge, get_engine, get_runtime
from amos_platform.agents.a2a.workflow_run_store import get_workflow_run_store
from amos_platform.api.responses import ok
from amos_platform.simulation.exchange_contract import chain_id_for


def register_health_routes(bp: Any) -> None:
    """Register lightweight platform health/status routes."""

    @bp.route("/api/v1/status", methods=["GET"])
    def platform_status():
        engine = get_engine()
        bridge = get_bridge()
        operator_state = engine.get_operator_state()
        scenario_id = str(engine.clock.get("scenario_id") or "")
        try:
            with open("/proc/self/statm", "r", encoding="ascii") as handle:
                process_rss_bytes = int(handle.read().split()[1]) * int(
                    os.sysconf("SC_PAGE_SIZE")
                )
        except (OSError, ValueError, IndexError):
            process_rss_bytes = None
        return ok({
            "platform": "AMOS Simulation Platform",
            "version": "3.0.0",
            "agent_mode": bridge.mode,
            "analysis_transport": bridge.mode,
            "analysis_url": bridge.backend_url,
            "sim_running": engine.clock.get("running", False),
            "sim_elapsed_sec": round(engine.clock.get("elapsed_sec", 0), 1),
            "sim_asset_count": len(engine.assets),
            "sim_track_count": len(operator_state.get("fused_tracks") or []),
            "run_id": engine.clock.get("run_id"),
            "scenario_id": scenario_id or None,
            "chain_id": chain_id_for(scenario_id) if scenario_id else None,
            "simulation_contract": "amos.simulation.snapshot.v1",
            "memory": {
                "process_rss_bytes": process_rss_bytes,
                "workflow_run_cache": get_workflow_run_store().stats(),
                "run_manifest_store": get_runtime().get_run_manifest_store().stats(),
            },
        })


__all__ = ["register_health_routes"]
