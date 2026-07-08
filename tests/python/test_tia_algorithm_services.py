"""Tests for TIA algorithm python_http_service packages."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SERVICES = ROOT / "services"
sys.path.insert(0, str(SERVICES))
sys.path.insert(0, str(ROOT))

from a2a_algorithms_common.http_service import create_algorithm_app  # noqa: E402
from a2a_algorithms_common.tia_predictors import (  # noqa: E402
    PREDICTOR_REGISTRY,
    tia_model_loaded,
)

TIA_ALGORITHM_IDS = list(PREDICTOR_REGISTRY.keys())


@pytest.fixture(scope="session")
def tia_clients():
    from fastapi.testclient import TestClient

    clients = {}
    for algorithm_id, predict_fn in PREDICTOR_REGISTRY.items():
        app = create_algorithm_app(
            algorithm_id,
            "1.0.0",
            "tia",
            predict_fn,
            model_loaded_callable=lambda aid=algorithm_id: tia_model_loaded(aid),
        )
        clients[algorithm_id] = TestClient(app)
    return clients


@pytest.mark.parametrize("algorithm_id", TIA_ALGORITHM_IDS)
def test_tia_health_ready(tia_clients, algorithm_id):
    resp = tia_clients[algorithm_id].get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["algorithm_id"] == algorithm_id


@pytest.mark.parametrize("algorithm_id", TIA_ALGORITHM_IDS)
def test_tia_predict_golden_case(tia_clients, algorithm_id):
    pkg = ROOT / "examples" / algorithm_id / "1.0.0" / "golden_cases" / "case_001_request.json"
    request = json.loads(pkg.read_text(encoding="utf-8"))
    resp = tia_clients[algorithm_id].post("/predict", json=request)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True, body
    assert body["outputs"]


def test_golden_packages_exist():
    for algorithm_id in TIA_ALGORITHM_IDS:
        base = ROOT / "examples" / algorithm_id / "1.0.0"
        for name in (
            "algorithm_card.yaml",
            "input.schema.json",
            "output.schema.json",
            "golden_cases/case_001_request.json",
            "golden_cases/case_001_response.json",
        ):
            assert (base / name).is_file(), f"missing {algorithm_id}/{name}"
