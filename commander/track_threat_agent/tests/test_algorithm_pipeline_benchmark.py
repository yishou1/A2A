from scripts.benchmark_algorithm_pipeline import run_benchmark


def test_algorithm_pipeline_benchmark_passes_short_regression():
    report = run_benchmark(frame_count=20, seed=42, max_p95_ms=200.0)

    assert report["passed"] is True
    assert report["metrics"]["id_switches"] == 0
    assert report["metrics"]["maximum_false_tracks"] == 0
    assert report["metrics"]["ranking_explanation_coverage"] == 1.0
