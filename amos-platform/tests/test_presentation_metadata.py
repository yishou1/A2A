"""Resource metadata must not turn missing readings into healthy defaults."""
from amos_platform.simulation.engine import SimEngine
from amos_platform.simulation.state_projector import build_internal_state


def test_health_projection_preserves_absent_and_zero_readings():
    engine = SimEngine()
    engine.assets = {
        "survey-boat": {"domain": "maritime", "health": {"fuel_pct": 63}},
        "weather-satellite": {"domain": "space", "health": {"battery_pct": 0, "comms_strength": 0}},
        "unknown": {"domain": "ground", "health": {}},
    }
    rows = {row["id"]: row for row in build_internal_state(engine)["assets"]}
    assert rows["survey-boat"]["battery_pct"] is None
    assert rows["survey-boat"]["fuel_pct"] == 63
    assert rows["survey-boat"]["comms_strength"] is None
    assert rows["weather-satellite"]["battery_pct"] == 0
    assert rows["weather-satellite"]["comms_strength"] == 0
    assert rows["unknown"]["fuel_pct"] is None
    assert rows["unknown"]["battery_pct"] is None
