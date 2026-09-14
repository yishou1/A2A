from __future__ import annotations

import ast
from pathlib import Path

import pytest

from services.a2a_algorithms_common.algorithm_profiles import (
    available_algorithm_profiles,
    normalize_algorithm_profile,
    profile_config,
)
from services.a2a_algorithms_common.closed_loop_advisor import advise
from services.a2a_algorithms_common.mission_scorer import _predict_with_tree_budget, score_mission


ROOT = Path(__file__).resolve().parents[2]
COMMON = ROOT / "services" / "a2a_algorithms_common"


def _feature_bundle() -> dict:
    return {
        "feature_version": "mission_features_v2",
        "assessment_status": "ready",
        "values": {
            "damage_rate": 0.8,
            "asset_readiness": 0.8,
            "control_timeliness": 0.8,
            "intel_confidence": 0.8,
            "threat_pressure": 0.3,
            "ammo_pressure": 0.2,
            "comm_quality": 0.9,
        },
    }


def test_profiles_have_stable_names_and_aliases() -> None:
    assert set(available_algorithm_profiles()) == {"low", "medium", "high"}
    assert normalize_algorithm_profile("small") == "low"
    assert normalize_algorithm_profile("large") == "high"
    with pytest.raises(ValueError, match="unsupported algorithm profile"):
        normalize_algorithm_profile("unknown")


@pytest.mark.parametrize("profile,trees", [("low", 32), ("medium", 96), ("high", 192)])
def test_mission_scorer_applies_forest_budget(profile: str, trees: int) -> None:
    result = score_mission(_feature_bundle(), profile=profile)
    assert result["algorithm_profile"] == profile
    assert result["profile_config"]["forest_trees_used"] == trees
    assert 0.0 <= result["mission_completion"] <= 1.0


def test_advisor_policy_is_business_stable_across_profiles() -> None:
    actions = {
        advise({"target_id": "T-1", "threat_score": 0.8}, 0.9, "critical", 0.8, profile=name)["action"]
        for name in available_algorithm_profiles()
    }
    assert actions == {"confirm_effect_and_shift"}


def test_algorithm_modules_do_not_import_agent_or_transport_layers() -> None:
    checked = [
        "algorithm_profiles.py",
        "execution_planner.py",
        "mission_feature_adapter.py",
        "mission_scorer.py",
        "closed_loop_advisor.py",
    ]
    forbidden = ("commander", "closed_loop_agent", "execution_control_agent", "fastapi", "algolib_bridge")
    for filename in checked:
        tree = ast.parse((COMMON / filename).read_text(encoding="utf-8"))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)
        assert not any(name.startswith(forbidden) for name in imports), (filename, imports)


def test_low_execution_profile_is_bounded() -> None:
    assert profile_config("execution_control", "low") == {
        "max_tracks": 16,
        "max_matched_rules": 3,
    }


def test_scorer_accepts_sklearn_style_estimators() -> None:
    class Estimator:
        def __init__(self, value: float) -> None:
            self.value = value

        def predict(self, rows: list[list[float]]) -> list[float]:
            return [self.value for _ in rows]

    class Forest:
        estimators_ = [Estimator(0.2), Estimator(0.6), Estimator(1.0)]

    score, used = _predict_with_tree_budget(Forest(), [0.0] * 7, 2)
    assert score == pytest.approx(0.4)
    assert used == 2
