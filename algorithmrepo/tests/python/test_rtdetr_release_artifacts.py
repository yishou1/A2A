from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
CHECKPOINT = (
    ROOT
    / "models"
    / "checkpoints"
    / "battlefield_rtdetr_unit17_coco25_ap_kaggle_best.pt"
)
ONNX_MODEL = ROOT / "models" / "battlefield_rtdetr.onnx"
ONNX_PACKAGE = ROOT / "examples" / "battlefield_rtdetr_detector_onnx" / "1.0.0"

EXPECTED_PT_SHA256 = "cb7092f2f5aea5bc23ec76b4a025db6cdd02548ef9fd9ae52d769fa3c3ae7cec"
EXPECTED_ONNX_SHA256 = "b61231916d211a9f2b3396dfc45b9a75823cf07f5e08577886c47b9dace5fc97"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def test_onnx_package_metadata_describes_the_delivered_model() -> None:
    metadata = json.loads((ONNX_PACKAGE / "model.metadata.json").read_text(encoding="utf-8"))
    labels = json.loads((ONNX_PACKAGE / "label_map.json").read_text(encoding="utf-8"))
    golden_input = json.loads(
        (ONNX_PACKAGE / "golden_cases" / "case_001_input.json").read_text(encoding="utf-8")
    )
    golden_expected = json.loads(
        (ONNX_PACKAGE / "golden_cases" / "case_001_expected.json").read_text(encoding="utf-8")
    )

    assert metadata["num_classes"] == 97
    assert metadata["input"]["shape"] == [1, 3, 640, 640]
    assert metadata["outputs"][0]["shape"] == [1, 300, 6]
    assert len(labels) == 97
    assert labels["0"] == "airport"
    assert labels["8"] == "plane"
    assert labels["10"] == "ship"
    assert golden_input == {"images": 0.0}
    assert golden_expected == {"output0": []}


def test_installed_release_artifact_hashes() -> None:
    if not CHECKPOINT.is_file() or not ONNX_MODEL.is_file():
        pytest.skip("RT-DETR release artifacts are installed separately from Git")

    assert _sha256(CHECKPOINT) == EXPECTED_PT_SHA256
    assert _sha256(ONNX_MODEL) == EXPECTED_ONNX_SHA256
    assert _sha256(ONNX_PACKAGE / "model.onnx") == EXPECTED_ONNX_SHA256


def test_installed_onnx_model_runs_zero_tensor_smoke() -> None:
    if not ONNX_MODEL.is_file():
        pytest.skip("RT-DETR ONNX artifact is installed separately from Git")

    numpy = pytest.importorskip("numpy")
    onnxruntime = pytest.importorskip("onnxruntime")
    session = onnxruntime.InferenceSession(
        str(ONNX_MODEL), providers=["CPUExecutionProvider"]
    )
    output = session.run(
        None,
        {"images": numpy.zeros((1, 3, 640, 640), dtype=numpy.float32)},
    )[0]

    assert output.shape == (1, 300, 6)
    assert output.dtype == numpy.float32
    assert float(output.min()) == pytest.approx(0.007876485586166382, abs=1e-6)
    assert float(output.max()) == pytest.approx(92.0, abs=1e-6)
    assert float(output.mean()) == pytest.approx(2.5330593585968018, abs=1e-6)
