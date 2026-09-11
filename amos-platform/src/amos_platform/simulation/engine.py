"""Continuous simulation clock, motion, observations, fusion, and event state.

The engine owns complete scripted truth and projects causal operator/Agent views
through separate visibility boundaries.

Usage:
    engine = SimEngine()
    engine.load_scenario(scenario_dict)
    engine.start()    # begins tick loop in background thread
    state = engine.get_operator_state()  # poll operator-visible state
    engine.stop()
"""

from __future__ import annotations

import math
import random
import threading
import time
from datetime import datetime, timezone

from amos_platform.domain.policies.kill_chain import KILL_CHAIN_PHASES, requires_decision_alert
from amos_platform.fusion.track_fusion import SensorFusionEngine
from amos_platform.simulation.state_projector import build_internal_state
from amos_platform.simulation.asset_motion import WaypointNav
from amos_platform.simulation.scenario_loader import load_scenario_into_engine
from amos_platform.simulation.tick_loop import run_tick_loop
from amos_platform.simulation.world_state import MeshNetwork
from amos_platform.simulation.exchange_contract import SimulationExchange
from amos_platform.simulation.media_capture import MediaCaptureRuntime
from amos_platform.domain.policies.geofence import GeofenceManager
from amos_platform.frontend_state.agent_state import build_agent_visible_state
from amos_platform.frontend_state.operator_state import build_operator_state


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _bearing_deg(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Return a local-map course where 0° is north and 90° is east."""
    mean_lat = math.radians((lat1 + lat2) / 2.0)
    north = lat2 - lat1
    east = (lng2 - lng1) * math.cos(mean_lat)
    return (math.degrees(math.atan2(east, north)) + 360.0) % 360.0


def _turn_toward(current: float, target: float, max_turn: float) -> float:
    delta = (target - current + 180.0) % 360.0 - 180.0
    delta = max(-max_turn, min(max_turn, delta))
    return (current + delta) % 360.0


def _advance_position(lat: float, lng: float, heading_deg: float, distance_nm: float) -> tuple[float, float]:
    """Advance a short local leg while preserving east-west scale by latitude."""
    heading_rad = math.radians(heading_deg)
    dlat = distance_nm / 60.0 * math.cos(heading_rad)
    lon_scale = max(0.2, math.cos(math.radians(lat)))
    dlng = distance_nm / (60.0 * lon_scale) * math.sin(heading_rad)
    return round(lat + dlat, 6), round(lng + dlng, 6)


class SimEngine:
    """Real-time simulation engine with background tick loop."""

    def __init__(self, *, seed: int = 0):
        self._seed = int(seed)
        self._rng = random.Random(self._seed)
        # Subsystems
        self.sensor_fusion = SensorFusionEngine(rng=self._rng)
        self.waypoint_nav = WaypointNav()
        self.mesh = MeshNetwork()
        self.geofence = GeofenceManager()
        self.exchange = SimulationExchange()
        self.media_capture = MediaCaptureRuntime()

        # State
        self.assets: dict[str, dict] = {}
        self.threats: dict[str, dict] = {}
        self.weapons: dict[str, dict] = {}
        self.alerts: list[dict] = []
        self.events: list[dict] = []
        self.tasks: list[dict] = []
        self._scenario_task_schedule: list[dict] = []
        self._scenario_capture_plans: list[dict] = []
        self._scenario_identification_rules: list[dict] = []
        self._evidence_classification_applied: set[str] = set()
        self._scenario_asset_follow_tasks: list[dict] = []
        self._scenario_coordination_links: list[dict] = []
        self._scenario_protected_assets: list[dict] = []
        self._engagement_policy: dict = {}
        self._operator_contact_labels: dict[str, str] = {}
        self.scenario_story: dict = {}
        self._story_emitted: set[str] = set()

        # Clock
        self.clock = {
            "elapsed_sec": 0.0,
            "speed": 1,
            "running": False,
            "lifecycle": "empty",
            "started_at": None,
            "last_tick_wall_time": None,
            "last_tick_error": None,
        }

        # Threading
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._tick_interval = 0.5  # seconds wall clock
        self._director_motion_limit_sec: float | None = None
        self._speed_lock_reasons: set[str] = set()
        self._speed_before_lock: float | None = None
        self._engagement_warnings: dict[str, dict] = {}
        self.lifecycle_callback = None

    def _notify_lifecycle(self) -> None:
        """Notify an optional runtime archive without affecting simulation."""
        callback = self.lifecycle_callback
        if callback is None:
            return
        try:
            callback(str(self.clock.get("lifecycle") or "unknown"))
        except Exception:
            # Archival failures must never create synthetic simulation errors.
            # The next API lifecycle action can retry the durable update.
            return

    # ── Lifecycle ──────────────────────────────────────────────

    def configure_seed(self, seed: int) -> int:
        """Reset the engine-local pseudo-random stream for a reproducible run."""
        if isinstance(seed, bool) or (isinstance(seed, float) and not seed.is_integer()):
            raise ValueError("seed must be an integer")
        value = int(seed)
        if value < 0 or value > 2**32 - 1:
            raise ValueError("seed must be an integer from 0 to 4294967295")
        self._seed = value
        self._rng.seed(value)
        self.clock["seed"] = value
        return value

    def load_scenario(self, scenario: dict, *, seed: int | None = None) -> None:
        """Load assets and threats from a scenario dict into the engine."""
        with self._lock:
            self._director_motion_limit_sec = None
            self.configure_seed(scenario.get("default_seed", self._seed) if seed is None else seed)
            load_scenario_into_engine(self, scenario, _now_iso)

    def start(self) -> None:
        """Start the simulation tick loop in a background daemon thread."""
        if self.clock.get("lifecycle") == "completed":
            self.clock["running"] = False
            return
        if self._thread and self._thread.is_alive():
            # A concurrent stop/start can observe the old loop while it is
            # finishing. Reuse a healthy loop, but never reuse one that failed.
            if not self.clock.get("last_tick_error"):
                self.clock["running"] = True
                self.clock["lifecycle"] = "running"
                return
            self._thread.join(timeout=self._tick_interval * 2 + 0.2)
        self.clock["running"] = True
        self.clock["lifecycle"] = "running"
        self.clock["started_at"] = time.time()
        self.clock["last_tick_error"] = None
        self._thread = threading.Thread(target=self._tick_loop, daemon=True)
        self._thread.start()
        self.alerts.append({"level": "INFO", "msg": "仿真已启动", "time": _now_iso()})
        self._notify_lifecycle()

    def stop(self) -> None:
        """Stop the simulation."""
        self.clock["running"] = False
        if self.clock.get("lifecycle") != "completed":
            self.clock["lifecycle"] = "stopped"
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None
        self.alerts.append({"level": "INFO", "msg": "仿真已停止", "time": _now_iso()})
        self._notify_lifecycle()

    def pause(self) -> None:
        """Pause simulation (keeps state, stops ticking)."""
        self.clock["running"] = False
        if self.clock.get("lifecycle") != "completed":
            self.clock["lifecycle"] = "paused"
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=self._tick_interval * 2 + 0.2)
            if not thread.is_alive():
                self._thread = None
        self.alerts.append({"level": "INFO", "msg": "仿真已暂停", "time": _now_iso()})
        self._notify_lifecycle()

    def resume(self) -> None:
        """Resume simulation."""
        if self.clock.get("lifecycle") == "completed":
            self.clock["running"] = False
            return
        if self._thread and self._thread.is_alive():
            self.clock["running"] = True
            self.clock["lifecycle"] = "running"
            return
        self.clock["running"] = True
        self.clock["lifecycle"] = "running"
        self.clock["started_at"] = time.time() - self.clock["elapsed_sec"] / self.clock["speed"]
        self.clock["last_tick_error"] = None
        self._thread = threading.Thread(target=self._tick_loop, daemon=True)
        self._thread.start()
        self.alerts.append({"level": "INFO", "msg": "仿真已恢复", "time": _now_iso()})
        self._notify_lifecycle()

    def set_speed(self, multiplier: float) -> None:
        """Set simulation speed multiplier without changing physical rates."""
        valid = [0.25, 0.5, 1, 2, 4, 8, 16, 32]
        if multiplier not in valid:
            multiplier = min(valid, key=lambda x: abs(x - multiplier))
        with self._lock:
            if self._speed_lock_reasons:
                multiplier = 1
            old = self.clock["speed"]
            self.clock["speed"] = multiplier
            if self.clock.get("running") and not (self._thread and self._thread.is_alive()):
                self._thread = threading.Thread(target=self._tick_loop, daemon=True)
                self._thread.start()
        self.alerts.append({
            "level": "INFO",
            "msg": f"仿真速度: {old}x → {multiplier}x",
            "time": _now_iso(),
        })

    def lock_speed_for_confirmation(self, reason: str) -> None:
        """Hold playback at 1x until the named operator confirmation completes."""
        reason = str(reason or "awaiting_confirmation")
        with self._lock:
            if reason in self._speed_lock_reasons:
                return
            if not self._speed_lock_reasons:
                self._speed_before_lock = float(self.clock.get("speed", 1) or 1)
            self._speed_lock_reasons.add(reason)
            self.clock["speed_locked_reason"] = reason
            self.clock["speed_locked_reasons"] = sorted(self._speed_lock_reasons)
            self.clock["speed_resume_value"] = self._speed_before_lock
            if float(self.clock.get("speed", 1) or 1) != 1:
                self.set_speed(1)

    def unlock_speed_for_confirmation(self, reason: str, *, restore: bool = True) -> None:
        """Release one confirmation lock and restore speed after the final lock."""
        reason = str(reason or "awaiting_confirmation")
        with self._lock:
            self._speed_lock_reasons.discard(reason)
            if self._speed_lock_reasons:
                self.clock["speed_locked_reason"] = sorted(self._speed_lock_reasons)[0]
                self.clock["speed_locked_reasons"] = sorted(self._speed_lock_reasons)
                return
            resume_speed = self._speed_before_lock
            self._speed_before_lock = None
            self.clock.pop("speed_locked_reason", None)
            self.clock.pop("speed_locked_reasons", None)
            self.clock.pop("speed_resume_value", None)
            if (
                restore and resume_speed is not None
                and float(self.clock.get("speed", 1) or 1) != resume_speed
            ):
                self.set_speed(resume_speed)

    def step(self, dt: float = 1.0) -> float:
        """Advance by one explicit simulation step while remaining paused."""
        value = float(dt)
        if value <= 0 or value > 30:
            raise ValueError("step dt must be greater than 0 and no more than 30 seconds")
        if self.clock.get("running"):
            self.pause()
        with self._lock:
            if self.clock.get("lifecycle") == "completed":
                return float(self.clock.get("elapsed_sec", 0) or 0)
            self._tick(value)
            if self.clock.get("lifecycle") != "completed":
                self.clock["lifecycle"] = "paused"
            self.clock["running"] = False
            return float(self.clock.get("elapsed_sec", 0) or 0)

    # ── Tick Loop ──────────────────────────────────────────────

    def _tick_loop(self) -> None:
        """Background thread: tick every 0.5s wall clock."""
        run_tick_loop(self)

    def _next_causal_boundary(self, current: float, target: float) -> float | None:
        """Return the next private schedule boundary inside ``(current, target)``."""
        candidates: list[float] = []
        for cue in self.scenario_story.get("timeline") or []:
            candidates.append(float(cue.get("at_sec", 0) or 0))
        for plan in self._scenario_capture_plans:
            candidates.append(float(plan.get("at_sec", 0) or 0))
        for task in self._scenario_task_schedule:
            window = task.get("window") if isinstance(task, dict) else None
            if isinstance(window, dict):
                candidates.append(float(window.get("start_sec", 0) or 0))
                if window.get("end_sec") is not None:
                    candidates.append(float(window["end_sec"]))
        for asset in self.assets.values():
            window = asset.get("_motion_window") or {}
            if window:
                candidates.append(float(window.get("start_sec", 0) or 0))
                if window.get("end_sec") is not None:
                    candidates.append(float(window["end_sec"]))
            phases = asset.get("_behavior_phases") or []
            next_index = int(asset.get("_next_behavior_phase_index", 0) or 0)
            for phase in phases[next_index:]:
                if isinstance(phase, dict):
                    candidates.append(float(phase.get("at_sec", 0) or 0))
        for threat in self.threats.values():
            window = threat.get("_observation_window") or {}
            if window:
                candidates.append(float(window.get("start_sec", 0) or 0))
                if window.get("end_sec") is not None:
                    candidates.append(float(window["end_sec"]))
            script = threat.get("_behavior_script") or {}
            phase_index = threat.get("_behavior_phase")
            phases = script.get("phases") or []
            if isinstance(phase_index, int) and 0 <= phase_index < len(phases):
                duration = phases[phase_index].get("duration_sec")
                if duration is not None:
                    remaining = float(duration) - float(threat.get("_phase_elapsed", 0) or 0)
                    if remaining > 0:
                        candidates.append(current + remaining)
        eligible = [value for value in candidates if current + 1e-9 < value < target - 1e-9]
        return min(eligible) if eligible else None

    def _tick(self, dt: float) -> None:
        """Advance simulated time using bounded, schedule-aligned physics steps."""
        requested = float(dt)
        if requested <= 0:
            return
        current = float(self.clock.get("elapsed_sec", 0) or 0)
        controls = self.scenario_story.get("demo_controls") or {}
        duration = float(controls.get("duration_sec", 0) or 0)
        target = current + requested
        if self._director_motion_limit_sec is not None:
            target = min(target, float(self._director_motion_limit_sec))
        if controls.get("auto_stop") and duration > 0:
            target = min(target, duration)
        while float(self.clock.get("elapsed_sec", 0) or 0) < target - 1e-9:
            current = float(self.clock.get("elapsed_sec", 0) or 0)
            segment_end = target
            boundary = self._next_causal_boundary(current, segment_end)
            if boundary is not None:
                segment_end = boundary
            step_dt = segment_end - current
            if step_dt <= 1e-9:
                break
            self._tick_once(step_dt)
            if float(self.clock.get("elapsed_sec", 0) or 0) <= current:
                break

    def _tick_once(self, dt: float) -> None:
        """Execute one bounded physical step; callers use :meth:`_tick`."""
        controls = self.scenario_story.get("demo_controls") or {}
        duration = float(controls.get("duration_sec", 0) or 0)
        if controls.get("auto_stop") and duration > 0:
            remaining = max(0.0, duration - float(self.clock.get("elapsed_sec", 0) or 0))
            dt = min(float(dt), remaining)
        if dt <= 0:
            return
        self.clock["elapsed_sec"] += dt
        self._update_scenario_tasks()

        # Emit operator-visible scripted narrative cues once as simulation time advances.
        for cue in self.scenario_story.get("timeline") or []:
            active_branch = str(
                self.clock.get("scenario_branch")
                or self.scenario_story.get("default_branch")
                or "standard"
            )
            cue_branches = {str(value) for value in cue.get("branch_ids") or ["*"] if value}
            if "*" not in cue_branches and active_branch not in cue_branches:
                continue
            cue_id = str(cue.get("cue_id") or "")
            if not cue_id or cue_id in self._story_emitted:
                continue
            if self.clock["elapsed_sec"] < float(cue.get("at_sec") or 0.0):
                continue
            self._story_emitted.add(cue_id)
            self.scenario_story["current_cue_id"] = cue_id
            self.alerts.append({
                "level": cue.get("level", "INFO"),
                "msg": cue.get("title", "场景事件"),
                "time": _now_iso(),
                "type": "scenario_cue",
                "cue_id": cue_id,
                "phase": cue.get("phase"),
            })
            self.events.append({
                "type": "scenario_cue",
                "cue_id": cue_id,
                "phase": cue.get("phase"),
                "title": cue.get("title"),
                "description": cue.get("description"),
                "media_ids": list(cue.get("media_ids") or []),
                "sim_time": round(self.clock["elapsed_sec"], 2),
                "timestamp": time.time(),
            })

        # Change each platform's actual route, speed and operating state when
        # its mission phase becomes due.  This models airborne holding/search
        # patterns and maritime patrols without exposing future coordinates to
        # operator-facing state.
        current_sim_time = float(self.clock["elapsed_sec"])
        for asset_id, asset in self.assets.items():
            phases = asset.get("_behavior_phases") or []
            next_index = int(asset.get("_next_behavior_phase_index", 0) or 0)
            while next_index < len(phases):
                phase = phases[next_index]
                if not isinstance(phase, dict):
                    next_index += 1
                    continue
                at_sec = float(phase.get("at_sec", 0) or 0)
                if current_sim_time + 1e-9 < at_sec:
                    break
                if isinstance(phase.get("route"), list):
                    self.waypoint_nav.set_route(
                        asset_id,
                        [dict(point) for point in phase["route"]],
                        mode=str(phase.get("mode") or "hold"),
                    )
                if phase.get("speed_kts") is not None:
                    phase_speed = float(phase.get("speed_kts") or 0)
                    asset["_cruise_speed_kts"] = phase_speed
                    asset["speed_kts"] = phase_speed
                if phase.get("alt_ft") is not None:
                    asset["position"]["alt_ft"] = float(phase.get("alt_ft") or 0)
                asset["status"] = str(phase.get("status") or asset.get("_configured_status") or "active")
                asset["_current_behavior"] = str(phase.get("behavior") or "")
                asset["_current_behavior_label"] = str(phase.get("label") or "")
                asset["_behavior_phase_started_at"] = at_sec
                next_index += 1
                asset["_next_behavior_phase_index"] = next_index
                self.events.append({
                    "type": "asset_behavior_phase_started",
                    "asset_id": asset_id,
                    "behavior": asset["_current_behavior"],
                    "label": asset["_current_behavior_label"],
                    "sim_time": round(current_sim_time, 2),
                    "timestamp": time.time(),
                })

        # Apply private scenario motion windows before movement. These windows
        # prevent standby or post-action assets from following future task
        # routes before the corresponding phase has actually occurred.
        for asset in self.assets.values():
            window = asset.get("_motion_window") or {}
            if not window:
                continue
            start_sec = float(window.get("start_sec", 0) or 0)
            end_raw = window.get("end_sec")
            active_now = current_sim_time >= start_sec and (
                end_raw is None or current_sim_time < float(end_raw)
            )
            if window.get("activate_on_follow"):
                active_now = bool(asset.get("_operator_follow_visible")) and (
                    end_raw is None or current_sim_time < float(end_raw)
                )
            # A behavior phase may set the scripted platform to ``active`` at
            # the same instant its motion window opens.  Apply the physical
            # host origin in either case so deck-launched assets never appear
            # to originate from a stale, preconfigured map coordinate.
            if (
                active_now
                and window.get("launch_from_asset")
                and not asset.get("_follow_launch_origin_applied")
            ):
                self._apply_follow_launch_origin(asset)
            if active_now and asset.get("status") == "staged":
                self._apply_follow_launch_origin(asset)
                asset["status"] = asset.get("_configured_status", "active")
            if (
                not active_now
                and end_raw is not None
                and current_sim_time >= float(end_raw)
                and asset.get("status") in {"active", "operational"}
            ):
                asset["status"] = "holding"
            if active_now and asset.get("status") in {"active", "operational"}:
                asset["speed_kts"] = float(asset.get("_cruise_speed_kts", 0) or 0)
            else:
                asset["speed_kts"] = 0.0

        self._update_asset_follow_tasks()

        # 1. Move assets along waypoint routes
        wp_events = self.waypoint_nav.tick(self.assets, dt)
        for ev in wp_events:
            self.events.append(ev)

        # 2. Unrouted mobile assets continue by dead reckoning. Scripted route
        # completion never reaches this branch because exhausted hold routes
        # remain registered.
        for aid, a in self.assets.items():
            if a["status"] not in ("operational", "active", "holding"):
                continue
            if aid not in self.waypoint_nav.get_all():
                pos = a["position"]
                distance_nm = max(0.0, float(a.get("speed_kts", 0) or 0)) * dt / 3600.0
                if distance_nm > 0:
                    pos["lat"], pos["lng"] = _advance_position(
                        float(pos.get("lat", 0)),
                        float(pos.get("lng", 0)),
                        float(a.get("heading_deg", 0) or 0),
                        distance_nm,
                    )

            # Health changes scale with simulation time rather than frame
            # count. A scripted communications fault is therefore not hidden
            # by large random jumps at high playback speed.
            health = a.get("health", {})
            if "battery_pct" in health:
                endurance_sec = max(3600.0, float(a.get("endurance_hr", 1) or 1) * 3600.0)
                health["battery_pct"] = max(
                    5.0,
                    float(health.get("battery_pct", 100)) - (100.0 / endurance_sec) * dt,
                )
            if "fuel_pct" in health:
                endurance_sec = max(3600.0, float(a.get("endurance_hr", 1) or 1) * 3600.0)
                cruise = max(1.0, float(a.get("_cruise_speed_kts", 0) or 0))
                operating_factor = 0.25 + 0.75 * min(1.0, float(a.get("speed_kts", 0) or 0) / cruise)
                health["fuel_pct"] = max(
                    3.0,
                    float(health.get("fuel_pct", 100)) - (100.0 / endurance_sec) * dt * operating_factor,
                )
            if "comms_strength" in health:
                comms_cap = float(a.get("_comms_fault_cap", 100.0) or 100.0)
                health["comms_strength"] = max(
                    20.0,
                    min(comms_cap, float(health.get("comms_strength", 90)) + self._rng.uniform(-0.04, 0.04) * math.sqrt(dt)),
                )

        # Persist causal own-asset trails in simulation time. Fixed resources
        # retain a single position and therefore never acquire a false trail.
        sim_time = round(float(self.clock["elapsed_sec"]), 2)
        for asset in self.assets.values():
            pos = asset.get("position") or {}
            history = asset.setdefault("_history_path", [])
            cutoff = sim_time - 900.0
            # A platform that has already reached a hold point must not keep
            # drawing an old approach route throughout the final assessment.
            # Keep only the last 15 simulated minutes of actual motion.
            history[:] = [
                point for point in history[-240:]
                if float(point.get("sim_time", 0) or 0) >= cutoff
            ]
            sample = {"lat": pos.get("lat", 0), "lng": pos.get("lng", 0), "sim_time": sim_time}
            previous = history[-1] if history else None
            moved = previous is None or (
                abs(float(previous.get("lat", 0)) - float(sample["lat"]))
                + abs(float(previous.get("lng", 0)) - float(sample["lng"]))
            ) >= 0.00001
            enough_time = previous is None or sim_time - float(previous.get("sim_time", 0)) >= 3.0
            if moved and enough_time:
                history.append(sample)
                asset["_history_path"] = [
                    point for point in history[-240:]
                    if float(point.get("sim_time", 0) or 0) >= cutoff
                ]

        # 3. Move threats
        for tid, t in list(self.threats.items()):
            if t.get("neutralized"):
                continue
            speed = t.get("speed_kts", 0)
            if speed <= 0:
                continue
            domain = str(t.get("domain") or "").lower()
            default_turn_rate = 2.5 if domain == "air" else 0.7
            turn_rate = float((t.get("flight_profile") or {}).get("turn_rate_dps", default_turn_rate))
            remaining_sec = float(dt)
            while remaining_sec > 1e-9:
                sub_dt = min(1.0, remaining_sec)
                flight_route = t.get("_flight_route") or []
                desired = float(t.get("_commanded_heading", t.get("heading", 0)))
                if flight_route:
                    target = flight_route[0]
                    route_distance_nm = self.waypoint_nav._haversine(
                        t["lat"], t["lng"], float(target["lat"]), float(target["lng"]),
                    )
                    if route_distance_nm < 0.01:
                        t["lat"] = round(float(target["lat"]), 6)
                        t["lng"] = round(float(target["lng"]), 6)
                        flight_route.pop(0)
                        if not flight_route and t.get("_hold_when_route_complete"):
                            t["speed_kts"] = 0.0
                            t["_behavior_phase_name"] = "已驶离护航航线"
                            remaining_sec = 0.0
                            break
                        if flight_route:
                            target = flight_route[0]
                    if flight_route:
                        desired = _bearing_deg(
                            t["lat"], t["lng"], float(target["lat"]), float(target["lng"]),
                        )
                t["heading"] = _turn_toward(
                    float(t.get("heading", desired)),
                    desired,
                    max(0.0, turn_rate * sub_dt),
                )
                t["lat"], t["lng"] = _advance_position(
                    float(t["lat"]),
                    float(t["lng"]),
                    float(t.get("heading", 0) or 0),
                    float(speed) * sub_dt / 3600.0,
                )
                remaining_sec -= sub_dt
            # Scripted contacts follow their declared course with only small
            # navigation noise; unscripted contacts keep the generic drift.
            if not (t.get("_behavior_script") or t.get("flight_profile")):
                t["heading"] = (
                    t["heading"] + self._rng.uniform(-0.08, 0.08) * math.sqrt(dt)
                ) % 360

        # 3.3. Execute behavior scripts for threats with _behavior_script
        for tid, t in list(self.threats.items()):
            script = t.get("_behavior_script")
            if not script or t.get("neutralized") or t.get("_impact_pending"):
                continue
            phases = script.get("phases", [])
            current_phase_idx = t.get("_behavior_phase", 0)
            if current_phase_idx is None or current_phase_idx >= len(phases):
                continue

            # Accumulate phase elapsed time
            t["_phase_elapsed"] = t.get("_phase_elapsed", 0) + dt
            phase = phases[current_phase_idx]
            phase_dur = phase.get("duration_sec", float("inf"))

            # Check phase transition
            if t["_phase_elapsed"] >= phase_dur:
                # Execute explicit phase-exit actions. By default, spawn actions
                # fire on phase entry so a "release" phase is visible as soon as
                # the scenario enters that stage.
                if phase.get("spawn_on") == "exit":
                    self._spawn_phase_child(tid, phase, expected_timing="exit")

                impact = phase.get("risk_impact", "")
                if impact == "escalate" or impact == "critical":
                    new_risk = phase.get("risk_level", t["risk_level"])
                    t["risk_level"] = new_risk

                # Advance to next phase
                t["_behavior_phase"] = current_phase_idx + 1
                t["_phase_elapsed"] = 0.0
                next_idx = t["_behavior_phase"]
                if next_idx < len(phases):
                    next_phase = phases[next_idx]
                    t["_behavior_phase_name"] = next_phase.get("name", "")
                    # Apply new phase kinematics + risk level
                    if "heading" in next_phase:
                        t["_commanded_heading"] = next_phase["heading"]
                    if "speed_kts" in next_phase:
                        t["speed_kts"] = next_phase["speed_kts"]
                    if "risk_level" in next_phase:
                        new_risk = next_phase["risk_level"]
                        t["risk_level"] = new_risk
                    for key, value in (next_phase.get("state_updates") or {}).items():
                        if key in {"ais_match", "iff_status", "rf_freq_mhz", "power_dbm", "ir_signature"}:
                            t[key] = value
                    self._spawn_phase_child(tid, next_phase, expected_timing="enter")
                else:
                    t["_behavior_phase_name"] = "completed"

        # 3.5. Compute TTG + nearest-asset for each threat
        for tid, t in self.threats.items():
            if t.get("neutralized"):
                t.pop("nearest_asset_id", None)
                t.pop("nearest_asset_distance_nm", None)
                t.pop("ttg_sec", None)
                continue
            tlat, tlng = t["lat"], t["lng"]
            best_asset_id = None
            best_dist = float("inf")
            for aid, a in self.assets.items():
                if a.get("status") not in ("operational", "active", "holding"):
                    continue
                apos = a.get("position", a)
                alat, alng = apos.get("lat", 0), apos.get("lng", 0)
                dist = WaypointNav._haversine(tlat, tlng, alat, alng)
                if dist < best_dist:
                    best_dist = dist
                    best_asset_id = aid
            t["nearest_asset_id"] = best_asset_id
            t["nearest_asset_distance_nm"] = round(best_dist, 2) if best_asset_id else None
            # TTG = distance / relative speed (threat speed + asset speed, simplified)
            rel_speed = t.get("speed_kts", 0)
            if best_asset_id and rel_speed > 0:
                t["ttg_sec"] = round(best_dist / rel_speed * 3600.0, 1)
            else:
                t["ttg_sec"] = None

        # 3.6. Move weapons + hit detection
        finished_weapons = []
        for wid, w in self.weapons.items():
            weapon_dt = float(dt)
            if w.get("status") == "scheduled":
                launch_at = float(w.get("scheduled_launch_time", 0) or 0)
                now = float(self.clock.get("elapsed_sec", 0) or 0)
                if now + 1e-9 < launch_at:
                    w["eta_sec"] = round(
                        launch_at - now + float(w.get("flight_time_sec", 0) or 0), 1,
                    )
                    continue
                w["status"] = "in_flight"
                w["launch_time"] = launch_at
                weapon_dt = min(float(dt), max(0.0, now - launch_at))
            if w.get("status") != "in_flight":
                finished_weapons.append(wid)
                continue
            target_id = w.get("target_threat_id", "")
            target = self.threats.get(target_id)
            if not target or target.get("neutralized"):
                w["status"] = "aborted"
                finished_weapons.append(wid)
                self.alerts.append({
                    "level": "WARNING",
                    "msg": f"武器 {wid} 目标丢失，中止",
                    "time": _now_iso(),
                    "type": "weapon_aborted",
                })
                continue

            # Guidance: steer toward target
            tlat, tlng = target["lat"], target["lng"]
            wlat, wlng = w["lat"], w["lng"]
            dist_nm = WaypointNav._haversine(wlat, wlng, tlat, tlng)

            # Update heading to point at target (simple pursuit)
            bearing = _bearing_deg(wlat, wlng, tlat, tlng)
            w["heading"] = bearing

            # Move weapon
            speed = w.get("speed_kts", 500)
            travel_nm = float(speed) * weapon_dt / 3600.0
            reaches_target = travel_nm >= dist_nm
            if reaches_target:
                w["lat"], w["lng"] = float(tlat), float(tlng)
            else:
                w["lat"], w["lng"] = _advance_position(
                    float(wlat), float(wlng), bearing, travel_nm,
                )

            # Update ETA
            if speed > 0:
                w["eta_sec"] = round(dist_nm / speed * 3600.0, 1)

            # Hit detection
            if dist_nm < 0.3 or reaches_target:  # within this simulation step -> impact
                # A physical hit and a confirmed kill are separate tactical
                # states.  Keep the target stopped at the impact point until a
                # real post-impact EO/IR or SAR observation confirms effects.
                damage = "impact_pending"
                w["status"] = "hit"
                w["damage_state"] = damage
                if (self._engagement_policy or {}).get("stochastic_damage_assessment"):
                    effect_rng = random.Random(
                        f"{self._seed}:{wid}:{w.get('target_threat_id', '')}:weapon-effect"
                    )
                    w["effect_roll"] = round(effect_rng.random(), 6)
                    w["predicted_damage_state"] = (
                        "destroyed"
                        if float(w["effect_roll"]) <= float(w.get("p_kill", 0) or 0)
                        else "damaged"
                    )
                else:
                    w["predicted_damage_state"] = "destroyed"
                w["eta_sec"] = 0.0
                impact_time = float(self.clock.get("elapsed_sec", 0.0) or 0.0)
                coordinated_policy = (self._engagement_policy or {}).get("coordinated_engagement") or {}
                if (
                    reaches_target and float(speed) > 0
                    and coordinated_policy.get("coordination_mode") == "time_on_target"
                ):
                    overshoot_sec = max(0.0, travel_nm - dist_nm) / float(speed) * 3600.0
                    impact_time -= min(weapon_dt, overshoot_sec)
                w["impact_sim_time"] = round(impact_time, 2)
                self._apply_damage(target, damage)
                coordinated_policy = (self._engagement_policy or {}).get("coordinated_engagement") or {}
                chain_id = str(w.get("coordination_chain_id") or "")
                expected_members = int(
                    w.get("expected_chain_members")
                    or len(coordinated_policy.get("participants") or [])
                    or 0
                )
                if chain_id and (
                    chain_id == str(coordinated_policy.get("chain_id") or "")
                    or bool(w.get("expected_chain_members"))
                ):
                    chain_impacts = [
                        item for item in self.weapons.values()
                        if str(item.get("coordination_chain_id") or "") == chain_id
                        and item.get("status") == "hit"
                        and item.get("impact_sim_time") is not None
                    ]
                    if expected_members and len(chain_impacts) >= expected_members and not any(
                        item.get("_tot_audit_emitted") for item in chain_impacts
                    ):
                        impact_times = [float(item["impact_sim_time"]) for item in chain_impacts]
                        actual_span = max(impact_times) - min(impact_times)
                        tolerance = max(
                            0.0,
                            float(
                                w.get("arrival_tolerance_sec")
                                or coordinated_policy.get("arrival_tolerance_sec", 20)
                                or 20
                            ),
                        )
                        compliant = actual_span <= tolerance + 1e-9
                        for item in chain_impacts:
                            item["tot_compliant"] = compliant
                            item["actual_arrival_span_sec"] = round(actual_span, 2)
                            item["_tot_audit_emitted"] = True
                        self.events.append({
                            "type": (
                                "time_on_target_verified"
                                if compliant else "time_on_target_violation"
                            ),
                            "coordination_chain_id": chain_id,
                            "weapon_ids": [item.get("id") for item in chain_impacts],
                            "actual_arrival_span_sec": round(actual_span, 2),
                            "arrival_tolerance_sec": tolerance,
                            "sim_time": round(impact_time, 2),
                            "timestamp": time.time(),
                        })
                finished_weapons.append(wid)
                target_track_id = str(w.get("target_track_id") or "")
                target_track = self.sensor_fusion.tracks.get(target_track_id)
                if target_track is not None:
                    target_track.kill_chain_phase = "ASSESS"
                    target_track.kill_chain_times["ASSESS"] = time.time()
                    target_track.retain_until_sim_time = max(
                        float(getattr(target_track, "retain_until_sim_time", 0.0) or 0.0),
                        float((self.scenario_story.get("demo_controls") or {}).get("duration_sec", 0.0) or 0.0),
                    )
                    target_track.history_path = list(target_track.history_path[-24:])
                    assessment = dict(getattr(target_track, "agent_assessment", {}) or {})
                    assessment.update({
                        "damage_state": "impact_pending",
                        "engagement_status": "pending_assessment",
                        "behavior_label": "导弹命中 · 待毁伤评估",
                    })
                    target_track.agent_assessment = assessment

        # Clean up finished weapons (keep last 10 for history)
        for wid in finished_weapons:
            w = self.weapons.get(wid)
            if (
                w and w.get("status") in ("hit", "aborted")
                and not w.get("_terminal_event_emitted")
            ):
                self.events.append({
                    "type": f"weapon_{w['status']}",
                    "weapon_id": wid,
                    "weapon_type": w.get("weapon_type", ""),
                    "target_threat_id": w.get("target_threat_id", ""),
                    "target_track_id": w.get("target_track_id", ""),
                    "damage_state": w.get("damage_state"),
                    "position": {"lat": w.get("lat"), "lng": w.get("lng")},
                    "sim_time": w.get("impact_sim_time"),
                    "timestamp": time.time(),
                })
                w["_terminal_event_emitted"] = True
        if len(self.weapons) > 50:
            # Prune old finished weapons
            stale = [k for k, v in self.weapons.items()
                     if v.get("status") != "in_flight"]
            for k in stale[:-10]:
                self.weapons.pop(k, None)

        # 4. Sensor fusion tick
        fusion_events = self.sensor_fusion.tick(
            self.assets,
            self.threats,
            dt,
            sim_time=float(self.clock.get("elapsed_sec", 0.0) or 0.0),
            run_id=str(self.clock.get("run_id") or ""),
            scenario_id=str(self.clock.get("scenario_id") or ""),
        )
        for ev in fusion_events:
            self.events.append(ev)

        self._prune_track_motion_histories()
        self._confirm_pending_damage_assessments()
        self._apply_confirmed_contact_behaviors()
        self._update_asset_follow_tasks()

        # 4.5. Ordinary coverage gaps no longer carry truth IDs. Handover
        # policies that need truth associations must use explicit admin/debug
        # paths rather than operator-visible coverage gap payloads.

        # 5. Mesh network tick
        self.mesh.tick(
            self.assets,
            dt,
            sim_time=float(self.clock.get("elapsed_sec", 0.0) or 0.0),
        )

        # Media becomes visible only after the same tick has updated sensors,
        # fusion and communications.  Frozen evidence therefore cannot mix a
        # current platform pose with a previous-tick network topology.
        self.evaluate_media_captures()

        # 6. Geofence tick
        tracks = self.sensor_fusion.get_tracks()
        gf_events = self.geofence.tick(self.assets, tracks)
        for ev in gf_events:
            if ev.get("entity_type") == "contact":
                track = tracks.get(ev["entity_id"]) or {}
                assessment = track.get("agent_assessment") or {}
                confirmed = assessment.get("status") == "confirmed"
                self.alerts.append({
                    "level": "CRITICAL" if confirmed else "WARNING",
                    "msg": (
                        f"已确认高风险接触 {ev['entity_id']} 进入 {ev['geofence_name']}"
                        if confirmed else f"未知接触 {ev['entity_id']} 进入 {ev['geofence_name']}"
                    ),
                    "time": _now_iso(),
                    "type": "contact_geofence_entry",
                })

        # 7. Generate alerts based on simulation state
        self._generate_alerts(dt)

        # 8. Prune event history
        if len(self.events) > 200:
            self.events = self.events[-100:]
        if len(self.alerts) > 100:
            self.alerts = self.alerts[-50:]

        if controls.get("auto_stop") and duration > 0 and self.clock["elapsed_sec"] >= duration:
            self.clock["elapsed_sec"] = duration
            if self.clock.get("running"):
                self.clock["running"] = False
                self.clock["lifecycle"] = "completed"
                self.alerts.append({
                    "level": "INFO",
                    "msg": "仿真完成，保留最终态势",
                    "time": _now_iso(),
                    "type": "scenario_complete",
                })
                self._notify_lifecycle()

        # Maintain a continuous, causal exchange log independently of browser
        # polling so Gateway receives the full run slice at submission time.
        self.exchange.build_snapshot(self.get_agent_visible_state())

    def evaluate_media_captures(self) -> list[dict]:
        """Evaluate due private plans and return newly frozen public records."""
        self._update_scenario_tasks()
        if (
            not self.sensor_fusion.last_observation_batch
            and float(self.clock.get("elapsed_sec", 0) or 0) == 0
        ):
            self.sensor_fusion.sample_observations(
                self.assets,
                self.threats,
                sim_time=0.0,
                run_id=str(self.clock.get("run_id") or ""),
                scenario_id=str(self.clock.get("scenario_id") or ""),
            )
        records = self.media_capture.evaluate(self)
        self._apply_evidence_classification_rules()
        self._update_scenario_tasks()
        for record in records:
            self.events.append({
                "type": "media_captured",
                "capture_id": record.get("capture_id"),
                "media_id": record.get("media_id"),
                "product_type": record.get("product_type"),
                "observation_ids": list(record.get("observation_ids") or []),
                "track_ids": list(record.get("track_ids") or []),
                "sim_time": record.get("captured_at_sim_time"),
                "timestamp": time.time(),
            })
        return records

    def _apply_evidence_classification_rules(self) -> None:
        """Publish simulated sensor classification only after its evidence exists.

        Scenario truth remains private: rules resolve a truth object internally,
        then attach only the evidence-derived class to the corresponding public
        fused track.  Commander must still confirm that candidate before the
        engagement gate can open.
        """
        released = set(self.media_capture.captured_media_ids)
        for rule in self._scenario_identification_rules:
            rule_id = str(rule.get("rule_id") or "")
            target_ref = str(rule.get("target_ref") or "")
            classification = str(rule.get("classification") or "").strip().upper()
            required_media = {
                str(value) for value in rule.get("required_media_ids") or [] if value
            }
            if (
                not rule_id
                or not target_ref
                or not classification
                or not required_media.issubset(released)
            ):
                continue
            for matched_track in self.sensor_fusion.tracks.values():
                applied_key = f"{rule_id}:{matched_track.id}"
                if (
                    applied_key in self._evidence_classification_applied
                    or self._truth_target_for_track(matched_track) != target_ref
                ):
                    continue
                matched_track.classification = classification
                # A collected intelligence product outlives the short sensor
                # access window that created it.  Scenario authors may retain
                # the fused track until a declared validity horizon instead
                # of deleting it as soon as the spacecraft or aircraft leaves
                # coverage.  Freshness remains visible through last_sim_time;
                # retention does not manufacture a new observation.
                if rule.get("retain_until_sec") is not None:
                    matched_track.retain_until_sim_time = max(
                        float(matched_track.retain_until_sim_time or 0.0),
                        float(rule["retain_until_sec"]),
                    )
                matched_track.agent_assessment = {
                    "status": "pending",
                    "label": "新证据待后端确认",
                    "source": None,
                    "evidence_media_ids": sorted(required_media),
                }
                self._evidence_classification_applied.add(applied_key)
                self.events.append({
                    "type": "sensor_classification_candidate",
                    "track_id": matched_track.id,
                    "classification": classification,
                    "evidence_media_ids": sorted(required_media),
                    "sim_time": round(float(self.clock.get("elapsed_sec", 0) or 0), 2),
                    "timestamp": time.time(),
                })

    def _update_asset_follow_tasks(self) -> None:
        """Retask scenario assets from public fused-track attributes only."""
        elapsed = float(self.clock.get("elapsed_sec", 0) or 0)
        for task in self._scenario_asset_follow_tasks:
            asset_id = str(task.get("asset_id") or "")
            if not asset_id or asset_id not in self.assets:
                continue
            asset = self.assets[asset_id]
            if asset.get("status") not in {"active", "operational", "holding", "staged"}:
                asset["_follow_pending_prompt"] = None
                continue
            start_sec = float(task.get("start_sec", 0) or 0)
            end_raw = task.get("end_sec")
            if elapsed < start_sec or (end_raw is not None and elapsed >= float(end_raw)):
                asset["_follow_pending_prompt"] = None
                continue
            pending_return_track_id = str(asset.get("_follow_return_pending_track_id") or "")
            return_not_before = asset.get("_follow_return_not_before_sec")
            if (
                pending_return_track_id
                and return_not_before is not None
                and elapsed >= float(return_not_before)
            ):
                self._return_follow_assets_after_strike(pending_return_track_id)
            if asset.get("_follow_returning_home"):
                self._update_follow_return_home(asset, task)
                continue
            candidates = self._follow_task_candidates(task, asset)
            if not candidates:
                asset["_follow_pending_prompt"] = None
                continue
            target = candidates[0]
            target_id = str(target.get("id") or target.get("track_id") or "")
            requires_authorization = bool(task.get("requires_operator_authorization"))
            already_following = (
                str(asset.get("_follow_track_id") or "") == target_id
                and bool(asset.get("_operator_follow_visible"))
            )
            authorized_track_id = str(asset.get("_follow_launch_authorized_track_id") or "")
            if requires_authorization and not already_following and authorized_track_id != target_id:
                asset["_follow_pending_prompt"] = {
                    "prompt_type": "launch_follow_uav",
                    "task_id": task.get("task_id"),
                    "asset_id": asset_id,
                    "track_id": target_id,
                    "launch_from_asset": task.get("launch_from_asset"),
                    "message": "是否派出补充侦察无人机跟踪该目标？",
                }
                continue
            asset["_follow_pending_prompt"] = None
            if asset.get("status") in {"staged", "holding"}:
                self._apply_follow_launch_origin(asset)
                asset["status"] = asset.get("_configured_status", "active")
                asset["speed_kts"] = float(asset.get("_cruise_speed_kts", 0) or 0)
                asset["_operator_follow_visible"] = True
            target_lat = float(target["lat"])
            target_lng = float(target["lng"])
            standoff_nm = float(task.get("standoff_nm", 1.5) or 0)
            waypoint = {"lat": target_lat, "lng": target_lng, "label": "FOLLOW"}
            if standoff_nm > 0:
                apos = asset.get("position", asset)
                target_heading = target.get("heading")
                if target_heading is None:
                    velocity = target.get("velocity") or {}
                    north = float(velocity.get("lat", 0) or 0)
                    east = float(velocity.get("lng", 0) or 0) * math.cos(math.radians(target_lat))
                    if abs(north) + abs(east) > 1e-9:
                        target_heading = (math.degrees(math.atan2(east, north)) + 360.0) % 360.0
                if target_heading is not None:
                    # Keep a stable trail position behind the contact. Using
                    # target->UAV as the offset bearing makes the waypoint flip
                    # by 180 degrees whenever the faster UAV crosses it.
                    desired_bearing = (float(target_heading) + 180.0) % 360.0
                else:
                    desired_bearing = float(asset.get("_follow_station_bearing", _bearing_deg(
                        target_lat,
                        target_lng,
                        float(apos.get("lat", 0)),
                        float(apos.get("lng", 0)),
                    )))
                previous_bearing = asset.get("_follow_station_bearing")
                last_guidance_time = asset.get("_follow_guidance_sim_time")
                if previous_bearing is not None and last_guidance_time is not None:
                    guidance_dt = max(0.0, elapsed - float(last_guidance_time))
                    slew_rate = float(task.get("station_bearing_slew_dps", 0.35) or 0.35)
                    desired_bearing = _turn_toward(
                        float(previous_bearing), desired_bearing,
                        min(30.0, max(1.0, slew_rate * guidance_dt)),
                    )
                asset["_follow_station_bearing"] = desired_bearing
                asset["_follow_guidance_sim_time"] = elapsed
                lat, lng = _advance_position(target_lat, target_lng, desired_bearing, standoff_nm)
                waypoint = {"lat": lat, "lng": lng, "label": "FOLLOW"}
                distance_to_station = self.waypoint_nav._haversine(
                    float(apos.get("lat", 0)), float(apos.get("lng", 0)), lat, lng,
                )
                target_speed = self._estimated_track_speed_kts(target)
                if target_speed <= 0:
                    target_speed = float(task.get("fallback_target_speed_kts", 30.0) or 30.0)
                cruise_speed = float(asset.get("_cruise_speed_kts", asset.get("speed_kts", 0)) or 0)
                closure_gain = float(task.get("closure_gain_kts_per_nm", 12.0) or 12.0)
                commanded_speed = target_speed + distance_to_station * closure_gain
                asset["speed_kts"] = round(max(1.0, min(cruise_speed, commanded_speed)), 1)
            self.waypoint_nav.set_route(asset_id, [waypoint], mode="hold")
            asset["_follow_track_id"] = target_id
            asset["_follow_task_id"] = task.get("task_id")
            asset["_follow_target"] = {"track_id": target_id, "lat": target_lat, "lng": target_lng}
            previous = asset.get("_follow_last_event_track_id")
            if previous != target_id:
                asset["_follow_last_event_track_id"] = target_id
                self.events.append({
                    "type": "asset_follow_track",
                    "asset_id": asset_id,
                    "track_id": target_id,
                    "sim_time": round(elapsed, 2),
                    "timestamp": time.time(),
                })

        # The follow-launch confirmation dialog no longer forces 1x playback:
        # the operator-selected demo speed (e.g. 32x) keeps running while the
        # dialog is pending and while the backend analysis continues. Safety
        # is unchanged — the UAV only launches after the explicit
        # authorize_follow_asset operator command.

    def follow_confirmation_pending(self) -> bool:
        """True while any asset waits for the operator follow-launch decision."""
        return any(
            isinstance(asset.get("_follow_pending_prompt"), dict)
            for asset in self.assets.values()
        )

    @staticmethod
    def _estimated_track_speed_kts(track: dict) -> float:
        """Estimate public-track ground speed without consulting hidden truth."""
        lat = float(track.get("lat", 0) or 0)
        velocity = track.get("velocity") if isinstance(track.get("velocity"), dict) else {}
        north_nmps = float(velocity.get("lat", 0) or 0) * 60.0
        east_nmps = float(velocity.get("lng", 0) or 0) * 60.0 * math.cos(math.radians(lat))
        speed_kts = math.hypot(north_nmps, east_nmps) * 3600.0
        if 0.5 <= speed_kts <= 2000.0:
            return speed_kts

        history = [
            point for point in track.get("history_path") or []
            if isinstance(point, dict) and point.get("sim_time") is not None
        ]
        if len(history) < 2:
            return 0.0
        previous, current = history[-2], history[-1]
        dt = float(current["sim_time"]) - float(previous["sim_time"])
        if dt <= 0:
            return 0.0
        distance_nm = WaypointNav._haversine(
            float(previous.get("lat", 0)), float(previous.get("lng", 0)),
            float(current.get("lat", 0)), float(current.get("lng", 0)),
        )
        return distance_nm / dt * 3600.0

    def _follow_task_candidates(self, task: dict, asset: dict) -> list[dict]:
        required_levels = {
            str(level).upper()
            for level in task.get("required_threat_levels") or ["HIGH", "CRITICAL"]
        }
        required_status = {
            str(value).lower()
            for value in task.get("required_assessment_status") or ["confirmed"]
        }
        excluded_classes = {
            str(value).upper()
            for value in task.get("excluded_classifications") or []
        }
        retired_track_ids = {
            str(value)
            for value in asset.get("_follow_retired_track_ids") or []
            if value
        }
        candidates = []
        for track in self.sensor_fusion.get_tracks().values():
            track_id = str(track.get("id") or track.get("track_id") or "")
            if track_id in retired_track_ids:
                continue
            assessment = track.get("agent_assessment") if isinstance(track.get("agent_assessment"), dict) else {}
            status = str(assessment.get("status") or "").lower()
            level = str(track.get("threat_level") or "").upper()
            classification = str(track.get("classification") or "").upper()
            if required_status and status not in required_status:
                continue
            if level not in required_levels:
                continue
            if classification in excluded_classes:
                continue
            if track.get("lat") is None or track.get("lng") is None:
                continue
            candidates.append(track)
        rank = {"CRITICAL": 3, "HIGH": 2, "MEDIUM": 1, "LOW": 0, "UNKNOWN": -1}
        candidates.sort(
            key=lambda row: (
                rank.get(str(row.get("threat_level") or "").upper(), -1),
                float(row.get("confidence", 0) or 0),
            ),
            reverse=True,
        )
        return candidates

    def _return_follow_assets_after_strike(self, target_track_id: str) -> None:
        if not target_track_id:
            return
        for task in self._scenario_asset_follow_tasks:
            asset_id = str(task.get("asset_id") or "")
            asset = self.assets.get(asset_id)
            if not asset or str(asset.get("_follow_track_id") or "") != target_track_id:
                continue
            if not task.get("return_to_launch_after_strike", True):
                continue
            return_after_sec = task.get("return_after_sec")
            elapsed = float(self.clock.get("elapsed_sec", 0) or 0)
            if return_after_sec is not None and elapsed < float(return_after_sec):
                asset["_follow_return_pending_track_id"] = target_track_id
                asset["_follow_return_not_before_sec"] = float(return_after_sec)
                asset["_operator_follow_visible"] = True
                continue
            retired = asset.setdefault("_follow_retired_track_ids", [])
            if target_track_id not in retired:
                retired.append(target_track_id)
            asset["_follow_return_pending_track_id"] = None
            asset["_follow_return_not_before_sec"] = None
            asset["_follow_returning_home"] = True
            asset["_follow_track_id"] = None
            asset["_follow_target"] = None
            asset["_operator_follow_visible"] = True
            if asset.get("status") in {"staged", "holding"}:
                asset["status"] = asset.get("_configured_status", "active")
            asset["speed_kts"] = float(asset.get("_cruise_speed_kts", 0) or 0)
            self._update_follow_return_home(asset, task)

    def _update_follow_return_home(self, asset: dict, task: dict) -> None:
        host = self.assets.get(str(task.get("launch_from_asset") or ""))
        if not host:
            return
        host_pos = host.get("position") or host
        pos = asset.get("position") or asset
        distance_nm = self.waypoint_nav._haversine(
            float(pos.get("lat", 0)),
            float(pos.get("lng", 0)),
            float(host_pos.get("lat", 0)),
            float(host_pos.get("lng", 0)),
        )
        if distance_nm <= float(task.get("return_hide_distance_nm", 0.2) or 0.2):
            pos["lat"] = round(float(host_pos.get("lat", pos.get("lat", 0))), 6)
            pos["lng"] = round(float(host_pos.get("lng", pos.get("lng", 0))), 6)
            asset["status"] = "staged"
            asset["speed_kts"] = 0.0
            asset["_operator_follow_visible"] = False
            asset["_follow_returning_home"] = False
            asset["_follow_launch_origin_applied"] = False
            asset["_follow_launch_authorized_track_id"] = None
            asset["_follow_pending_prompt"] = None
            asset["_follow_station_bearing"] = None
            asset["_follow_guidance_sim_time"] = None
            asset["_follow_return_pending_track_id"] = None
            asset["_follow_return_not_before_sec"] = None
            self.waypoint_nav.clear(str(asset.get("id") or ""))
            return
        asset["status"] = asset.get("_configured_status", "active")
        asset["speed_kts"] = float(asset.get("_cruise_speed_kts", 0) or 0)
        self.waypoint_nav.set_route(str(asset.get("id") or ""), [{
            "lat": float(host_pos.get("lat", 0)),
            "lng": float(host_pos.get("lng", 0)),
            "label": "RETURN",
        }], mode="hold")

    def _apply_follow_launch_origin(self, asset: dict) -> None:
        asset_id = str(asset.get("id") or "")
        if not asset_id or asset.get("_follow_launch_origin_applied"):
            return
        task = next((
            row for row in self._scenario_asset_follow_tasks
            if str(row.get("asset_id") or "") == asset_id and row.get("launch_from_asset")
        ), None)
        # Scripted launches can be tied to a physical host without being an
        # operator-follow task (for example a scheduled carrier deck launch).
        # Prefer an explicit follow task when present, otherwise use the
        # scenario motion window's launch host.
        if not task:
            window = asset.get("_motion_window") or {}
            if window.get("launch_from_asset"):
                task = window
        if not task:
            return
        host = self.assets.get(str(task.get("launch_from_asset")))
        if not host:
            return
        host_pos = host.get("position") or host
        pos = asset.setdefault("position", {})
        pos["lat"] = round(float(host_pos.get("lat", pos.get("lat", 0))), 6)
        pos["lng"] = round(float(host_pos.get("lng", pos.get("lng", 0))), 6)
        if pos.get("alt_ft") is None:
            pos["alt_ft"] = 0
        # Deck and host launches start on the route's first outbound bearing.
        # Retaining a stale scenario heading here produces a visible corkscrew
        # immediately after launch while the turn-rate limiter catches up.
        route = self.waypoint_nav.get_route(asset_id)
        align_route_heading = bool(
            (asset.get("_motion_window") or {}).get("align_route_heading")
        )
        if route and align_route_heading:
            first = route[0]
            asset["heading_deg"] = round(self.waypoint_nav._bearing(
                float(pos.get("lat", 0)), float(pos.get("lng", 0)),
                float(first.get("lat", pos.get("lat", 0))),
                float(first.get("lng", pos.get("lng", 0))),
            ), 1)
        asset["_history_path"] = [{
            "lat": pos.get("lat", 0),
            "lng": pos.get("lng", 0),
            "sim_time": round(float(self.clock.get("elapsed_sec", 0) or 0), 2),
        }]
        asset["_follow_launch_origin_applied"] = True

    def _update_scenario_tasks(self) -> None:
        """Project only current/past scripted work; future schedule stays private."""
        elapsed = float(self.clock.get("elapsed_sec", 0) or 0)
        branch = str(self.clock.get("scenario_branch") or self.scenario_story.get("default_branch") or "standard")
        captured = self.media_capture.captured_media_ids
        analysis = self.scenario_story.get("agent_analysis") or {}
        retained = [
            task for task in self.tasks
            if not isinstance(task, dict) or task.get("source") != "scenario_schedule"
        ]
        captures_by_task: dict[str, list[str]] = {}
        for capture in self._scenario_capture_plans:
            task_id = str(capture.get("required_task_id") or "")
            media_id = str(capture.get("media_id") or "")
            if task_id and media_id:
                captures_by_task.setdefault(task_id, []).append(media_id)
        projected = []
        for task in self._scenario_task_schedule:
            if not isinstance(task, dict):
                continue
            branches = {str(value) for value in task.get("branch_ids") or ["*"]}
            if "*" not in branches and branch not in branches:
                continue
            window = task.get("window") if isinstance(task.get("window"), dict) else {}
            start = float(window.get("start_sec", 0) or 0)
            end = float(window.get("end_sec", start) or start)
            if elapsed < start:
                continue
            task_id = str(task.get("task_id") or "")
            media_ids = captures_by_task.get(task_id, [])
            if elapsed <= end:
                if task.get("task_type") == "command_product" and analysis.get("source") != "commander_workflow":
                    status = "awaiting_backend"
                else:
                    status = "active"
            elif media_ids and all(media_id in captured for media_id in media_ids):
                status = "completed"
            else:
                status = "expired_without_evidence"
            projected.append({
                "task_id": task_id,
                "platform_id": task.get("platform_id"),
                "capability_id": task.get("capability_id"),
                "task_type": task.get("task_type"),
                "window": {"start_sec": start, "end_sec": end},
                "status": status,
                "source": "scenario_schedule",
            })
        self.tasks = retained + projected

    # ── State Access ───────────────────────────────────────────

    def get_state(self) -> dict:
        """Return current full simulation truth state for internal consumers."""
        with self._lock:
            return build_internal_state(self)

    def get_operator_state(self) -> dict:
        """Return operator-visible state without hidden threat truth."""
        self.evaluate_media_captures()
        internal_state = self.get_state()
        for track in internal_state.get("fused_tracks") or []:
            track_id = str(track.get("track_id") or track.get("id") or "")
            if track_id:
                track["display_label"] = self._operator_label_for_track(track_id)
        state = build_operator_state(internal_state)
        policy = self._engagement_policy or {}
        asset_ids = list(policy.get("authorized_asset_ids") or [])
        weapon_names = list(policy.get("authorized_weapons") or [])
        if not asset_ids or not weapon_names:
            return state
        coordinated = policy.get("coordinated_engagement") or {}
        participants = []
        for participant in coordinated.get("participants") or []:
            participant_asset_id = str(participant.get("asset_id") or "")
            participant_weapon_name = str(participant.get("weapon_name") or "")
            if participant_asset_id and participant_weapon_name:
                participants.append({
                    "asset_id": participant_asset_id,
                    "weapon_name": participant_weapon_name,
                    "role": str(participant.get("role") or "member"),
                })
        state["engagement_action"] = {
            "asset_id": str(asset_ids[0]),
            "weapon_name": str(weapon_names[0]),
            "action_label": str(policy.get("action_label") or weapon_names[0]),
            "authorization_message": str(policy.get("authorization_message") or ""),
            "coordinated": len(participants) >= 2,
            "coordination_chain_id": str(coordinated.get("chain_id") or "") or None,
            "participants": participants,
        }
        target_engagements = (
            policy.get("target_engagements")
            if isinstance(policy.get("target_engagements"), dict)
            else {}
        )
        for track in state.get("fused_tracks") or []:
            track_id = str(track.get("track_id") or track.get("id") or "")
            source_track = self.sensor_fusion.tracks.get(track_id)
            truth_target_id = self._truth_target_for_track(source_track)
            assignment = (
                target_engagements.get(str(truth_target_id or ""))
                if truth_target_id else None
            )
            has_target_assignment = isinstance(assignment, dict) and bool(assignment)
            assignment = assignment if has_target_assignment else {}
            selected_asset_id = str(assignment.get("asset_id") or asset_ids[0])
            selected_weapon_name = str(assignment.get("weapon_name") or weapon_names[0])
            eligibility = self.engagement_eligibility(
                track_id,
                asset_id=selected_asset_id,
                weapon_name=selected_weapon_name,
            )
            prior_salvos = sum(
                event.get("type") == "authorized_fire_command"
                and str(event.get("target_track_id") or "") == track_id
                for event in self.events
            )
            maximum_salvos = max(1, int(policy.get("max_salvos_per_track", 1) or 1))
            track["engagement_eligible"] = (
                eligibility.get("eligible") is True and prior_salvos < maximum_salvos
            )
            if eligibility.get("reason"):
                track["engagement_block_reason"] = str(eligibility["reason"])
            elif prior_salvos >= maximum_salvos:
                track["engagement_block_reason"] = "该航迹已完成授权打击"
            assignment_wave = int(assignment.get("wave", 0) or 0)
            if has_target_assignment:
                wave_participants = [
                    {
                        "asset_id": str(member.get("asset_id") or ""),
                        "weapon_name": str(member.get("weapon_name") or ""),
                        "role": str(member.get("role") or "member"),
                    }
                    for member in target_engagements.values()
                    if isinstance(member, dict)
                    and assignment_wave > 0
                    and int(member.get("wave", 0) or 0) == assignment_wave
                ]
            else:
                # A scenario-wide coordinated engagement has no per-target
                # assignment. Preserve the chain on the target action so the
                # operator dialog does not present it as a single weapon.
                wave_participants = [dict(member) for member in participants]
            track["engagement_action"] = {
                "asset_id": selected_asset_id,
                "weapon_name": selected_weapon_name,
                "action_label": str(
                    assignment.get("wave_action_label")
                    or assignment.get("action_label")
                    or policy.get("action_label")
                    or selected_weapon_name
                ),
                "authorization_message": str(
                    assignment.get("authorization_message")
                    or policy.get("authorization_message")
                    or ""
                ),
                "wave": assignment.get("wave"),
                "role": str(assignment.get("role") or ""),
                "coordination_chain_id": str(
                    assignment.get("chain_id")
                    or (coordinated.get("chain_id") if not has_target_assignment else "")
                    or ""
                ) or None,
                "coordinated": len(wave_participants) >= 2,
                "participants": wave_participants,
            }
        prompts = self._operator_follow_launch_prompts(state.get("fused_tracks") or [])
        state["follow_launch_prompts"] = prompts
        state["follow_launch_prompt"] = prompts[0] if prompts else None
        warnings = [dict(value) for value in self._engagement_warnings.values()]
        state["engagement_warnings"] = warnings
        state["engagement_warning"] = warnings[-1] if warnings else None
        return state

    def get_agent_visible_state(self) -> dict:
        """Return agent-visible observations without hidden threat truth."""
        self.evaluate_media_captures()
        return build_agent_visible_state(self.get_state())

    # ── Asset re-tasking (F3 Re-Task Detection support) ──────────

    def _operator_follow_launch_prompts(self, public_tracks: list[dict]) -> list[dict]:
        tracks_by_id = {
            str(track.get("id") or track.get("track_id") or ""): track
            for track in public_tracks
        }
        prompts = []
        for task in self._scenario_asset_follow_tasks:
            asset_id = str(task.get("asset_id") or "")
            asset = self.assets.get(asset_id)
            if not asset:
                continue
            pending = asset.get("_follow_pending_prompt")
            if not isinstance(pending, dict):
                continue
            track_id = str(pending.get("track_id") or "")
            if track_id not in tracks_by_id:
                continue
            track = tracks_by_id[track_id]
            prompts.append({
                "prompt_type": "launch_follow_uav",
                "task_id": str(pending.get("task_id") or task.get("task_id") or ""),
                "asset_id": asset_id,
                "asset_label": str(asset.get("role") or asset_id),
                "track_id": track_id,
                "target_label": str(track.get("display_label") or track_id),
                "launch_from_asset": str(pending.get("launch_from_asset") or task.get("launch_from_asset") or ""),
                "message": str(pending.get("message") or "是否派出补充侦察无人机跟踪该目标？"),
            })
        return prompts

    def retask_asset(self, asset_id: str, waypoints: list[dict]) -> dict:
        """Replace an asset's patrol route with a new set of waypoints.

        Args:
            asset_id: The asset to re-task.
            waypoints: List of waypoint dicts, each with ``lat``, ``lng``,
                       and optional ``label``.

        Returns:
            ``{"asset_id": "...", "waypoint_count": N, "status": "retasked"}``
        """
        with self._lock:
            if asset_id not in self.assets:
                return {"error": f"asset not found: {asset_id}"}
            self.waypoint_nav.set_route(asset_id, waypoints)
            self.alerts.append({
                "level": "INFO",
                "msg": f"平台 {asset_id} 已重新分配：{len(waypoints)} 个航路点",
                "time": _now_iso(),
            })
            return {
                "asset_id": asset_id,
                "waypoint_count": len(waypoints),
                "status": "retasked",
            }

    def retask_asset_to_intercept(self, asset_id: str,
                                  target_lat: float, target_lng: float) -> dict:
        """Redirect an asset to intercept a target position.

        Creates a direct route from the asset's current position to the
        target coordinates.

        Args:
            asset_id: The asset to re-task.
            target_lat: Target latitude.
            target_lng: Target longitude.

        Returns:
            ``{"asset_id": "...", "target": {"lat": ..., "lng": ...}, "status": "intercepting"}``
        """
        with self._lock:
            if asset_id not in self.assets:
                return {"error": f"asset not found: {asset_id}"}
            a = self.assets[asset_id]
            apos = a.get("position", a)
            alat, alng = apos.get("lat", 0), apos.get("lng", 0)
            self.waypoint_nav.set_route(asset_id, [
                {"lat": alat, "lng": alng, "label": "CURRENT"},
                {"lat": target_lat, "lng": target_lng, "label": "INTERCEPT"},
            ])
            self.alerts.append({
                "level": "INFO",
                "msg": (f"平台 {asset_id} 已重定向至目标 "
                        f"({target_lat:.2f}, {target_lng:.2f})"),
                "time": _now_iso(),
            })
            return {
                "asset_id": asset_id,
                "target": {"lat": target_lat, "lng": target_lng},
                "status": "intercepting",
            }

    def authorize_follow_asset(self, asset_id: str, track_id: str, *, authorized: bool) -> dict:
        """Authorize a scenario follow asset to launch against an eligible public track."""
        if not authorized:
            return {"error": "operator authorization is required"}
        asset_id = str(asset_id or "")
        track_id = str(track_id or "")
        with self._lock:
            asset = self.assets.get(asset_id)
            if not asset:
                return {"error": f"asset not found: {asset_id}"}
            task = next((
                row for row in self._scenario_asset_follow_tasks
                if str(row.get("asset_id") or "") == asset_id
            ), None)
            if not task:
                return {"error": f"follow task not found for asset: {asset_id}"}
            candidates = self._follow_task_candidates(task, asset)
            if track_id not in {
                str(track.get("id") or track.get("track_id") or "")
                for track in candidates
            }:
                return {"error": "target track is not eligible for follow launch"}
            asset["_follow_launch_authorized_track_id"] = track_id
            asset["_follow_pending_prompt"] = None
            self.events.append({
                "type": "operator_follow_launch_authorized",
                "asset_id": asset_id,
                "target_track_id": track_id,
                "timestamp": time.time(),
                "sim_time": round(float(self.clock.get("elapsed_sec", 0) or 0), 2),
            })
            self._update_asset_follow_tasks()
            return {
                "asset_id": asset_id,
                "track_id": track_id,
                "authorization": "operator_confirmed",
                "status": "authorized",
            }

    # ── Weapon engagement (F23 Attack Target) ──────────────────

    def _truth_target_for_track(self, track: object) -> str | None:
        """Resolve a public fused track to private truth without exposing the mapping."""
        associated = str(getattr(track, "associated_threat_id", "") or "")
        if associated in self.threats:
            return associated
        observation_ids = {
            str(ref.get("observation_id"))
            for ref in getattr(track, "source_refs", []) or []
            if isinstance(ref, dict) and ref.get("observation_id")
        }
        counts: dict[str, int] = {}
        associations = [
            *(getattr(self.sensor_fusion, "truth_association_history", []) or []),
            *(getattr(self.sensor_fusion, "truth_associations", []) or []),
        ]
        for item in associations:
            if not isinstance(item, dict) or str(item.get("observation_id") or "") not in observation_ids:
                continue
            truth_id = str(item.get("truth_id") or "")
            if truth_id in self.threats:
                counts[truth_id] = counts.get(truth_id, 0) + 1
        if not counts:
            return None
        return max(counts, key=counts.get)

    def _apply_confirmed_contact_behaviors(self) -> None:
        """Apply scenario reactions that depend on a confirmed backend classification."""
        for track in self.sensor_fusion.tracks.values():
            assessment = dict(getattr(track, "agent_assessment", {}) or {})
            if assessment.get("status") != "confirmed" or not assessment.get("source"):
                continue
            truth_id = self._truth_target_for_track(track)
            threat = self.threats.get(str(truth_id or ""))
            if not threat or threat.get("neutralized") or threat.get("_confirmed_behavior_applied"):
                continue
            reaction = dict(
                ((threat.get("_behavior_script") or {}).get("on_confirmed_classification")) or {}
            )
            allowed = {
                str(value).strip().upper()
                for value in reaction.get("classifications", [])
                if str(value).strip()
            }
            classification = str(getattr(track, "classification", "") or "").strip().upper()
            if not reaction or classification not in allowed:
                continue

            route = []
            route_lat = float(threat.get("lat", 0) or 0)
            route_lng = float(threat.get("lng", 0) or 0)
            for index, leg in enumerate(reaction.get("route_legs") or [], start=1):
                if not isinstance(leg, dict):
                    continue
                bearing = leg.get("bearing")
                distance_nm = leg.get("distance_nm")
                if bearing is None or distance_nm is None:
                    continue
                route_lat, route_lng = _advance_position(
                    route_lat, route_lng, float(bearing), max(0.0, float(distance_nm)),
                )
                route.append({
                    "lat": route_lat,
                    "lng": route_lng,
                    "label": str(leg.get("label") or f"离场航点 {index}"),
                })
            if route:
                threat["_flight_route"] = route
                threat["_commanded_heading"] = float(reaction["route_legs"][0]["bearing"])
                threat["_hold_when_route_complete"] = bool(reaction.get("hold_after_route"))
            elif reaction.get("heading") is not None:
                threat["_commanded_heading"] = float(reaction["heading"])
            if reaction.get("speed_kts") is not None:
                threat["speed_kts"] = float(reaction["speed_kts"])
            threat["_behavior_phase"] = None
            threat["_phase_elapsed"] = 0.0
            threat["_behavior_phase_name"] = str(reaction.get("label") or "状态已更新")
            threat["_confirmed_behavior_applied"] = True

            assessment.update({
                "behavior_state": str(reaction.get("state") or "updated"),
                "behavior_label": str(reaction.get("label") or "状态已更新"),
            })
            track.agent_assessment = assessment
            self.events.append({
                "type": "confirmed_contact_behavior_changed",
                "target_track_id": track.id,
                "contact_label": self._operator_label_for_track(track.id),
                "behavior_state": assessment["behavior_state"],
                "behavior_label": assessment["behavior_label"],
                "sim_time": round(float(self.clock.get("elapsed_sec", 0.0) or 0.0), 2),
                "timestamp": time.time(),
            })

    def _confirm_pending_damage_assessments(self) -> None:
        """Promote an impact to a confirmed kill after post-impact sensing."""
        now = float(self.clock.get("elapsed_sec", 0.0) or 0.0)
        bda_not_before = max(
            0.0,
            float((self._engagement_policy or {}).get("bda_not_before_sec", 0) or 0),
        )
        if now < bda_not_before:
            return
        observations = {
            str(item.get("observation_id") or ""): item
            for item in (self.sensor_fusion.last_observation_batch.get("observations") or [])
            if isinstance(item, dict)
        }
        visual_sensors = {"EO/IR", "IR", "SAR"}
        allowed_observers = {
            str(value) for value in (self._engagement_policy or {}).get("bda_observer_asset_ids") or []
            if value
        }
        for weapon in self.weapons.values():
            if weapon.get("status") != "hit" or weapon.get("damage_state") != "impact_pending":
                continue
            impact_time = float(weapon.get("impact_sim_time", now) or now)
            if now - impact_time < 120.0:
                continue
            truth_id = str(weapon.get("target_threat_id") or "")
            observer_id = None
            for association in self.sensor_fusion.truth_associations or []:
                if str(association.get("truth_id") or "") != truth_id:
                    continue
                observation = observations.get(str(association.get("observation_id") or "")) or {}
                if allowed_observers and str(observation.get("asset_id") or "") not in allowed_observers:
                    continue
                if str(observation.get("sensor_id") or "").upper() in visual_sensors:
                    observer_id = str(observation.get("asset_id") or "") or None
                    break
            # The fusion batch can legitimately omit a low-rate visual frame
            # even when the confirmation UAV is already over the impact site.
            # Treat a platform's real, current EO/IR/SAR geometry as that
            # post-impact observation rather than promoting a kill on time
            # alone.  This keeps assessment causal without making it depend
            # on an unrelated radar/AIS batch cadence.
            if not observer_id:
                observer_id = self._post_impact_visual_observer(truth_id)
            if not observer_id:
                continue

            target = self.threats.get(truth_id)
            if not target:
                continue
            if target.get("neutralized"):
                weapon["damage_state"] = "destroyed"
                weapon["assessed_sim_time"] = round(now, 2)
                continue
            related_impacts = [
                item for item in self.weapons.values()
                if item.get("status") == "hit"
                and str(item.get("target_threat_id") or "") == truth_id
                and item.get("damage_state") == "impact_pending"
            ]
            assessed_damage = (
                "destroyed"
                if any(item.get("predicted_damage_state") == "destroyed" for item in related_impacts)
                else "damaged"
            )
            self._apply_damage(target, assessed_damage)
            for related in related_impacts:
                related["damage_state"] = assessed_damage
                related["assessed_sim_time"] = round(now, 2)
            target_track_id = str(weapon.get("target_track_id") or "")
            track = self.sensor_fusion.tracks.get(target_track_id)
            if track is not None:
                track.retain_until_sim_time = max(
                    float(getattr(track, "retain_until_sim_time", 0.0) or 0.0),
                    float((self.scenario_story.get("demo_controls") or {}).get("duration_sec", 0.0) or 0.0),
                )
                assessment = dict(getattr(track, "agent_assessment", {}) or {})
                assessment.update({
                    "damage_state": assessed_damage,
                    "engagement_status": assessed_damage,
                    "behavior_label": (
                        "已击毁，威胁解除"
                        if assessed_damage == "destroyed"
                        else "目标受损，需重新规划"
                    ),
                    "assessment_observer": observer_id,
                })
                track.agent_assessment = assessment
            self._return_follow_assets_after_strike(target_track_id)
            post_bda_routes = (self._engagement_policy or {}).get("post_bda_routes") or {}
            post_bda_behaviors = (self._engagement_policy or {}).get("post_bda_behaviors") or {}
            rerouted_assets = []
            if isinstance(post_bda_routes, dict):
                for route_asset_id, route in post_bda_routes.items():
                    route_asset_id = str(route_asset_id or "")
                    asset = self.assets.get(route_asset_id)
                    if asset is None or not isinstance(route, list) or not route:
                        continue
                    waypoints = [dict(point) for point in route if isinstance(point, dict)]
                    if not waypoints:
                        continue
                    self.waypoint_nav.set_route(route_asset_id, waypoints, mode="hold")
                    asset["status"] = "active"
                    behavior = (
                        post_bda_behaviors.get(route_asset_id)
                        if isinstance(post_bda_behaviors, dict) else None
                    ) or {}
                    asset["_current_behavior"] = str(behavior.get("behavior") or "post_bda_return")
                    asset["_current_behavior_label"] = str(behavior.get("label") or "毁伤评估后返航/撤离")
                    asset["_behavior_phase_started_at"] = now
                    asset["_cruise_speed_kts"] = float(
                        behavior.get("speed_kts", asset.get("_cruise_speed_kts", 1)) or 1
                    )
                    asset["speed_kts"] = max(
                        1.0,
                        float(asset.get("_cruise_speed_kts", 1) or 1),
                    )
                    rerouted_assets.append(route_asset_id)
            self.events.append({
                "type": "damage_assessment_confirmed",
                "weapon_id": weapon.get("id"),
                "target_track_id": target_track_id,
                "damage_state": assessed_damage,
                "observer_asset_id": observer_id,
                "position": {"lat": weapon.get("lat"), "lng": weapon.get("lng")},
                "sim_time": round(now, 2),
                "timestamp": time.time(),
            })
            if rerouted_assets:
                self.events.append({
                    "type": "post_bda_return_started",
                    "asset_ids": rerouted_assets,
                    "target_track_id": target_track_id,
                    "sim_time": round(now, 2),
                    "timestamp": time.time(),
                })

    def _post_impact_visual_observer(self, truth_id: str) -> str | None:
        """Return an active visual platform currently able to assess an impact.

        This is deliberately a geometry check rather than a timer fallback:
        the confirmation only happens after the assessment delay *and* a
        configured EO/IR or SAR platform has actually reached visual range.
        Prefer the dedicated confirmation UAV when it is available.
        """
        target = self.threats.get(str(truth_id or ""))
        if not target:
            return None
        candidates = sorted(
            self.assets,
            key=lambda asset_id: (asset_id != "UAV-CONFIRM-01", asset_id),
        )
        allowed_observers = {
            str(value) for value in (self._engagement_policy or {}).get("bda_observer_asset_ids") or []
            if value
        }
        for asset_id in candidates:
            if allowed_observers and asset_id not in allowed_observers:
                continue
            asset = self.assets[asset_id]
            if asset.get("status") not in {"operational", "active", "holding"}:
                continue
            sensors = {
                str(sensor).strip().upper()
                for sensor in asset.get("sensors") or []
            }
            if not sensors.intersection({"EO/IR", "IR", "SAR"}):
                continue
            pos = asset.get("position") or asset
            distance_nm = self.waypoint_nav._haversine(
                float(pos.get("lat", 0) or 0),
                float(pos.get("lng", 0) or 0),
                float(target.get("lat", 0) or 0),
                float(target.get("lng", 0) or 0),
            )
            # EO/IR confirmation is intentionally conservative.  SAR-only
            # platforms can assess at a slightly longer, still local range.
            visual_range_nm = 12.0 if sensors.intersection({"EO/IR", "IR"}) else 20.0
            if distance_nm <= visual_range_nm:
                return asset_id
        return None

    def _prune_track_motion_histories(self) -> None:
        """Keep terminal map trails local to the current tactical picture."""
        cutoff = float(self.clock.get("elapsed_sec", 0.0) or 0.0) - 900.0
        for track in self.sensor_fusion.tracks.values():
            history = list(getattr(track, "history_path", []) or [])
            if not history:
                continue
            retained = [
                point for point in history[-120:]
                if float(point.get("sim_time", 0) or 0) >= cutoff
            ]
            # Static, retained contacts (such as a destroyed target) still
            # need a final position, but no stale route should be rendered.
            track.history_path = retained or [history[-1]]

    def _operator_label_for_track(self, track_id: str) -> str:
        """Return the run-stable public label assigned to a fused track."""
        label = self._operator_contact_labels.get(track_id)
        if label:
            return label
        track = self.sensor_fusion.tracks.get(track_id)
        domain = str(getattr(track, "domain_hint", "") or "").casefold()
        prefix = "空中接触" if domain == "air" else ("地面接触" if domain == "ground" else "海面接触")
        same_domain_count = sum(
            1 for value in self._operator_contact_labels.values()
            if str(value).startswith(prefix)
        )
        label = f"{prefix} {same_domain_count + 1:02d}"
        self._operator_contact_labels[track_id] = label
        return label

    def _operator_contact_for_threat(self, threat_id: str) -> tuple[str, str | None]:
        """Resolve private truth to the stable operator contact label."""
        classification_labels = {
            "FAST_ATTACK_CRAFT": "高速攻击艇",
            "MISSILE_BOAT": "导弹艇",
            "SURFACE_COMBATANT": "水面作战舰艇",
            "FISHING_VESSEL": "民用渔船",
            "FISHING": "民用渔船",
            "CIVILIAN": "民用船只",
            "MERCHANT": "商船",
            "MERCHANT_VESSEL": "商船",
            "COASTAL_MISSILE_SITE": "沿海导弹阵地",
            "MISSILE_SITE": "导弹阵地",
            "MISSILE_BATTERY": "导弹阵地",
        }
        for index, track in enumerate(self.sensor_fusion.tracks.values(), start=1):
            if self._truth_target_for_track(track) != threat_id:
                continue
            track_id = str(getattr(track, "id", "") or "")
            label = self._operator_label_for_track(track_id) if track_id else f"待识别接触 {index:02d}"
            classification = str(getattr(track, "classification", "UNKNOWN") or "UNKNOWN").upper()
            if classification in classification_labels:
                label += f" · {classification_labels[classification]}"
            return label, track_id or None
        return "已授权目标", None

    def engagement_eligibility(
        self,
        track_id: str,
        *,
        asset_id: str,
        weapon_name: str,
    ) -> dict:
        """Evaluate identification, ROE and platform gates for a fire command."""
        track = self.sensor_fusion.tracks.get(track_id)
        if track is None:
            return {"eligible": False, "reason": f"track not found: {track_id}"}
        assessment = getattr(track, "agent_assessment", {}) or {}
        if not assessment.get("source") or assessment.get("status") != "confirmed":
            return {"eligible": False, "reason": "目标尚未完成后端敌方识别"}
        policy = self._engagement_policy or {}
        classification = str(getattr(track, "classification", "UNKNOWN") or "UNKNOWN").upper()
        protected = [str(value).upper() for value in policy.get("protected_classifications") or []]
        if classification == "UNKNOWN" or any(value in classification for value in protected):
            return {"eligible": False, "reason": "目标身份未知或属于民用禁射类别"}
        threat_level = str(getattr(track, "threat_level", "UNKNOWN") or "UNKNOWN").upper()
        minimum_levels = {
            str(value).upper()
            for value in policy.get("minimum_threat_levels") or ["HIGH", "CRITICAL"]
        }
        if threat_level not in minimum_levels:
            return {"eligible": False, "reason": "目标威胁等级未达到交战门槛"}
        eligible_phases = {
            str(value).upper()
            for value in policy.get("eligible_kill_chain_phases") or ["TARGET", "ENGAGE"]
        }
        if str(getattr(track, "kill_chain_phase", "") or "").upper() not in eligible_phases:
            return {"eligible": False, "reason": "目标尚未进入 TARGET 阶段"}
        if asset_id not in set(policy.get("authorized_asset_ids") or [asset_id]):
            return {"eligible": False, "reason": "发射平台不在授权清单"}
        if weapon_name not in set(policy.get("authorized_weapons") or [weapon_name]):
            return {"eligible": False, "reason": "武器不在授权清单"}
        truth_target_id = self._truth_target_for_track(track)
        if not truth_target_id:
            return {"eligible": False, "reason": "航迹无法与当前目标稳定关联"}
        target_engagements = (
            policy.get("target_engagements")
            if isinstance(policy.get("target_engagements"), dict)
            else {}
        )
        assignment = target_engagements.get(str(truth_target_id))
        if isinstance(assignment, dict):
            if str(assignment.get("asset_id") or "") != str(asset_id) or str(
                assignment.get("weapon_name") or ""
            ) != str(weapon_name):
                return {"eligible": False, "reason": "该目标必须使用已分配的专用火力单元"}
            elapsed = float(self.clock.get("elapsed_sec", 0) or 0)
            not_before = max(0.0, float(assignment.get("not_before_sec", 0) or 0))
            if elapsed < not_before:
                return {
                    "eligible": False,
                    "reason": f"当前波次尚未开放，需等待至 T+{int(not_before)} 秒",
                }
            required_targets = {
                str(value) for value in assignment.get("requires_completed_target_ids") or []
            }
            if required_targets:
                completed_targets: set[str] = set()
                for event in self.events:
                    if event.get("type") != "authorized_fire_command":
                        continue
                    completed_track_id = str(event.get("target_track_id") or "")
                    completed_track = self.sensor_fusion.tracks.get(completed_track_id)
                    completed_target = self._truth_target_for_track(completed_track)
                    if completed_target:
                        completed_targets.add(str(completed_target))
                if required_targets - completed_targets:
                    return {"eligible": False, "reason": "必须先完成第一波全部固定目标攻击"}
            if assignment.get("requires_damage_assessment") and not any(
                event.get("type") == "damage_assessment_confirmed"
                for event in self.events
            ):
                return {"eligible": False, "reason": "必须先完成第一波攻击毁伤评估"}
        if truth_target_id in set(policy.get("protected_truth_ids") or []):
            return {"eligible": False, "reason": "目标命中任务禁射保护规则"}
        threat = self.threats.get(truth_target_id) or {}
        buffer_nm = max(0.0, float(policy.get("protected_asset_buffer_nm", 0) or 0))
        for protected_asset in self._scenario_protected_assets:
            protected_lat = protected_asset.get("lat")
            protected_lng = protected_asset.get("lng", protected_asset.get("lon"))
            if protected_lat is None or protected_lng is None:
                continue
            radius_nm = max(
                0.0,
                float(protected_asset.get("protection_radius_m", 0) or 0) / 1852.0,
            )
            separation_nm = self.waypoint_nav._haversine(
                float(threat.get("lat", 0)), float(threat.get("lng", 0)),
                float(protected_lat), float(protected_lng),
            )
            if separation_nm < radius_nm + buffer_nm:
                return {"eligible": False, "reason": "目标距民用保护区安全边界不足"}
        return {
            "eligible": True,
            "track_id": track_id,
            "asset_id": asset_id,
            "weapon_name": weapon_name,
            "classification": classification,
            "threat_level": threat_level,
            "_truth_target_id": truth_target_id,
        }

    def fire_weapon_at_track(
        self,
        track_id: str,
        *,
        asset_id: str,
        weapon_name: str,
        authorized: bool,
    ) -> dict:
        """Execute an explicitly authorized fire command against an eligible track."""
        if not authorized:
            return {"error": "攻击命令缺少操作员明确授权"}
        with self._lock:
            eligibility = self.engagement_eligibility(
                track_id,
                asset_id=asset_id,
                weapon_name=weapon_name,
            )
            if not eligibility.get("eligible"):
                return {"error": eligibility.get("reason", "目标不满足交战条件")}
            policy = self._engagement_policy or {}
            maximum_salvos = max(1, int(policy.get("max_salvos_per_track", 1) or 1))
            prior_salvos = sum(
                event.get("type") == "authorized_fire_command"
                and str(event.get("target_track_id") or "") == str(track_id)
                for event in self.events
            )
            if prior_salvos >= maximum_salvos:
                return {"error": "该航迹已执行授权齐射，禁止重复发射"}
            if policy.get("requires_prior_warning"):
                warning = self._engagement_warnings.get(track_id)
                if not warning:
                    return {"error": "必须先向目标发出警告"}
                not_before = float(warning.get("fire_not_before_sec", 0) or 0)
                elapsed = float(self.clock.get("elapsed_sec", 0) or 0)
                if elapsed < not_before:
                    remaining = max(1, int(math.ceil(not_before - elapsed)))
                    return {"error": f"警告观察期尚未结束，还需等待 {remaining} 个仿真秒"}
            truth_target_id = str(eligibility["_truth_target_id"])
            target_engagements = (
                policy.get("target_engagements")
                if isinstance(policy.get("target_engagements"), dict)
                else {}
            )
            target_assignment = target_engagements.get(truth_target_id)
            target_assignment = (
                dict(target_assignment) if isinstance(target_assignment, dict) else None
            )
            coordinated = target_assignment or policy.get("coordinated_engagement")
            if target_assignment and target_assignment.get("authorize_wave_as_group"):
                selected_wave = int(target_assignment.get("wave", 0) or 0)
                wave_members = []
                for member_target_id, raw_member in target_engagements.items():
                    if not isinstance(raw_member, dict) or int(raw_member.get("wave", 0) or 0) != selected_wave:
                        continue
                    member_track_id = next(
                        (
                            candidate.id
                            for candidate in self.sensor_fusion.tracks.values()
                            if self._truth_target_for_track(candidate) == str(member_target_id)
                        ),
                        "",
                    )
                    member = dict(raw_member)
                    member["_truth_target_id"] = str(member_target_id)
                    member["_target_track_id"] = str(member_track_id)
                    wave_members.append(member)
                wave_members.sort(
                    key=lambda item: 0 if item.get("_truth_target_id") == truth_target_id else 1
                )
                coordinated = {
                    "chain_id": str(target_assignment.get("chain_id") or f"wave-{selected_wave}"),
                    "coordination_mode": str(target_assignment.get("coordination_mode") or "time_on_target"),
                    "arrival_tolerance_sec": float(target_assignment.get("arrival_tolerance_sec", 30) or 30),
                    "max_launch_stagger_sec": float(target_assignment.get("max_launch_stagger_sec", 180) or 180),
                    "participants": wave_members,
                    "multi_target": True,
                    "wave": selected_wave,
                }
            participants = [
                dict(item) for item in (coordinated or {}).get("participants") or []
                if isinstance(item, dict)
            ] if isinstance(coordinated, dict) else []
            if target_assignment and not participants:
                participants = [target_assignment]
            if participants and (
                str(participants[0].get("asset_id") or "") != asset_id
                or str(participants[0].get("weapon_name") or "") != weapon_name
            ):
                return {"error": "协同武器链必须由配置的主攻击平台发起"}
            launch_plan = participants or [{"asset_id": asset_id, "weapon_name": weapon_name, "role": "single"}]
            for participant in launch_plan:
                participant_asset = str(participant.get("asset_id") or "")
                participant_weapon = str(participant.get("weapon_name") or "")
                participant_track_id = str(participant.get("_target_track_id") or track_id)
                participant_truth_target_id = str(
                    participant.get("_truth_target_id") or truth_target_id
                )
                if not participant_track_id:
                    return {"error": f"协同节点 {participant_asset} 缺少当前目标航迹"}
                participant_eligibility = self.engagement_eligibility(
                    participant_track_id,
                    asset_id=participant_asset,
                    weapon_name=participant_weapon,
                )
                if not participant_eligibility.get("eligible"):
                    return {
                        "error": (
                            f"协同节点 {participant_asset} 不满足交战条件："
                            f"{participant_eligibility.get('reason', 'unknown')}"
                        )
                    }
                preflight_error = self._weapon_launch_preflight(
                    participant_asset,
                    participant_truth_target_id,
                    participant_weapon,
                )
                if preflight_error:
                    return {"error": f"协同节点 {participant_asset} 发射前检查失败：{preflight_error}"}

            launch_delays: dict[str, float] = {}
            if isinstance(coordinated, dict) and coordinated.get("coordination_mode") == "time_on_target":
                from amos_platform.data.scenario_repository import get_weapon_spec

                target = self.threats[truth_target_id]
                arrival_times = []
                for participant in launch_plan:
                    participant_asset = self.assets[str(participant.get("asset_id") or "")]
                    participant_position = participant_asset.get("position", participant_asset)
                    participant_spec = get_weapon_spec(str(participant.get("weapon_name") or ""))
                    if participant_spec is None:
                        return {"error": "协同武器链存在未知武器规格"}
                    participant_target = self.threats[str(
                        participant.get("_truth_target_id") or truth_target_id
                    )]
                    distance_nm = self.waypoint_nav._haversine(
                        float(participant_position.get("lat", 0)),
                        float(participant_position.get("lng", 0)),
                        float(participant_target.get("lat", 0)),
                        float(participant_target.get("lng", 0)),
                    )
                    arrival_times.append(distance_nm / participant_spec.speed_kts * 3600.0)
                longest_flight = max(arrival_times) if arrival_times else 0.0
                maximum_stagger = max(
                    0.0, float(coordinated.get("max_launch_stagger_sec", 90) or 90),
                )
                if arrival_times and longest_flight - min(arrival_times) > maximum_stagger:
                    return {"error": "协同武器所需发射间隔超限，需重新规划攻击阵位"}
                launch_delays = {
                    str(participant.get("asset_id") or ""): longest_flight - flight_time
                    for participant, flight_time in zip(launch_plan, arrival_times)
                }

            launch_results = []
            for participant in launch_plan:
                participant_asset = str(participant.get("asset_id") or "")
                participant_weapon = str(participant.get("weapon_name") or "")
                participant_truth_target_id = str(
                    participant.get("_truth_target_id") or truth_target_id
                )
                participant_track_id = str(participant.get("_target_track_id") or track_id)
                launched = self.fire_weapon(
                    participant_asset, participant_truth_target_id, participant_weapon
                )
                if launched.get("error"):
                    return launched
                weapon = self.weapons.get(str(launched.get("weapon_id") or ""))
                if weapon is not None:
                    launch_delay = max(0.0, launch_delays.get(participant_asset, 0.0))
                    weapon["flight_time_sec"] = float(weapon.get("eta_sec", 0) or 0)
                    weapon["planned_launch_delay_sec"] = round(launch_delay, 1)
                    weapon["planned_time_on_target_sec"] = round(
                        float(self.clock.get("elapsed_sec", 0) or 0)
                        + launch_delay + float(weapon.get("flight_time_sec", 0) or 0),
                        1,
                    )
                    if launch_delay > 0.05:
                        weapon["status"] = "scheduled"
                        weapon["scheduled_launch_time"] = (
                            float(self.clock.get("elapsed_sec", 0) or 0) + launch_delay
                        )
                        weapon["eta_sec"] = round(
                            launch_delay + float(weapon.get("flight_time_sec", 0) or 0), 1,
                        )
                    weapon["target_track_id"] = participant_track_id
                    weapon["coordination_chain_id"] = (
                        str((coordinated or {}).get("chain_id") or "") or None
                    )
                    weapon["coordination_role"] = str(participant.get("role") or "member")
                    weapon["expected_chain_members"] = len(launch_plan)
                    weapon["arrival_tolerance_sec"] = float(
                        (coordinated or {}).get("arrival_tolerance_sec", 20) or 20
                    )
                    if participant.get("expend_source_asset"):
                        weapon["source_asset_disposition"] = "expended_on_terminal_commit"
                        source_asset = self.assets.get(participant_asset)
                        if source_asset is not None:
                            source_asset["status"] = "expended"
                            source_asset["speed_kts"] = 0.0
                            source_asset["_current_behavior"] = "terminal_attack_committed"
                            source_asset["_current_behavior_label"] = "已转为一次性武器并执行末端攻击"
                            source_asset["_operator_hidden_after_launch"] = True
                            self.waypoint_nav.set_route(participant_asset, [], mode="hold")
                            self.events.append({
                                "type": "one_way_asset_committed",
                                "asset_id": participant_asset,
                                "weapon_id": weapon.get("id"),
                                "target_track_id": participant_track_id,
                                "sim_time": round(float(self.clock.get("elapsed_sec", 0) or 0), 2),
                                "timestamp": time.time(),
                            })
                launch_results.append({
                    **{key: value for key, value in launched.items() if key != "threat_id"},
                    "asset_id": participant_asset,
                    "weapon_name": participant_weapon,
                    "coordination_role": str(participant.get("role") or "member"),
                    "target_track_id": participant_track_id,
                    "planned_launch_delay_sec": round(
                        max(0.0, launch_delays.get(participant_asset, 0.0)), 1,
                    ),
                })
            result = launch_results[0]
            for launch_result in launch_results:
                engaged_track_id = str(launch_result.get("target_track_id") or track_id)
                engaged_track = self.sensor_fusion.tracks.get(engaged_track_id)
                if engaged_track is not None:
                    engaged_track.kill_chain_phase = "ENGAGE"
                    engaged_track.kill_chain_times["ENGAGE"] = time.time()
            event_base = {
                "command_source": "operator",
                "asset_id": asset_id,
                "asset_ids": [item["asset_id"] for item in launch_results],
                "weapon_id": result.get("weapon_id"),
                "weapon_ids": [item.get("weapon_id") for item in launch_results],
                "coordination_chain_id": str((coordinated or {}).get("chain_id") or "") or None,
                "sim_time": round(float(self.clock.get("elapsed_sec", 0) or 0), 2),
                "timestamp": time.time(),
            }
            if (coordinated or {}).get("multi_target"):
                for launch_result in launch_results:
                    self.events.append({
                        **event_base,
                        "type": "authorized_fire_command",
                        "target_track_id": launch_result.get("target_track_id"),
                    })
                self.events.append({
                    **event_base,
                    "type": "authorized_strike_wave",
                    "wave": (coordinated or {}).get("wave"),
                    "target_track_ids": [
                        item.get("target_track_id") for item in launch_results
                    ],
                })
            else:
                self.events.append({
                    **event_base,
                    "type": "authorized_fire_command",
                    "target_track_id": track_id,
                })
            post_launch_routes = policy.get("post_launch_routes") or {}
            post_launch_behaviors = policy.get("post_launch_behaviors") or {}
            rerouted_assets = []
            target_specific_assets = (
                {item["asset_id"] for item in launch_results}
                if target_assignment else None
            )
            if isinstance(post_launch_routes, dict):
                for route_asset_id, route in post_launch_routes.items():
                    route_asset_id = str(route_asset_id or "")
                    if target_specific_assets is not None and route_asset_id not in target_specific_assets:
                        continue
                    asset = self.assets.get(route_asset_id)
                    if asset is None or not isinstance(route, list) or not route:
                        continue
                    waypoints = [dict(point) for point in route if isinstance(point, dict)]
                    if not waypoints:
                        continue
                    self.waypoint_nav.set_route(route_asset_id, waypoints, mode="hold")
                    asset["status"] = "active"
                    behavior = (
                        post_launch_behaviors.get(route_asset_id)
                        if isinstance(post_launch_behaviors, dict) else None
                    ) or {}
                    asset["_current_behavior"] = str(behavior.get("behavior") or "post_launch_egress")
                    asset["_current_behavior_label"] = str(behavior.get("label") or "发射后脱离")
                    asset["_behavior_phase_started_at"] = float(self.clock.get("elapsed_sec", 0) or 0)
                    asset["_cruise_speed_kts"] = float(
                        behavior.get("speed_kts", asset.get("_cruise_speed_kts", 1)) or 1
                    )
                    asset["speed_kts"] = max(
                        1.0,
                        float(asset.get("_cruise_speed_kts", 1) or 1),
                    )
                    rerouted_assets.append(route_asset_id)
            if rerouted_assets:
                self.events.append({
                    "type": "post_launch_egress_started",
                    "asset_ids": rerouted_assets,
                    "target_track_id": track_id,
                    "sim_time": round(float(self.clock.get("elapsed_sec", 0) or 0), 2),
                    "timestamp": time.time(),
                })
            # In the maritime demo the operator confirms one engagement
            # action.  The post-strike assessment UAV is an execution support
            # asset for that same authorized action, not a separate weapon
            # release decision.  Mark configured follow/assessment assets as
            # authorized for this track so the ASSESS evidence window can be
            # satisfied after the confirmed fire command.
            for task in self._scenario_asset_follow_tasks:
                if not task.get("requires_operator_authorization"):
                    continue
                follow_asset = self.assets.get(str(task.get("asset_id") or ""))
                if follow_asset is None:
                    continue
                follow_asset["_follow_launch_authorized_track_id"] = track_id
                follow_asset["_follow_pending_prompt"] = None
            return {
                key: value
                for key, value in {
                    **result,
                    "track_id": track_id,
                    "authorization": "operator_confirmed",
                    "coordinated": len(participants) >= 2,
                    "coordination_chain_id": str((coordinated or {}).get("chain_id") or "") or None,
                    "weapon_ids": [item.get("weapon_id") for item in launch_results],
                    "participants": launch_results,
                }.items()
                if key != "threat_id"
            }

    def issue_warning_at_track(self, track_id: str, *, authorized: bool) -> dict:
        """Record one operator-authorized warning before the fire gate opens."""
        if not authorized:
            return {"error": "警告命令缺少操作员明确授权"}
        with self._lock:
            policy = self._engagement_policy or {}
            asset_ids = list(policy.get("authorized_asset_ids") or [])
            weapon_names = list(policy.get("authorized_weapons") or [])
            if not asset_ids or not weapon_names:
                return {"error": "当前任务未配置警告目标校验规则"}
            eligibility = self.engagement_eligibility(
                track_id,
                asset_id=str(asset_ids[0]),
                weapon_name=str(weapon_names[0]),
            )
            if not eligibility.get("eligible"):
                return {"error": eligibility.get("reason", "目标不满足警告条件")}
            existing = self._engagement_warnings.get(track_id)
            if existing:
                return dict(existing)
            issued_at = float(self.clock.get("elapsed_sec", 0) or 0)
            delay = max(0.0, float(policy.get("warning_delay_sec", 300) or 300))
            warning = {
                "track_id": track_id,
                "status": "issued",
                "issued_at_sec": round(issued_at, 3),
                "fire_not_before_sec": round(issued_at + delay, 3),
                "delay_sec": delay,
                "authorization": "operator_confirmed",
            }
            track = self.sensor_fusion.tracks.get(track_id)
            if track is not None:
                # Keep the already-confirmed track available while the operator
                # reads and answers the second dialog; this does not fabricate
                # a new sensor observation or alter its last-observed time.
                track.retain_until_sim_time = float(warning["fire_not_before_sec"]) + 300.0
            self._engagement_warnings[track_id] = warning
            self.events.append({
                "type": "target_warning_issued",
                "command_source": "operator",
                **warning,
                "timestamp": time.time(),
            })
            target_label, _ = self._operator_contact_for_threat(
                str(eligibility["_truth_target_id"])
            )
            self.alerts.append({
                "level": "WARNING",
                "msg": (
                    f"已向“{target_label}”发出无线电警告，"
                    f"继续观察 {int(delay)} 个仿真秒"
                ),
                "time": _now_iso(),
            })
            return dict(warning)

    def _weapon_launch_preflight(self, asset_id: str, threat_id: str, weapon_name: str) -> str | None:
        """Return a launch error without mutating ammunition or weapon state."""
        from amos_platform.data.scenario_repository import get_weapon_spec

        if asset_id not in self.assets:
            return f"asset not found: {asset_id}"
        if threat_id not in self.threats:
            return f"threat not found: {threat_id}"
        asset = self.assets[asset_id]
        threat = self.threats[threat_id]
        if str(asset.get("status") or "").casefold() not in {"active", "operational", "holding"}:
            return f"asset {asset_id} is not available for weapon release"
        if threat.get("neutralized"):
            return f"threat already neutralized: {threat_id}"
        spec = get_weapon_spec(weapon_name)
        if spec is None:
            return f"unknown weapon: {weapon_name}"
        asset_weapons = asset.get("weapons", [])
        if weapon_name not in asset_weapons and not any(
            spec.weapon_id in carried or weapon_name in carried for carried in asset_weapons
        ):
            return f"asset {asset_id} does not carry {weapon_name}"
        ammo = asset.get("_ammo") if isinstance(asset.get("_ammo"), dict) else {}
        if weapon_name in ammo and int(ammo.get(weapon_name, 0) or 0) <= 0:
            return f"asset {asset_id} has no remaining {weapon_name}"
        apos = asset.get("position", asset)
        dist_nm = self.waypoint_nav._haversine(
            apos.get("lat", 0), apos.get("lng", 0), threat["lat"], threat["lng"]
        )
        if dist_nm > spec.range_nm * 1.1:
            return f"target out of range: {dist_nm:.1f} NM > {spec.range_nm} NM"
        return None

    def fire_weapon(self, asset_id: str, threat_id: str,
                    weapon_name: str) -> dict:
        """Launch a weapon from an asset toward a threat.

        Looks up weapon specs from WEAPON_CATALOG.  Creates a weapon entity
        and deducts ammo from the asset.

        Args:
            asset_id:   Launching asset.
            threat_id:  Target threat.
            weapon_name: Weapon name as listed in AssetSnapshot.weapons
                         (e.g. ``"PL-15 超视距"``).

        Returns:
            ``{"weapon_id": "...", "status": "launched", ...}``
        """
        from amos_platform.data.scenario_repository import get_weapon_spec

        with self._lock:
            preflight_error = self._weapon_launch_preflight(asset_id, threat_id, weapon_name)
            if preflight_error:
                return {"error": preflight_error}

            asset = self.assets[asset_id]
            threat = self.threats[threat_id]

            spec = get_weapon_spec(weapon_name)
            assert spec is not None
            apos = asset.get("position", asset)
            alat, alng = apos.get("lat", 0), apos.get("lng", 0)
            tlat, tlng = threat["lat"], threat["lng"]
            dist_nm = self.waypoint_nav._haversine(alat, alng, tlat, tlng)
            # Create weapon entity
            wid = f"WPN-{self._rng.getrandbits(24):06x}"
            heading = math.degrees(math.atan2(tlng - alng, tlat - alat)) % 360
            eta = dist_nm / spec.speed_kts * 3600.0

            self.weapons[wid] = {
                "id": wid,
                "type": spec.weapon_id,
                "weapon_type": spec.weapon_type,
                "category": spec.category,
                "domain": "air" if spec.category in ("anti-air", "anti-radiation") else "air",
                "lat": alat,
                "lng": alng,
                "heading": heading,
                "speed_kts": spec.speed_kts,
                "source_asset_id": asset_id,
                "target_threat_id": threat_id,
                "launch_time": self.clock["elapsed_sec"],
                "eta_sec": round(eta, 1),
                "p_kill": spec.p_kill,
                "warhead": spec.warhead,
                "guidance": spec.guidance,
                "status": "in_flight",
            }

            # Deduct ammo from asset
            ammo = asset.setdefault("_ammo", {})
            ammo[weapon_name] = max(0, int(ammo.get(weapon_name, 2) or 0) - 1)

            self.alerts.append({
                "level": "INFO",
                "msg": (f"武器发射: {asset_id} → {spec.weapon_type} → "
                        f"{self._operator_contact_for_threat(threat_id)[0]} (ETA {eta:.0f}s)"),
                "time": _now_iso(),
                "type": "weapon_launched",
            })

            return {
                "weapon_id": wid,
                "weapon_type": spec.weapon_type,
                "asset_id": asset_id,
                "threat_id": threat_id,
                "distance_nm": round(dist_nm, 1),
                "eta_sec": round(eta, 1),
                "status": "launched",
            }

    # ── Threat injection + spawn (manual / scripted) ──────────

    def inject_threat(self, injection: dict) -> dict:
        """Runtime threat injection — create a new entity mid-simulation.

        Accepts the ThreatInjection format (see docs).  Supports:
          - Standalone threats (position, kinematics, features)
          - Behavior-scripted threats (multi-phase evolution)
          - Parent-child relationships (injection_info carried to children)

        Args:
            injection: ThreatInjection dict.

        Returns:
            ``{"threat_id": "...", "status": "injected", ...}``
        """
        with self._lock:
            child_ids = []
            for target in injection.get("targets", []):
                tid = target["target_id"]
                init = target.get("initial_state", {})
                pos = init.get("position", {})
                assess = target.get("initial_assessment", {})
                rf = target.get("rf_signature", {})
                script = target.get("behavior_script")

                self.threats[tid] = {
                    "id": tid,
                    "type": target.get("target_type", "unknown"),
                    "domain": target.get("domain", "maritime"),
                    "lat": pos.get("lat", 0),
                    "lng": pos.get("lng", 0),
                    "heading": init.get("heading", 0),
                    "speed_kts": init.get("speed_kts", 0),
                    "risk_level": assess.get("risk_level", "UNKNOWN"),
                    "neutralized": False,
                    "detected_by": [],
                    "first_detected": None,
                    "rf_freq_mhz": rf.get("rf_freq_mhz"),
                    "power_dbm": rf.get("power_dbm"),
                    "rcs_dbsm": assess.get("rcs_dbsm"),
                    "ir_signature": assess.get("ir_signature", "medium"),
                    "iff_status": assess.get("iff_status", "unknown"),
                    "ais_match": assess.get("ais_match", False),
                    "damage_state": "intact",
                    "hit_count": 0,
                    "injection_info": injection.get("injection", {}),
                    "_behavior_script": script,
                    "_behavior_phase": 0 if script else None,
                    "_behavior_phase_name": script["phases"][0]["name"] if script else None,
                    "_phase_elapsed": 0.0,
                }

                # Apply phase-0 kinematics from behavior script
                if script and script.get("phases"):
                    p0 = script["phases"][0]
                    self.threats[tid]["heading"] = p0.get("heading", self.threats[tid]["heading"])
                    self.threats[tid]["speed_kts"] = p0.get("speed_kts", self.threats[tid]["speed_kts"])

                # Register mesh node
                self.mesh.register_node(tid, pos.get("lat", 0), pos.get("lng", 0),
                                       node_type="threat")

                self.alerts.append({
                    "level": "INFO",
                    "msg": (f"手动注入: {tid} ({target.get('target_type','?')}) "
                            f"域={target.get('domain','?')} "
                            f"风险={assess.get('risk_level','UNKNOWN')}"),
                    "time": _now_iso(),
                    "type": "threat_injected",
                })

            return {
                "threat_ids": [t["target_id"] for t in injection.get("targets", [])],
                "child_ids": child_ids,
                "status": "injected",
                "injection_id": injection.get("injection", {}).get("injection_id", ""),
            }

    def _spawn_phase_child(self, parent_id: str, phase: dict, expected_timing: str = "enter") -> dict | None:
        spawn = phase.get("spawn_child")
        if not spawn:
            return None
        if phase.get("spawn_on", "enter") != expected_timing:
            return None

        child_id = spawn.get("child_id")
        if child_id and child_id in self.threats:
            return {"child_id": child_id, "parent_id": parent_id, "status": "already_spawned"}
        return self.spawn_child(parent_id, spawn)

    def spawn_child(self, parent_id: str, spawn_req: dict) -> dict:
        """Spawn a child entity from a parent threat.

        Used for: warship → drone, bomber → missile, carrier → fighter.

        Args:
            parent_id: Parent threat ID.
            spawn_req: Spawn specification with child_id, child_type, domain,
                       initial_offset, flight_profile, initial_assessment.

        Returns:
            ``{"child_id": "...", "parent_id": "...", "status": "spawned"}``
        """
        with self._lock:
            parent = self.threats.get(parent_id)
            if not parent:
                return {"error": f"parent not found: {parent_id}"}

            child_id = spawn_req.get("child_id", f"{parent_id}-CHILD-{self._rng.getrandbits(16):04x}")
            offset = spawn_req.get("initial_offset", {})
            bearing_rad = math.radians(offset.get("bearing_deg", parent["heading"]))
            dist_nm = offset.get("distance_nm", 1.0)
            dist_deg = dist_nm / 60.0

            child_lat = parent["lat"] + dist_deg * math.cos(bearing_rad)
            child_lng = parent["lng"] + dist_deg * math.sin(bearing_rad)

            flight = spawn_req.get("flight_profile", {})
            assess = spawn_req.get("initial_assessment", {})

            self.threats[child_id] = {
                "id": child_id,
                "type": spawn_req.get("child_type", "unknown"),
                "domain": spawn_req.get("domain", "air"),
                "lat": child_lat,
                "lng": child_lng,
                "altitude_ft": flight.get("altitude_ft", assess.get("altitude_ft", 0)),
                "heading": flight.get("heading", parent["heading"]),
                "_commanded_heading": flight.get("heading", parent["heading"]),
                "speed_kts": flight.get("speed_kts", 60),
                "risk_level": assess.get("risk_level", parent.get("risk_level", "MEDIUM")),
                "neutralized": False,
                "detected_by": [],
                "first_detected": None,
                "rcs_dbsm": assess.get("rcs_dbsm"),
                "ir_signature": assess.get("ir_signature", "medium"),
                "iff_status": assess.get("iff_status", "unknown"),
                "ais_match": assess.get("ais_match", False),
                "damage_state": "intact",
                "hit_count": 0,
                "parent_id": parent_id,
                "spawn_time": _now_iso(),
                "injection_info": parent.get("injection_info", {}),
                "flight_profile": flight,
                "_flight_route": [dict(point) for point in flight.get("route") or []],
                "_behavior_script": None,
                "_behavior_phase": None,
            }

            # Register mesh node
            self.mesh.register_node(child_id, child_lat, child_lng, node_type="threat")

            return {
                "child_id": child_id,
                "parent_id": parent_id,
                "status": "spawned",
            }

    # ── Command execution (F22 Issue Order) ────────────────────

    def execute_command(self, command: dict) -> dict:
        """Execute a standardized Agent→Platform command.

        Supported command types:
          - ``"retask"``: redirect asset to new waypoints or intercept target
          - ``"fire"``: launch a weapon from asset toward target
          - ``"hold"``: asset holds position (clears route, speed → 0)
          - ``"resume_patrol"``: restore default patrol route for an asset
          - ``"update_roe"``: update rules-of-engagement context

        Args:
            command: Command dict with ``command_type`` and ``params``.

        Returns:
            ``{"command_id": "...", "status": "executed", ...}``
        """
        cmd_type = command.get("command_type", "")
        cmd_id = command.get("command_id", f"CMD-{self._rng.getrandbits(32):08x}")
        params = command.get("params", {})

        if cmd_type == "retask":
            if "waypoints" in params:
                result = self.retask_asset(params["asset_id"], params["waypoints"])
            elif "target_lat" in params:
                result = self.retask_asset_to_intercept(
                    params["asset_id"], params["target_lat"], params["target_lng"])
            else:
                return {"command_id": cmd_id, "status": "error",
                        "error": "retask requires waypoints or target_lat/target_lng"}

        elif cmd_type == "fire":
            result = self.fire_weapon(
                params.get("asset_id", ""),
                params.get("threat_id", ""),
                params.get("weapon_name", ""),
            )

        elif cmd_type == "hold":
            asset_id = params.get("asset_id", "")
            if asset_id not in self.assets:
                result = {"error": f"asset not found: {asset_id}"}
            else:
                self.waypoint_nav.clear(asset_id)
                # Reduce speed toward zero (gradual stop, simplified)
                asset = self.assets[asset_id]
                asset["speed_kts"] = 0
                self.alerts.append({
                    "level": "INFO",
                    "msg": f"平台 {asset_id} 进入待命状态",
                    "time": _now_iso(),
                })
                result = {"asset_id": asset_id, "status": "holding"}

        elif cmd_type == "resume_patrol":
            asset_id = params.get("asset_id", "")
            if asset_id not in self.assets:
                result = {"error": f"asset not found: {asset_id}"}
            else:
                self._generate_patrol_route(asset_id)
                asset = self.assets[asset_id]
                asset["speed_kts"] = 30 if asset.get("domain") == "maritime" else 400
                self.alerts.append({
                    "level": "INFO",
                    "msg": f"平台 {asset_id} 恢复巡逻",
                    "time": _now_iso(),
                })
                result = {"asset_id": asset_id, "status": "patrolling"}

        elif cmd_type == "update_roe":
            roe = params.get("roe", {})
            self.clock.setdefault("roe_context", {}).update(roe)
            self.alerts.append({
                "level": "INFO",
                "msg": f"ROE 已更新: {list(roe.keys())}",
                "time": _now_iso(),
            })
            result = {"status": "roe_updated", "roe": self.clock["roe_context"]}

        else:
            return {"command_id": cmd_id, "status": "error",
                    "error": f"unknown command_type: {cmd_type}"}

        return {
            "command_id": cmd_id,
            "command_type": cmd_type,
            "status": "executed" if "error" not in result else "error",
            "result": result,
        }

    # ── Internals ──────────────────────────────────────────────

    def _apply_damage(self, threat: dict, damage_state: str) -> None:
        """Apply damage effects to a threat after weapon impact.

        ``"damaged"``: speed halved, RF power reduced, RCS altered.
        ``"destroyed"``: speed zeroed, RF silent, marked neutralized.
        """
        previous_damage_state = str(threat.get("damage_state") or "")
        threat["damage_state"] = damage_state
        threat.setdefault("hit_count", 0)
        if not (damage_state == "destroyed" and previous_damage_state == "impact_pending"):
            threat["hit_count"] += 1

        if damage_state == "destroyed":
            threat["_impact_pending"] = False
            threat["neutralized"] = True
            threat["speed_kts"] = 0
            threat["rf_freq_mhz"] = None
            threat["power_dbm"] = None
            threat["rcs_dbsm"] = None
            threat["ir_signature"] = "high"  # explosion

            contact_label, track_id = self._operator_contact_for_threat(str(threat.get("id") or ""))
            self.alerts.append({
                "level": "CRITICAL",
                "msg": f"目标摧毁: {contact_label}",
                "time": _now_iso(),
                "type": "target_destroyed",
                "target_track_id": track_id,
            })
        elif damage_state == "impact_pending":
            threat["_impact_pending"] = True
            threat["neutralized"] = False
            threat["speed_kts"] = 0
            threat["rf_freq_mhz"] = None
            threat["power_dbm"] = None
            threat["ir_signature"] = "high"
            contact_label, track_id = self._operator_contact_for_threat(str(threat.get("id") or ""))
            self.alerts.append({
                "level": "WARNING",
                "msg": f"导弹命中 {contact_label}，等待毁伤评估确认",
                "time": _now_iso(),
                "type": "target_impact_pending_assessment",
                "target_track_id": track_id,
            })
        elif damage_state == "damaged":
            threat["speed_kts"] = max(1, threat.get("speed_kts", 0) * 0.5)
            if threat.get("power_dbm") is not None:
                threat["power_dbm"] = max(0, threat["power_dbm"] - 30)
            if threat.get("rcs_dbsm") is not None:
                if threat.get("rcs_dbsm") is not None:
                    threat["rcs_dbsm"] = threat["rcs_dbsm"] + 5  # structure damage
                else:
                    threat["rcs_dbsm"] = 10.0  # damaged structure becomes visible

            contact_label, track_id = self._operator_contact_for_threat(str(threat.get("id") or ""))
            self.alerts.append({
                "level": "WARNING",
                "msg": (f"目标受损: {contact_label} · "
                        f"速度降至 {threat['speed_kts']} kts"),
                "time": _now_iso(),
                "target_track_id": track_id,
                "type": "target_damaged",
            })

    def _generate_patrol_route(self, asset_id: str) -> None:
        """Create a small patrol loop around the asset's current position."""
        a = self.assets.get(asset_id)
        if not a:
            return
        pos = a.get("position", a)
        lat, lng = pos.get("lat", 0), pos.get("lng", 0)
        domain = a.get("domain", "air")

        # Larger patrol for air, smaller for maritime
        radius = 0.2 if domain == "air" else 0.08
        points = []
        for i in range(4):
            angle = math.radians(i * 90)
            points.append({
                "lat": round(lat + radius * math.cos(angle), 4),
                "lng": round(lng + radius * math.sin(angle), 4),
                "label": f"PATROL-{i+1}",
            })

        # Close the loop
        points.append(points[0])
        points[-1]["label"] = "PATROL-RTB"

        self.waypoint_nav.set_route(asset_id, points)

    def _generate_alerts(self, dt: float) -> None:
        """Generate simulation alerts based on current state."""
        tracks = self.sensor_fusion.get_tracks()

        # Coverage gap alert
        gaps = self.sensor_fusion.get_coverage_gaps()
        if len(gaps) > 0 and self._rng.random() < 0.1:  # 10% chance per tick
            self.alerts.append({
                "level": "WARNING",
                "msg": f"传感器覆盖缺口: {len(gaps)} 个潜在接触未被覆盖",
                "time": _now_iso(),
            })

        # High threat level alert
        for trk_id, trk in tracks.items():
            assessment = trk.get("agent_assessment") or {}
            if assessment.get("status") == "confirmed" and requires_decision_alert(trk):
                if self._rng.random() < 0.15:
                    self.alerts.append({
                        "level": "CRITICAL",
                        "msg": f"高风险威胁 {trk['id']} ({trk.get('classification','?')}) 需要决策",
                        "time": _now_iso(),
                    })
