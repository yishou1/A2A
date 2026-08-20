from __future__ import annotations

import base64
import io
import json
import sys
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
SERVICES = ROOT / "services"
sys.path.insert(0, str(SERVICES))

from a2a_algorithms_common.service_predictors import (  # noqa: E402
    predict_xbd_damage_assessor,
    xbd_damage_model_loaded,
)


def _tiny_png_base64(rgb: tuple[int, int, int]) -> str:
    image = Image.new("RGB", (64, 64), rgb)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


@pytest.fixture(scope="session")
def xbd_client():
    from fastapi.testclient import TestClient

    from a2a_algorithms_common.http_service import create_algorithm_app

    app = create_algorithm_app(
        "xbd_damage_assessor",
        "1.0.0",
        "scoring",
        predict_xbd_damage_assessor,
        model_loaded_callable=xbd_damage_model_loaded,
    )
    return TestClient(app)


def test_xbd_damage_model_loaded():
    assert xbd_damage_model_loaded() is True


def test_xbd_features_mode_inference():
    outputs = predict_xbd_damage_assessor(
        {
            "input_mode": "features",
            "sample_id": "guatemala-volcano_00000000",
            "handcrafted_features": {
                "pre_area": 0.0069561004638671875,
                "spectral_delta": 0.11385739196510551,
                "texture_delta": 0.040614633569358925,
                "heat_signature": 0.10955983161018899,
                "crater_density": 0.29558541266794625,
                "std_spectral": 0.09248520384250485,
                "max_spectral": 0.615686274509804,
                "high_change_ratio": 0.21565670414038937,
                "severe_damage_ratio": 0.06073485056210584,
                "collapse_ratio": 0.17918837400603235,
                "post_brightness": 0.6580762055301864,
                "brightness_drop": 0.0601235503798625,
                "normalized_distance": 0.411391608396209,
                "detection_confidence": 1.0,
                "threat_score": 0.5,
            },
        },
        {},
    )
    assert outputs["assessment_status"] == "model_estimate"
    assert outputs["input_mode"] == "features"
    assert 0.0 <= outputs["damage_probability"] <= 1.0
    assert outputs["damage_label"] in {0, 1}


def test_xbd_images_mode_with_polygon():
    outputs = predict_xbd_damage_assessor(
        {
            "input_mode": "images",
            "pre_image": _tiny_png_base64((220, 200, 180)),
            "post_image": _tiny_png_base64((80, 60, 50)),
            "polygon": [[8, 8], [56, 8], [56, 56], [8, 56]],
        },
        {},
    )
    assert outputs["assessment_status"] == "model_estimate"
    assert outputs["input_mode"] == "images"
    assert outputs["feature_dim"] == 1567


def test_xbd_images_mode_missing_polygon_is_insufficient_data():
    outputs = predict_xbd_damage_assessor(
        {
            "input_mode": "images",
            "pre_image": _tiny_png_base64((220, 200, 180)),
            "post_image": _tiny_png_base64((80, 60, 50)),
        },
        {},
    )
    assert outputs["assessment_status"] == "insufficient_data"
    assert "polygon" in outputs["missing_fields"]
    assert outputs["damage_probability"] is None


def test_xbd_http_golden_cases(xbd_client):
    for case_name in ("case_001", "case_002"):
        request_path = ROOT / "examples" / "xbd_damage_assessor" / "1.0.0" / "golden_cases" / f"{case_name}_request.json"
        response_path = ROOT / "examples" / "xbd_damage_assessor" / "1.0.0" / "golden_cases" / f"{case_name}_response.json"
        payload = json.loads(request_path.read_text(encoding="utf-8"))
        expected = json.loads(response_path.read_text(encoding="utf-8"))
        result = xbd_client.post("/predict", json=payload).json()
        assert result["ok"] is True
        assert result["outputs"]["assessment_status"] == expected["outputs"]["assessment_status"]
        assert result["outputs"]["damage_label"] == expected["outputs"]["damage_label"]


def test_xbd_http_health_and_metadata(xbd_client):
    health = xbd_client.get("/health").json()
    assert health["algorithm_id"] == "xbd_damage_assessor"
    assert health["status"] == "ready"
    metadata = xbd_client.get("/metadata").json()
    assert metadata["backend_type"] == "python_http_service"
