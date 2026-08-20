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
from decision_agents.common.schemas import AgentRequest
from task_scheduling_agent.algolib_runtime import (
    DEFAULT_ALGORITHM,
    amos_to_algolib_inputs,
    build_local_scheduling_catalog,
    run_with_algolib,
)
from task_scheduling_agent.agent import TaskSchedulingAgent
from task_scheduling_agent.main import normalize_task_scheduling_result
from decision_agents.common.schemas import AgentRequest
from task_scheduling_agent.main import (
    TaskSchedulingA2AAgent,
    build_scheduler_input,
)
from tactical_intelligence_agent.llm.client import ToolLLMSettings

SAMPLE = ROOT / "examples" / "amos_schedule_inputs" / "sample_amos_request.json"


class TestAlgolibInputs(unittest.TestCase):
    def test_normalized_unready_resource_matches_shared_decision_contract(self):
        payload = {
            "workflow_id": "wf-offline-resource",
            "context": {
                "mission_input": {
                    "friendly_platforms": [
                        {
                            "platform_id": "UAV-STAGED",
                            "readiness": 0,
                            "metadata": {"sensors": ["EO/IR"]},
                        }
                    ]
                }
            },
        }

        result = normalize_task_scheduling_result(payload, {})

        self.assertEqual(result["resources"][0]["status"], "offline")
        AgentRequest.model_validate({"resources": result["resources"]})

    def test_tool_llm_settings_accept_standard_azure_environment(self):
        env = {
            "TOOL_LLM_URL": "",
            "TOOL_LLM_NAME": "",
            "API_KEY": "",
            "AZURE_OPENAI_ENDPOINT": "https://example.openai.azure.com/",
            "AZURE_OPENAI_CHAT_DEPLOYMENT": "4o-mini",
            "AZURE_OPENAI_API_KEY": "test-key",
        }
        with patch.dict("os.environ", env, clear=False):
            settings = ToolLLMSettings.from_config({})

        self.assertEqual(settings.url, env["AZURE_OPENAI_ENDPOINT"])
        self.assertEqual(settings.name, "4o-mini")
        self.assertEqual(settings.api_key, "test-key")

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

    def test_bpel_threat_result_is_converted_to_scheduler_input(self):
        payload = {
            "workflow_id": "wf-1",
            "input": {
                "threat_assessment_result": [
                    {
                        "value": {
                            "tracks": [
                                {
                                    "track_id": "TRK-1",
                                    "object_type": "ship",
                                    "lat": 22.1,
                                    "lon": 121.2,
                                    "track_quality": 0.9,
                                }
                            ],
                            "risk_assessments": [
                                {
                                    "target_id": "TRK-1",
                                    "priority": 1,
                                    "threat_score": 84.0,
                                    "probability": 0.84,
                                }
                            ],
                        }
                    }
                ]
            },
            "context": {
                "mission_input": {
                    "friendly_platforms": [
                        {
                            "platform_id": "AEW-01",
                            "platform_type": "aew",
                            "readiness": 0.8,
                            "munitions": 0,
                            "metadata": {
                                "position": {"lat": 22.0, "lon": 121.0},
                                "sensors": ["radar"],
                            },
                        }
                    ]
                }
            },
        }

        converted = build_scheduler_input(payload)

        self.assertEqual(converted["mission_id"], "wf-1")
        self.assertEqual(converted["tasks"][0]["target_id"], "TRK-1")
        self.assertEqual(converted["tasks"][0]["lat"], 22.1)
        self.assertEqual(converted["platforms"][0]["platform_id"], "AEW-01")

    def test_phase_workflow_builds_scheduler_tasks_from_mission_contacts(self):
        converted = build_scheduler_input({
            "workflow_id": "wf-decide",
            "input": {
                "mission_input": {
                    "contacts": [
                        {
                            "track_id": "HOSTILE-1",
                            "classification": "FAST_ATTACK_CRAFT",
                            "confidence": 0.91,
                            "geo": {"lat": 22.1, "lon": 121.2},
                        },
                        {
                            "track_id": "CIVILIAN-1",
                            "classification": "FISHING_VESSEL",
                            "confidence": 0.95,
                            "geo": {"lat": 22.2, "lon": 121.3},
                        },
                    ],
                    "friendly_platforms": [],
                }
            },
            "context": {"mission_input": {}},
        })

        by_target = {item["target_id"]: item for item in converted["tasks"]}
        self.assertEqual(set(by_target), {"HOSTILE-1", "CIVILIAN-1"})
        self.assertGreater(by_target["HOSTILE-1"]["threat_score"], 0.5)
        self.assertEqual(by_target["CIVILIAN-1"]["threat_score"], 0.05)


class TestRunWithAlgolib(unittest.TestCase):
    def test_normalized_output_is_a_valid_decision_agent_request(self):
        result = normalize_task_scheduling_result(
            {
                "workflow_id": "wf-contract",
                "input": {
                    "mission_input": {
                        "contacts": [
                            {
                                "track_id": "HOSTILE-1",
                                "classification": "FAST_ATTACK_CRAFT",
                                "confidence": 0.91,
                            }
                        ]
                    }
                },
            },
            {},
        )

        request = AgentRequest.model_validate(result)

        self.assertEqual(request.risk_assessments[0].target_id, "HOSTILE-1")
        self.assertEqual(request.risk_assessments[0].threat_score, 91.0)
        self.assertTrue(request.risk_assessments[0].rationale)

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

    def test_normalized_output_rejects_invented_strike_resource(self):
        payload = {
            "workflow_id": "wf-1",
            "input": {
                "threat_assessment_result": {
                    "risk_assessments": [
                        {
                            "target_id": "TRK-1",
                            "priority": 1,
                            "threat_score": 84.0,
                            "probability": 0.84,
                        }
                    ]
                }
            },
            "context": {
                "mission_input": {
                    "friendly_platforms": [
                        {
                            "platform_id": "AEW-01",
                            "platform_type": "aew",
                            "readiness": 0.8,
                            "munitions": 0,
                            "metadata": {"sensors": ["radar"]},
                        }
                    ]
                }
            },
        }
        result = normalize_task_scheduling_result(
            payload,
            {
                "sensor_assignments": [
                    {"sensor_id": "AEW-01", "target_id": "TRK-1"}
                ],
                "reattack_plan": [
                    {"asset_id": "ARTY-1", "target_id": "TRK-1"}
                ],
            },
        )

        self.assertEqual(result["scheduled_tasks"][0]["task_type"], "monitor")
        self.assertNotIn("ARTY-1", result["scheduled_tasks"][0]["assigned_resources"])
        self.assertEqual([row["id"] for row in result["resources"]], ["AEW-01"])

    def test_http_agent_uses_bpel_output_hint(self):
        scheduler = MagicMock()
        scheduler.run.return_value = {
            "mission_id": "wf-1",
            "sensor_assignments": [],
            "reattack_plan": [],
            "selected_algorithms": [DEFAULT_ALGORITHM],
            "llm_plan": {"mode": "fixed"},
        }
        agent = TaskSchedulingA2AAgent(port=10201, scheduler=scheduler)
        payload = {
            "workflow_id": "wf-1",
            "command": "allocate_tasks_and_resources",
            "output_hint": "task_scheduling_result",
            "input": {"threat_assessment_result": []},
        }

        output, _ = agent.execute_task(payload)

        self.assertIn("task_scheduling_result", output)
        scheduler.run.assert_called_once()

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

        with patch.dict("os.environ", {"ENABLE_LLM": "false"}):
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
