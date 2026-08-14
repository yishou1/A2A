"""task_scheduling_agent algolib runtime（对齐 lzh）单测。"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.algorithm_library.client import AlgorithmRunCall
from task_scheduling_agent.algolib_runtime import (
    DEFAULT_ALGORITHM,
    amos_to_algolib_inputs,
    build_local_scheduling_catalog,
    run_with_algolib,
)
from task_scheduling_agent.agent import TaskSchedulingAgent
from task_scheduling_agent.main import normalize_task_scheduling_result

SAMPLE = ROOT / "examples" / "amos_schedule_inputs" / "sample_amos_request.json"


class TestAlgolibInputs(unittest.TestCase):
    def test_amos_to_inputs_has_tracks_and_payload(self):
        payload = json.loads(SAMPLE.read_text(encoding="utf-8"))
        inputs = amos_to_algolib_inputs(payload)
        self.assertIn("amos_payload", inputs)
        self.assertTrue(inputs["tracks"])
        self.assertTrue(inputs["detections"])
        self.assertTrue(inputs["frames"])
        self.assertEqual(inputs["batch_context"]["phase"], "recon")

    def test_local_catalog_contains_marl_ppo(self):
        catalog = build_local_scheduling_catalog()
        self.assertEqual(catalog[0]["algorithm_id"], DEFAULT_ALGORITHM)


class TestRunWithAlgolib(unittest.TestCase):
    def test_normalized_output_preserves_algorithm_planning_evidence(self):
        result = normalize_task_scheduling_result(
            {"workflow_id": "wf-1", "input": {}},
            {
                "mission_id": "wf-1",
                "task_schedule": {},
                "algorithm": "mock-heuristic",
                "selected_algorithms": [DEFAULT_ALGORITHM],
                "llm_plan": {"mode": "llm", "catalog_source": "algolib:/algorithms"},
                "algolib_result": {"algorithm_id": DEFAULT_ALGORITHM},
            },
        )
        self.assertEqual(result["selected_algorithms"], [DEFAULT_ALGORITHM])
        self.assertEqual(result["llm_plan"]["mode"], "llm")
        self.assertEqual(result["algolib_result"]["algorithm_id"], DEFAULT_ALGORITHM)

    def test_fixed_default_algorithm_without_llm(self):
        payload = json.loads(SAMPLE.read_text(encoding="utf-8"))
        mock_client = MagicMock()
        mock_client.list_algorithms.return_value = build_local_scheduling_catalog()
        mock_client.predict.return_value = {
            "sensor_assignments": [
                {
                    "sensor_id": "UAV-1",
                    "target_id": "E-01",
                    "task": "surveillance",
                    "priority": "high",
                    "rationale": "unit-test",
                }
            ],
            "reattack_plan": [],
            "covered_targets": ["E-01"],
            "reattack_targets": [],
            "algorithm": "marl_ppo_task_scheduler",
        }

        result = run_with_algolib(
            payload,
            config={"tool_llm": {"enable": False}, "algorithm_planner": {"mode": "fixed"}},
            algolib_client=mock_client,
        )
        self.assertEqual(result["selected_algorithms"], [DEFAULT_ALGORITHM])
        self.assertEqual(result["llm_plan"]["mode"], "fixed")
        self.assertTrue(result["sensor_assignments"])
        mock_client.predict.assert_called_once()
        args, kwargs = mock_client.predict.call_args
        self.assertEqual(args[0], DEFAULT_ALGORITHM)
        self.assertIn("amos_payload", args[1])

    def test_llm_plan_selects_algorithm_and_overrides_inputs(self):
        payload = json.loads(SAMPLE.read_text(encoding="utf-8"))
        mock_client = MagicMock()
        mock_client.list_algorithms.return_value = build_local_scheduling_catalog()
        mock_client.predict.return_value = {
            "sensor_assignments": [],
            "reattack_plan": [],
            "covered_targets": [],
            "reattack_targets": [],
            "algorithm": "marl_ppo_task_scheduler",
        }
        llm = MagicMock()
        llm.chat_json.return_value = {
            "intent": "schedule",
            "algorithm_calls": [
                {
                    "algorithm_id": DEFAULT_ALGORITHM,
                    "version": "1.0.0",
                    "backend_type": "python_http_service",
                    "inputs": {"tracks": [{"hallucinated": True}]},
                    "params": {},
                    "reason": "default scheduler",
                }
            ],
            "missing_fields": [],
            "explanation": "选调度算法",
        }

        result = run_with_algolib(
            payload,
            config={
                "tool_llm": {"enable": True},
                "algorithm_planner": {"mode": "llm", "fallback_to_fixed": True},
            },
            algolib_client=mock_client,
            llm_client=llm,
        )
        self.assertEqual(result["llm_plan"]["mode"], "llm")
        self.assertEqual(result["selected_algorithms"], [DEFAULT_ALGORITHM])
        # LLM 伪造的 inputs 必须被真实 AMOS 派生输入覆盖
        call_inputs = mock_client.predict.call_args[0][1]
        self.assertIn("amos_payload", call_inputs)
        self.assertNotIn("hallucinated", str(call_inputs.get("tracks")))

    def test_agent_algolib_backend_flag(self):
        payload = json.loads(SAMPLE.read_text(encoding="utf-8"))
        fake = {
            "mission_id": "x",
            "phase": "recon",
            "jamming_level": 0.0,
            "n_targets": 1,
            "n_sensors": 1,
            "n_strike_assets": 1,
            "sensor_assignments": [],
            "reattack_plan": [],
            "covered_targets": [],
            "reattack_targets": [],
            "algorithm": "marl_ppo_task_scheduler",
            "task_schedule": {"sensor_assignments": [], "reattack_plan": []},
            "selected_algorithms": [DEFAULT_ALGORITHM],
            "llm_plan": {"mode": "fixed"},
        }
        with patch("task_scheduling_agent.algolib_runtime.run_with_algolib", return_value=fake) as mocked:
            agent = TaskSchedulingAgent(
                use_mock=True,
                config={"backend": "algolib", "tool_llm": {"enable": False}},
            )
            with patch.dict("os.environ", {"TASK_SCHEDULING_BACKEND": "algolib"}):
                out = agent.run(payload)
            mocked.assert_called_once()
            self.assertEqual(out["selected_algorithms"], [DEFAULT_ALGORITHM])


if __name__ == "__main__":
    unittest.main()
