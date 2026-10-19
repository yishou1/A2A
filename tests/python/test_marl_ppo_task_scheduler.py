from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest
from safetensors.torch import load_file

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "services"), str(ROOT)]

from agent.inference.models.marl_ppo_scheduler import MARLPPOSchedulerNet  # noqa: E402
from agent.inference.registry import clear_model_cache, get_marl_ppo_scheduler  # noqa: E402
from agent.training.battlefield_scheduling_env import BattlefieldSchedulingEnv  # noqa: E402
from a2a_algorithms_common import tia_predictors  # noqa: E402
from scripts.train_marl_ppo_scheduler import (  # noqa: E402
    evaluate_model,
    evaluate_random_policy,
    situation_from_dict,
)

CHECKPOINT = ROOT / "models/checkpoints/marl_ppo_scheduler_s.safetensors"
METADATA = ROOT / "models/checkpoints/marl_ppo_scheduler_s.metadata.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(autouse=True)
def real_small_profile(monkeypatch):
    monkeypatch.setenv("TIA_USE_MOCK", "0")
    monkeypatch.setenv("TIA_COMPUTE_PROFILE", "small")
    tia_predictors._config.cache_clear()
    tia_predictors._backend.cache_clear()
    clear_model_cache()
    yield
    tia_predictors._config.cache_clear()
    tia_predictors._backend.cache_clear()
    clear_model_cache()


def test_artifact_dataset_and_training_hashes_are_verified() -> None:
    metadata = json.loads(METADATA.read_text(encoding="utf-8"))
    dataset = ROOT / metadata["dataset"]["path"]
    script = ROOT / metadata["training_script"]
    assert _sha256(CHECKPOINT) == metadata["artifact_sha256"]
    assert _sha256(dataset) == metadata["dataset"]["sha256"]
    assert _sha256(script) == metadata["training_script_sha256"]
    assert metadata["architecture"] == {
        "action_count": 9,
        "hidden_dimension": 128,
        "maximum_agent_count": 8,
        "observation_dimension": 104,
        "parameter_count": 39434,
    }


def test_checkpoint_reproduces_holdout_metrics_and_beats_random_policy() -> None:
    metadata = json.loads(METADATA.read_text(encoding="utf-8"))
    payload = json.loads((ROOT / metadata["dataset"]["path"]).read_text(encoding="utf-8"))
    situations = [situation_from_dict(item) for item in payload]
    env = BattlefieldSchedulingEnv()
    model = MARLPPOSchedulerNet(env.obs_dim, env.n_actions, env.n_agents)
    model.load_state_dict(load_file(str(CHECKPOINT), device="cpu"), strict=True)
    model.eval()
    trained = evaluate_model(model, situations)
    baseline = evaluate_random_policy(situations, metadata["dataset"]["holdout_seed"] + 1)
    assert trained == metadata["evaluation"]["trained_policy"]
    assert baseline == metadata["evaluation"]["random_policy_baseline"]
    assert trained["total_reward"] > baseline["total_reward"]
    assert trained["high_threat_coverage"] > baseline["high_threat_coverage"]
    assert trained["reattack_coverage"] > baseline["reattack_coverage"]


def test_real_predictor_loads_policy_and_allocates_sensor_and_strike_actions() -> None:
    request = json.loads(
        (
            ROOT
            / "examples/marl_ppo_task_scheduler/1.0.0/golden_cases/case_001_request.json"
        ).read_text(encoding="utf-8")
    )
    assert tia_predictors.tia_model_loaded("marl_ppo_task_scheduler") is True
    output = tia_predictors.predict_marl_ppo_task_scheduler(request["inputs"], {})
    assert output["algorithm"] == "MARL-PPO"
    assert output["n_agents"] == 4
    assert {item["target_id"] for item in output["sensor_assignments"]} == {"T-1", "T-2"}
    assert any(item["task"] == "reattack" for item in output["reattack_plan"])
    assert output["model"]["artifact_sha256"] == _sha256(CHECKPOINT)


def test_registry_refuses_missing_checkpoint() -> None:
    with pytest.raises(FileNotFoundError, match="trained MARL-PPO checkpoint"):
        get_marl_ppo_scheduler(
            {
                "compute_profile": "small",
                "marl_ppo_checkpoint": "definitely_missing_marl_ppo.safetensors",
                "device": "cpu",
            }
        )
