"""Background tick-loop helper."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import time


def run_tick_loop(engine: Any) -> None:
    """Run the engine tick loop without embedding business logic here."""
    last_wall = time.time()
    while engine.clock["running"]:
        now = time.time()
        # A slow sensor/fusion pass must not be paid back as a large simulated
        # time jump on the following frame.  At most two normal frames are
        # advanced; the dashboard remains smooth under transient load.
        real_dt = min(max(0.0, now - last_wall), engine._tick_interval * 2.0)
        last_wall = now
        sim_dt = real_dt * engine.clock["speed"]
        motion_limit = engine._director_motion_limit_sec
        if motion_limit is not None:
            remaining = max(
                0.0,
                float(motion_limit) - float(engine.clock.get("elapsed_sec", 0) or 0),
            )
            sim_dt = min(sim_dt, remaining)
            if sim_dt <= 1e-9:
                engine.clock["running"] = False
                if engine.clock.get("lifecycle") != "completed":
                    engine.clock["lifecycle"] = "paused"
                engine._notify_lifecycle()
                break
        try:
            with engine._lock:
                engine._tick(sim_dt)
                engine.clock["last_tick_wall_time"] = now
                engine.clock["last_tick_error"] = None
                if (
                    motion_limit is not None
                    and float(engine.clock.get("elapsed_sec", 0) or 0)
                    >= float(motion_limit) - 1e-9
                ):
                    engine.clock["running"] = False
                    if engine.clock.get("lifecycle") != "completed":
                        engine.clock["lifecycle"] = "paused"
                    engine._notify_lifecycle()
        except Exception as exc:  # keep a failed daemon from looking healthy
            engine.clock["running"] = False
            engine.clock["lifecycle"] = "error"
            engine.clock["last_tick_error"] = f"{type(exc).__name__}: {exc}"
            engine.alerts.append({
                "level": "CRITICAL",
                "type": "simulation_tick_failed",
                "msg": "仿真时钟异常停止，请查看服务日志后重试",
                "time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            })
            engine._notify_lifecycle()
            break
        elapsed = time.time() - now
        time.sleep(max(0.01, engine._tick_interval - elapsed))
