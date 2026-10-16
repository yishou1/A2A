from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COMMANDER_ROOT = ROOT if (ROOT / "decision_support").is_dir() else ROOT.parent / "commander"
SERVICES = ROOT / "services"
sys.path.insert(0, str(COMMANDER_ROOT))
sys.path.insert(0, str(SERVICES))

from a2a_algorithms_common.decision_agent_predictors import (  # noqa: E402
    predict_compliance_authorization_core,
    predict_decision_planning_core,
)


CAPABILITY_PACKAGES = {
    "decision_plan_recommender_onnx",
    "target_trend_predictor_onnx",
    "compliance_risk_scorer_onnx",
}


def test_decision_capabilities_are_independent_algorithm_packages():
    for algorithm_id in CAPABILITY_PACKAGES:
        package = ROOT / "examples" / algorithm_id / "1.0.0"
        assert (package / "algorithm_card.yaml").is_file()
        assert (package / "input.schema.json").is_file()
        assert (package / "output.schema.json").is_file()
        assert (package / "model.onnx").is_file()
        assert (package / "model.metadata.json").is_file()


def test_decision_agent_core_composes_independent_capabilities():
    planning_payload = _load_case("decision_planning_core")
    compliance_payload = _load_case("compliance_authorization_core")

    planning_outputs = predict_decision_planning_core(
        planning_payload["inputs"],
        planning_payload.get("params", {}),
    )
    compliance_outputs = predict_compliance_authorization_core(
        compliance_payload["inputs"],
        compliance_payload.get("params", {}),
    )

    plan_runtime = planning_outputs["model_runtime"]["plan_recommendation"]
    assert plan_runtime["algorithm_id"] == "decision_plan_recommender_onnx"
    assert plan_runtime["version"] == "1.0.0"
    assert plan_runtime["plans"]
    assert all(item["backend"] == "onnxruntime" for item in plan_runtime["plans"])
    assert all(item["used"] is True for item in plan_runtime["plans"])

    trend_runtime = planning_outputs["model_runtime"]["target_trend"]
    assert trend_runtime["algorithm_id"] == "target_trend_predictor_onnx"
    assert trend_runtime["version"] == "1.0.0"
    assert trend_runtime["targets"]

    compliance_runtime = compliance_outputs["model_runtime"]["compliance_risk"]
    assert compliance_runtime["algorithm_id"] == "compliance_risk_scorer_onnx"
    assert compliance_runtime["version"] == "1.0.0"
    assert compliance_runtime["backend"] == "onnxruntime"
    assert compliance_runtime["used"] is True


def test_decision_agent_core_falls_back_when_capability_packages_are_missing(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("DECISION_CAPABILITY_PACKAGE_ROOT", str(tmp_path))
    planning_payload = _load_case("decision_planning_core")
    compliance_payload = _load_case("compliance_authorization_core")

    planning_outputs = predict_decision_planning_core(
        planning_payload["inputs"],
        planning_payload.get("params", {}),
    )
    compliance_outputs = predict_compliance_authorization_core(
        compliance_payload["inputs"],
        compliance_payload.get("params", {}),
    )

    plan_runtime = planning_outputs["model_runtime"]["plan_recommendation"]
    assert all(item["fallback"] is True for item in plan_runtime["plans"])
    assert all(item["backend"] == "python_formula" for item in plan_runtime["plans"])

    compliance_runtime = compliance_outputs["model_runtime"]["compliance_risk"]
    assert compliance_runtime["fallback"] is True
    assert compliance_runtime["backend"] == "python_formula"


def test_core_predictor_does_not_import_agent_implementations():
    source = (
        ROOT / "services" / "a2a_algorithms_common" / "decision_agent_predictors.py"
    ).read_text(encoding="utf-8")
    assert "decision_agents" not in source
    assert "from decision_support." in source


def test_commander_fixture_uses_the_canonical_capability_adapters():
    a2a_root = ROOT.parent
    for filename in ("decision_capabilities.py", "decision_agent_predictors.py"):
        canonical = (
            a2a_root / "algorithmrepo" / "services" / "a2a_algorithms_common" / filename
        )
        fixture = a2a_root / "commander" / "services" / "a2a_algorithms_common" / filename
        assert fixture.read_bytes() == canonical.read_bytes()


def _load_case(algorithm_id: str) -> dict:
    path = (
        ROOT
        / "examples"
        / algorithm_id
        / "1.0.0"
        / "golden_cases"
        / "case_001_request.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))
