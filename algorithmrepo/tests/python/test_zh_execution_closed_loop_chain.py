from __future__ import annotations

import pytest

from services.a2a_algorithms_common.service_predictors import (
    predict_closed_loop_decision_advisor,
    predict_execution_control_planner,
    predict_mission_completion_scorer,
    predict_mission_feature_adapter,
)


@pytest.mark.parametrize("profile,trees", [("low", 32), ("medium", 96), ("high", 192)])
def test_execution_to_closed_loop_chain(profile: str, trees: int) -> None:
    results = {
        "compliance_authorization": {
            "output_data": {"decision": "approved", "approved_for_demo_handoff": True}
        },
        "plan_decision": {"output_data": {"decision": "STRIKE"}},
        "threat_evaluation": {
            "output_data": {
                "priority_score": 0.78,
                "ranked_targets": [{"target_id": "T-001", "score": 0.78}],
            }
        },
        "perception_detection": {"output_data": {"detections": [{"conf": 0.91}]}},
        "resource_allocation": {"output_data": {"readiness": 0.84, "supply_pressure": 0.35}},
        "communication": {"output_data": {"delivery_rate": 0.93}},
        "data_fusion": {
            "output_data": {
                "track_history": [
                    {
                        "track_id": "T-001",
                        "history": [
                            {"t": 0.0, "x": 10.0, "y": 18.0},
                            {"t": 0.2, "x": 10.9, "y": 19.1},
                            {"t": 0.4, "x": 11.8, "y": 20.2},
                        ],
                        "weapon_prep_sec": 2.0,
                        "flight_time_sec": 4.0,
                    }
                ]
            }
        },
    }

    execution = predict_execution_control_planner(
        {"phase": "strike", "results": results},
        {"profile": profile},
    )
    assert execution["assessment_status"] == "ready"
    assert execution["commands"][0]["target_id"] == "T-001"
    expected_motion = "sklearn_linear_regression" if profile == "low" else "filterpy_constant_velocity_kalman"
    assert execution["prediction_details"][0]["model"] == expected_motion

    closed_loop_results = {
        **results,
        "execution_control": {"output_data": execution},
        "damage_confirmation": {"output_data": {"engaged_targets": 1, "confirmed_destroyed": 1}},
    }
    adapted = predict_mission_feature_adapter(
        {"source_type": "agent_results", "mode": "strict", "agent_results": closed_loop_results},
        {"profile": profile},
    )
    assert adapted["assessment_status"] == "ready"
    assert adapted["missing_fields"] == []
    assert set(adapted["values"]) == {
        "damage_rate",
        "asset_readiness",
        "control_timeliness",
        "intel_confidence",
        "threat_pressure",
        "ammo_pressure",
        "comm_quality",
    }

    mission = predict_mission_completion_scorer(
        {"features": adapted["values"]},
        {"profile": profile},
    )
    assert mission["profile_config"]["forest_trees_used"] == trees
    assert 0.0 <= mission["mission_completion"] <= 1.0

    advice = predict_closed_loop_decision_advisor(
        {
            "target": {"target_id": "T-001", "threat_score": 0.78, "uncertainty": 0.09},
            "damage_probability": adapted["values"]["damage_rate"],
            "situation": "critical",
            "mission_completion": mission["mission_completion"],
        },
        {"profile": profile},
    )
    assert advice["target_id"] == "T-001"
    assert advice["algorithm_profile"] == profile
    assert advice["action"] in {
        "confirm_effect_and_shift",
        "re_attack",
        "reallocate_sensor",
        "coordinated_suppression",
        "continue_tracking",
    }
