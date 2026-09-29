from __future__ import annotations

from amos_platform.api.app_factory import create_app

from test_maritime_engagement import _identified_tracks


def test_duplicate_authorized_fire_command_returns_existing_weapon(monkeypatch) -> None:
    from amos_platform.api.routes import sim_routes

    engine, hostile, _ = _identified_tracks()
    weapon_name = engine._engagement_policy["authorized_weapons"][0]
    engine.issue_warning_at_track(hostile.id, authorized=True)
    engine._tick(300.0)

    monkeypatch.setattr(sim_routes, "get_engine", lambda: engine)
    app = create_app()
    app.testing = True
    client = app.test_client()
    request_body = {
        "command_type": "fire",
        "params": {
            "track_id": hostile.id,
            "asset_id": "ESCORT-01",
            "weapon_name": weapon_name,
        },
        "authorization": {"approved": True},
    }

    accepted = client.post("/api/v1/sim/commands", json=request_body)
    duplicate = client.post("/api/v1/sim/commands", json=request_body)

    assert accepted.status_code == 200
    assert duplicate.status_code == 200
    first_result = accepted.get_json()["data"]["result"]
    second_result = duplicate.get_json()["data"]["result"]
    assert second_result["weapon_id"] == first_result["weapon_id"]
    assert second_result["status"] == "already_executed"
