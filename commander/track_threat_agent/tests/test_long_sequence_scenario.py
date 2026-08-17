from app.scenario_generator import generate_long_operation_sequence


def test_long_operation_sequence_has_expected_frames_assets_and_behaviors():
    sequence = generate_long_operation_sequence(frame_count=90)

    assert sequence["scenario_id"] == "coastal_joint_operation_90_frames"
    assert len(sequence["frames"]) == 90
    assert sequence["frames"][0]["scene"]["protected_assets"]
    assert len(sequence["frames"][0]["detections"]) == 7
    assert sequence["frames"][0]["scene"]["operation_phase"] == "phase_1_initial_detection"
    assert sequence["frames"][35]["scene"]["operation_phase"] == "phase_3_protected_asset_monitoring"
    assert sequence["frames"][45]["scene"]["operation_phase"] == "phase_4_anomaly_escalation"

    unknown_after_anomaly = next(
        item
        for item in sequence["frames"][45]["detections"]
        if item["detection_id"].startswith("auto-unknown-intermittent-1")
    )

    assert unknown_after_anomaly["confidence"] < 0.45
    assert unknown_after_anomaly["metadata"]["simulated_anomaly"]
