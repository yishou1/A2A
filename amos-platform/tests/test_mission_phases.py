from amos_platform.domain.mission_phases import build_mission_phase_state


def test_ooda_is_strictly_derived_from_f2t2ea() -> None:
    state = build_mission_phase_state(
        {"timeline": [{"phase": "FIND"}, {"phase": "FIX"}, {"phase": "TRACK"}]},
        {"lifecycle": "paused", "director_status": "awaiting_analysis"},
    )

    assert state["f2t2ea"]["current"] == "TRACK"
    assert state["ooda"]["current"] == "ORIENT"
    assert [row["status"] for row in state["f2t2ea"]["items"]] == [
        "completed", "completed", "waiting_backend", "pending", "pending", "pending",
    ]
    assert [row["status"] for row in state["ooda"]["items"]] == [
        "completed", "waiting_backend", "pending", "pending",
    ]


def test_act_covers_engage_and_assess() -> None:
    engage = build_mission_phase_state(
        {"timeline": [{"phase": "ENGAGE"}]},
        {"lifecycle": "paused", "director_status": "awaiting_authorization"},
    )
    assess = build_mission_phase_state(
        {"timeline": [{"phase": "ASSESS"}]},
        {"lifecycle": "running", "director_status": "auto_running"},
    )

    assert engage["ooda"] == {
        "current": "ACT",
        "items": [
            {"phase": "OBSERVE", "status": "completed"},
            {"phase": "ORIENT", "status": "completed"},
            {"phase": "DECIDE", "status": "completed"},
            {"phase": "ACT", "status": "waiting_authorization"},
        ],
    }
    assert assess["ooda"]["current"] == "ACT"
    assert assess["ooda"]["items"][-1]["status"] == "active"
