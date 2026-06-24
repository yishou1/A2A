from app.scenario_generator import generate_long_operation_sequence
from eval.prediction_eval import evaluate_sequence


def test_prediction_eval_compares_baseline_and_st_gnn_enhanced_runs():
    sequence = generate_long_operation_sequence(frame_count=18)

    report = evaluate_sequence(sequence["frames"])

    assert report["scenario_id"] == "in_memory_sequence"
    assert report["frames_processed"] == 18
    assert report["baseline"]["sample_count"] > 0
    assert report["enhanced"]["sample_count"] > 0
    assert report["baseline"]["mean_ade_m"] >= 0
    assert report["enhanced"]["mean_fde_m"] >= 0
    assert "st_gnn_delta" in report
    assert "mean_ade_delta_m" in report["st_gnn_delta"]
    assert report["safety_boundary"].startswith("Simulation-only")
