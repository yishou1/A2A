"""Basic simulation lifecycle and state routes."""

from __future__ import annotations

from typing import Any

from flask import Response, request, stream_with_context

from amos_platform.api.dependencies import get_engine, get_runtime
from amos_platform.api.responses import err, ok
from amos_platform.data.scenario_repository import get_scenario
from amos_platform.realtime.sse import operator_state_event_stream

DEFAULT_SCENARIO_ID = "amphibious-landing-joint-operation"


def _seed_from_request(data: dict[str, Any], scenario: dict[str, Any]) -> int:
    raw = data.get("seed", scenario.get("default_seed", 0))
    if isinstance(raw, bool) or (isinstance(raw, float) and not raw.is_integer()):
        raise ValueError("seed must be an integer")
    value = int(raw)
    if value < 0 or value > 2**32 - 1:
        raise ValueError("seed must be an integer from 0 to 4294967295")
    return value


def register_sim_routes(bp: Any) -> None:
    """Register basic simulation lifecycle, stream, and state routes."""

    @bp.route("/api/v1/sim/start", methods=["POST"])
    def sim_start():
        """Load a scenario and start the simulation engine."""
        data = request.get_json(silent=True) or {}
        scenario_id = data.get("scenario_id", DEFAULT_SCENARIO_ID)

        scenario = get_scenario(scenario_id)
        if not scenario:
            return err(404, f"scenario not found: {scenario_id}"), 404
        try:
            seed = _seed_from_request(data, scenario)
        except (TypeError, ValueError) as exc:
            return err(400, str(exc)), 400

        runtime = get_runtime()
        engine = get_engine()
        engine.stop()
        run_context = runtime.begin_run(scenario_id, seed=seed)
        engine.load_scenario(scenario, seed=seed)
        engine.clock["scenario_id"] = scenario_id
        engine.clock["run_id"] = run_context.run_id
        engine.evaluate_media_captures()
        engine.start()

        return ok({
            "status": "started",
            "scenario_id": scenario_id,
            "run_id": run_context.run_id,
            "seed": seed,
            "mode": run_context.platform_mode.value,
            "scenario_name": scenario.get("name", ""),
            "asset_count": len(engine.assets),
            "track_count": 0,
        })

    @bp.route("/api/v1/sim/stop", methods=["POST"])
    def sim_stop():
        """Stop the simulation engine."""
        engine = get_engine()
        engine.stop()
        return ok({"status": "stopped"})

    @bp.route("/api/v1/sim/reset", methods=["POST"])
    def sim_reset():
        """Reset the selected scenario to T+0 without starting its clock."""
        data = request.get_json(silent=True) or {}
        scenario_id = data.get("scenario_id", DEFAULT_SCENARIO_ID)
        scenario = get_scenario(scenario_id)
        if not scenario:
            return err(404, f"scenario not found: {scenario_id}"), 404
        try:
            seed = _seed_from_request(data, scenario)
        except (TypeError, ValueError) as exc:
            return err(400, str(exc)), 400

        runtime = get_runtime()
        engine = get_engine()
        engine.stop()
        run_context = runtime.begin_run(scenario_id, seed=seed)
        engine.load_scenario(scenario, seed=seed)
        engine.clock["scenario_id"] = scenario_id
        engine.clock["run_id"] = run_context.run_id
        engine.evaluate_media_captures()
        return ok({
            "status": "reset",
            "scenario_id": scenario_id,
            "run_id": run_context.run_id,
            "seed": seed,
            "state": engine.get_operator_state(),
        })

    @bp.route("/api/v1/sim/pause", methods=["POST"])
    def sim_pause():
        """Pause the simulation."""
        get_engine().pause()
        return ok({"status": "paused"})

    @bp.route("/api/v1/sim/resume", methods=["POST"])
    def sim_resume():
        """Resume the simulation."""
        get_engine().resume()
        return ok({"status": "resumed"})

    @bp.route("/api/v1/sim/speed", methods=["POST"])
    def sim_speed():
        """Set simulation speed multiplier."""
        data = request.get_json(silent=True) or {}
        multiplier = float(data.get("speed", 1.0))
        engine = get_engine()
        engine.set_speed(multiplier)
        return ok({
            "speed": engine.clock["speed"],
            "running": bool(engine.clock.get("running")),
            "elapsed_sec": round(float(engine.clock.get("elapsed_sec", 0)), 3),
            "tick_thread_alive": bool(engine._thread and engine._thread.is_alive()),
            "last_tick_error": engine.clock.get("last_tick_error"),
        })

    @bp.route("/api/v1/sim/commands", methods=["POST"])
    def sim_command():
        """Apply an explicitly authorized operator command to the simulation."""
        data = request.get_json(silent=True) or {}
        command_type = str(data.get("command_type") or "")
        params = data.get("params") if isinstance(data.get("params"), dict) else {}
        authorization = (
            data.get("authorization")
            if isinstance(data.get("authorization"), dict)
            else {}
        )
        if command_type == "launch_follow_uav":
            required = ("track_id", "asset_id")
            missing = [name for name in required if not params.get(name)]
            if missing:
                return err(400, f"missing command parameters: {', '.join(missing)}"), 400
            result = get_engine().authorize_follow_asset(
                str(params["asset_id"]),
                str(params["track_id"]),
                authorized=authorization.get("approved") is True,
            )
            if result.get("error"):
                return err(409, str(result["error"])), 409
            return ok({
                "command_type": "launch_follow_uav",
                "status": "executed",
                "result": result,
            })
        if command_type != "fire":
            return err(400, "unsupported simulation command"), 400
        required = ("track_id", "asset_id", "weapon_name")
        missing = [name for name in required if not params.get(name)]
        if missing:
            return err(400, f"missing command parameters: {', '.join(missing)}"), 400
        result = get_engine().fire_weapon_at_track(
            str(params["track_id"]),
            asset_id=str(params["asset_id"]),
            weapon_name=str(params["weapon_name"]),
            authorized=authorization.get("approved") is True,
        )
        if result.get("error"):
            return err(409, str(result["error"])), 409
        return ok({"command_type": "fire", "status": "executed", "result": result})

    @bp.route("/api/v1/sim/stream", methods=["GET"])
    def sim_stream():
        """SSE endpoint pushing sim_state events at ~2 Hz while sim is running."""
        return Response(
            stream_with_context(operator_state_event_stream(get_engine)),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @bp.route("/api/v1/sim/state", methods=["GET"])
    def sim_state():
        """Return current simulation state as a single JSON snapshot."""
        return ok(get_engine().get_operator_state())

    @bp.route("/api/v1/sim/snapshot", methods=["GET"])
    def sim_snapshot():
        """Return the versioned, causal exchange snapshot."""
        engine = get_engine()
        return ok(engine.exchange.build_snapshot(engine.get_agent_visible_state()))

    @bp.route("/api/v1/sim/events", methods=["GET"])
    def sim_events():
        """Return continuous simulation exchange events after a cursor."""
        try:
            after_sequence = int(request.args.get("after_sequence", 0))
            if after_sequence < 0:
                raise ValueError
        except (TypeError, ValueError):
            return err(400, "after_sequence must be a non-negative integer"), 400
        engine = get_engine()
        # Synchronize before reading so a direct events request is complete.
        engine.exchange.build_snapshot(engine.get_agent_visible_state())
        return ok(engine.exchange.events_after(after_sequence))


__all__ = ["DEFAULT_SCENARIO_ID", "register_sim_routes"]
