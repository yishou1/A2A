from __future__ import annotations

import tempfile
import unittest

from artillery_agent.main import execute_artillery_command
from assault_agent.main import execute_assault_command
from closed_loop_agent.agent_results_mapping import build_standard_results_from_context
from commander_agent.main import CommanderAgent
from execution_control_agent.association_rules import (
    discretize_situation,
    load_or_mine_rules,
    load_training_records,
    match_rules,
    mine_association_rules,
)
from execution_control_agent.execution_control_core import run_execution_control
from execution_control_agent.motion_prediction import build_track_histories, predict_tracks
from local_runtime import LocalAgentRuntime


class ExecutionControlCoreTest(unittest.TestCase):
    def test_mine_association_rules_from_fixture(self):
        records = load_training_records()
        rules = mine_association_rules(records, min_support=0.2, min_confidence=0.6)
        self.assertTrue(rules)
        strike_rules = [
            rule
            for rule in rules
            if rule["consequent"]["executor_role"] == "artillery"
            and "phase=strike" in rule["antecedent"]
        ]
        self.assertTrue(strike_rules)
        top = strike_rules[0]
        self.assertIn(top["consequent"]["action"], {"precision_strike", "area_suppression", "observe_and_hold"})

    def test_match_rules_for_strike_situation(self):
        rules = load_or_mine_rules(refresh=True)
        items = discretize_situation(
            {
                "threat_score": 0.75,
                "intel_confidence": 0.82,
                "resource_readiness": 0.81,
            },
            phase="strike",
        )
        matched = match_rules(items, rules, phase="strike")
        self.assertTrue(matched)
        self.assertEqual(matched[0]["consequent"]["executor_role"], "artillery")

    def test_motion_prediction_uses_linear_regression(self):
        results = {
            "data_fusion": {
                "output_data": {
                    "track_history": [
                        {
                            "track_id": "T-001",
                            "history": [
                                {"t": 0.0, "x": 10.0, "y": 18.0},
                                {"t": 0.1, "x": 10.4, "y": 18.6},
                                {"t": 0.2, "x": 10.9, "y": 19.1},
                                {"t": 0.3, "x": 11.3, "y": 19.7},
                                {"t": 0.4, "x": 11.8, "y": 20.2},
                            ],
                            "weapon_prep_sec": 2.0,
                            "flight_time_sec": 4.0,
                        }
                    ]
                }
            }
        }
        tracks = build_track_histories(results)
        updated, details = predict_tracks(tracks)
        self.assertEqual(len(updated), 1)
        self.assertEqual(len(details), 1)
        self.assertEqual(details[0]["model"], "linear_regression")
        self.assertAlmostEqual(details[0]["future_t"], 6.4, places=3)
        self.assertGreater(details[0]["aim_point"]["x"], 11.8)

    def test_run_execution_control_output_envelope(self):
        payload = run_execution_control(
            {
                "phase": "strike",
                "results": {
                    "threat_evaluation": {"output_data": {"priority_score": 0.75}},
                    "perception_detection": {"output_data": {"detections": [{"conf": 0.9}]}},
                    "resource_allocation": {"output_data": {"readiness": 0.85}},
                    "communication": {"output_data": {"delivery_rate": 0.9}},
                    "data_fusion": {
                        "output_data": {
                            "track_history": [
                                {
                                    "track_id": "T-001",
                                    "history": [
                                        {"t": 0.0, "x": 10.0, "y": 18.0},
                                        {"t": 0.4, "x": 11.8, "y": 20.2},
                                    ],
                                }
                            ]
                        }
                    },
                },
            }
        )
        output_data = payload["output_data"]
        for key in ("commands", "tracks", "coordination", "latency_ms", "matched_rules", "prediction_details"):
            self.assertIn(key, output_data)
        self.assertTrue(output_data["commands"])
        command = output_data["commands"][0]
        self.assertEqual(command["executor_role"], "artillery")
        self.assertIn("aim_point", command)
        self.assertIn("rule_id", command)


class ExecutionControlCommanderIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.commander = CommanderAgent(mode="local", state_dir=self.temp_dir.name, workflow_id="wf-ec")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_rule_next_step_requires_execution_control_before_artillery(self):
        context = self.commander.initial_workflow_context()
        context["recon_report"] = [{"value": "recon done", "status": "completed"}]
        step = self.commander.rule_next_step(context)
        self.assertEqual(step["role"], "execution_control")

    def test_build_task_payload_artillery_uses_execution_command(self):
        context = self.commander.initial_workflow_context()
        ec_result = run_execution_control({"phase": "strike", "results": {}})
        context["execution_control_result"] = [{"value": ec_result, "status": "completed"}]
        payload, stream = self.commander.build_task_payload("artillery", context, activatity_index=3)
        command = payload["input"]["execution_command"]
        self.assertIsInstance(command, dict)
        self.assertEqual(command["executor_role"], "artillery")
        self.assertEqual(payload["command"], command["action"])
        self.assertTrue(stream)

    def test_build_standard_results_reads_execution_control_latency(self):
        context = self.commander.initial_workflow_context()
        ec_result = run_execution_control({"phase": "strike", "results": {}})
        context["execution_control_result"] = [{"value": ec_result, "status": "completed"}]
        results = build_standard_results_from_context(context, latest_value=CommanderAgent._latest_context_value)
        self.assertEqual(
            results["execution_control"]["output_data"]["latency_ms"],
            ec_result["output_data"]["latency_ms"],
        )
        self.assertTrue(results["execution_control"]["output_data"]["commands"])


class ExecutionControlLocalRuntimeTest(unittest.TestCase):
    def test_local_runtime_execution_control_and_artillery(self):
        runtime = LocalAgentRuntime()
        ec_payload = {
            "schema_version": "1.0",
            "workflow_id": "wf-local-ec",
            "work_item": "wf-local-ec:2:execution_control",
            "command": "plan_strike_control",
            "required_skill": "plan_strike_control",
            "input": {"phase": "strike", "results": {}},
            "output_hint": "execution_control_result",
        }
        ec_response, _events = runtime.execute("execution_control", ec_payload)
        ec_value = ec_response["output"]["execution_control_result"]
        artillery_payload = {
            "schema_version": "1.0",
            "workflow_id": "wf-local-ec",
            "work_item": "wf-local-ec:3:artillery",
            "command": ec_value["output_data"]["commands"][0]["action"],
            "required_skill": "suppress_beach_sector_A",
            "input": {
                "execution_command": ec_value["output_data"]["commands"][0],
            },
            "output_hint": "strike_result",
        }
        strike_response, _stream_events = runtime.execute("artillery", artillery_payload, stream=True)
        strike_value = strike_response["output"]["strike_result"]
        self.assertEqual(strike_value["output_data"]["target_id"], ec_value["output_data"]["commands"][0]["target_id"])

    def test_artillery_and_assault_helpers_require_execution_command_fields(self):
        payload = {
            "command": "precision_strike",
            "input": {
                "execution_command": {
                    "action": "precision_strike",
                    "target_id": "T-001",
                    "aim_point": {"x": 15.8, "y": 22.6},
                    "command_id": "CMD-001",
                }
            },
        }
        artillery_result, artillery_message = execute_artillery_command(payload)
        self.assertIn("T-001", artillery_message)
        self.assertEqual(artillery_result["output_data"]["aim_point"]["x"], 15.8)

        assault_result, assault_message = execute_assault_command(
            {
                "command": "coordinated_assault",
                "input": {
                    "execution_command": {
                        "action": "coordinated_assault",
                        "target_id": "T-002",
                        "aim_point": {"x": 20.6, "y": 13.5},
                    }
                },
            }
        )
        self.assertIn("T-002", assault_message)
        self.assertEqual(assault_result["output_data"]["action"], "coordinated_assault")


if __name__ == "__main__":
    unittest.main()
