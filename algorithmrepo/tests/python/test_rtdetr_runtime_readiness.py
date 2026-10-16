from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SERVICES = ROOT / "services"
sys.path.insert(0, str(SERVICES))
sys.path.insert(0, str(ROOT))

from agent.inference.registry import (  # noqa: E402
    detector_runtime_profile,
    get_odconv_refiner,
    resolve_detector_weights,
)
from a2a_algorithms_common import tia_predictors  # noqa: E402
from agent.inference import registry  # noqa: E402


def test_detector_defaults_to_battlefield_checkpoint(tmp_path, monkeypatch) -> None:
    checkpoint_dir = tmp_path / "models" / "checkpoints"
    checkpoint_dir.mkdir(parents=True)
    checkpoint = checkpoint_dir / "battlefield_rtdetr.pt"
    checkpoint.write_bytes(b"test battlefield checkpoint")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(registry, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(registry, "CHECKPOINT_DIR", checkpoint_dir)

    assert resolve_detector_weights({}) == checkpoint.resolve()


def test_missing_battlefield_checkpoint_does_not_silently_use_generic_model(tmp_path, monkeypatch) -> None:
    (tmp_path / "rtdetr-l.pt").write_bytes(b"test generic checkpoint")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(registry, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(registry, "CHECKPOINT_DIR", tmp_path / "models" / "checkpoints")

    with pytest.raises(FileNotFoundError, match="Install the battlefield RT-DETR release bundle"):
        resolve_detector_weights({})


def test_detector_falls_back_to_bundled_rtdetr_l() -> None:
    path = resolve_detector_weights(
        {
            "detection_model": "models/checkpoints/missing-battlefield-rtdetr.pt",
            "detection_fallback_model": "rtdetr-l.pt",
        }
    )

    assert path == (ROOT / "rtdetr-l.pt").resolve()


def test_missing_odconv_checkpoint_disables_refinement() -> None:
    config = {"odconv_checkpoint": "models/checkpoints/missing-odconv-refiner.pt"}

    assert get_odconv_refiner(config) is None
    assert detector_runtime_profile(config)["odconv_enabled"] is False


def test_rtdetr_health_accepts_fallback_model(monkeypatch) -> None:
    monkeypatch.setenv("TIA_USE_MOCK", "0")
    tia_predictors._config.cache_clear()
    tia_predictors._backend.cache_clear()

    assert tia_predictors.tia_model_loaded("battlefield_rtdetr_detector") is True


def test_rtdetr_response_reports_real_artifacts(monkeypatch) -> None:
    monkeypatch.setenv("TIA_USE_MOCK", "0")
    monkeypatch.setattr(
        tia_predictors,
        "_backend_config",
        lambda _module: {
            "detection_model": "models/checkpoints/missing-battlefield-rtdetr.pt",
            "detection_fallback_model": "rtdetr-l.pt",
            "odconv_checkpoint": "models/checkpoints/missing-odconv-refiner.pt",
        },
    )
    monkeypatch.setattr(
        tia_predictors,
        "_run_backend",
        lambda _module, _class, _inputs: [
            {
                "class_name": "boat",
                "confidence": 0.5,
                "bbox": [1, 2, 3, 4],
                "odconv_refined": False,
            }
        ],
    )

    output = tia_predictors.predict_battlefield_rtdetr_detector(
        {"frames": [{"sensor_id": "EO-1", "modality": "eo_ir"}]},
        {},
    )

    assert output["runtime"]["mode"] == "real"
    assert output["runtime"]["detector_model"] == "rtdetr-l.pt"
    assert output["runtime"]["odconv_enabled"] is False
    assert output["detections"][0]["odconv_refined"] is False
