from __future__ import annotations

from scripts.accept_all_algorithms import find_non_real_markers


def test_real_gate_accepts_normal_outputs() -> None:
    payload = {
        "model_runtime": {"backend": "onnxruntime", "fallback": False},
        "scores": [0.2, 0.8],
        "assessment_status": "model_estimate",
    }
    assert find_non_real_markers(payload) == []


def test_real_gate_rejects_boolean_fallback() -> None:
    payload = {"model_runtime": {"fallback_used": True}}
    assert find_non_real_markers(payload) == [
        "$.model_runtime.fallback_used=true"
    ]


def test_real_gate_rejects_mock_text_marker() -> None:
    payload = {"damage_mask_ref": "siamese_mask2former_mock"}
    assert find_non_real_markers(payload) == [
        "$.damage_mask_ref='siamese_mask2former_mock'"
    ]


def test_real_gate_rejects_nested_random_initialization() -> None:
    payload = {"items": [{"runtime": "random-initialized head"}]}
    assert find_non_real_markers(payload) == [
        "$.items[0].runtime='random-initialized head'"
    ]


def test_real_gate_rejects_stub_runtime() -> None:
    payload = {"usage": {"session_backend": "stub", "execution_provider": "cpu"}}
    assert find_non_real_markers(payload) == [
        "$.usage.session_backend='stub'"
    ]
