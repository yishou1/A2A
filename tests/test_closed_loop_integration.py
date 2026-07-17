from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests
from fastapi.testclient import TestClient

from a2a_protocol.messages import is_success_response
from closed_loop_agent.main import ClosedLoopAgent, build_closed_loop_arguments
from commander_agent.main import CommanderAgent
from local_runtime import LocalAgentRuntime


def sample_closed_loop_result() -> dict:
    return {
        "task_type": "closed_loop_optimization",
        "input_data": {"cycles": 1, "target_count": 2},
        "output_data": {
            "execution_control": {"processed_targets": 2},
            "effect_assessment": {},
            "closed_loop_optimization": {"cycles_completed": 1},
            "requirement_report": {"status": "ok"},
            "meets_requirements": True,
        },
        "accuracy": 0.9,
        "latency": 0.01,
    }


def _agent_with_temp_store(port: int = 8016) -> tuple[ClosedLoopAgent, tempfile.TemporaryDirectory]:
    temp_dir = tempfile.TemporaryDirectory()
    agent = ClosedLoopAgent(
        port=port,
        idempotency_db_path=str(Path(temp_dir.name) / "agent.db"),
    )
    return agent, temp_dir


class ClosedLoopIntegrationTest(unittest.TestCase):
    def test_build_closed_loop_arguments_reads_input_and_passthrough(self):
        payload = {
            "input": {"cycles": 2, "target_count": 3},
            "seed": 7,
            "results": {"recon": {"output_data": {"report": "ok"}}},
        }
        arguments = build_closed_loop_arguments(payload)
        self.assertEqual(arguments["cycles"], 2)
        self.assertEqual(arguments["target_count"], 3)
        self.assertEqual(arguments["seed"], 7)
        self.assertIn("recon", arguments["results"])

    def test_commander_build_task_payload_matches_standard_envelope(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            commander = CommanderAgent(mode="local", state_dir=temp_dir, workflow_id="wf-closed-loop")
            context = commander.initial_workflow_context()
            context["last_work_item"] = "wf-closed-loop:4:assault"
            context["assault_result"] = [
                {
                    "value": "Assault unit captured the beachhead.",
                    "activity_id": "activatity-004-assault",
                    "work_item": "wf-closed-loop:4:assault",
                    "role": "assault",
                    "status": "completed",
                }
            ]

            payload, stream = commander.build_task_payload("closed_loop", context, activatity_index=5)

            self.assertFalse(stream)
            self.assertEqual(payload["workflow_id"], "wf-closed-loop")
            self.assertEqual(payload["work_item"], "wf-closed-loop:5:closed_loop")
            self.assertEqual(payload["parent_work_item"], "wf-closed-loop:4:assault")
            self.assertEqual(payload["activatity_index"], 5)
            self.assertEqual(payload["activatity_role"], "closed_loop")
            self.assertEqual(payload["command"], "closed_loop_optimization")
            self.assertEqual(payload["output_hint"], "closed_loop_result")
            self.assertIn("work_list", payload)
            self.assertIn("context", payload)
            results = payload["input"]["results"]
            self.assertIn("perception_detection", results)
            self.assertIn("threat_evaluation", results)
            self.assertIn("execution_control", results)
            self.assertIn("communication", results)
            self.assertEqual(
                results["assault"]["output_data"]["result"],
                "Assault unit captured the beachhead.",
            )
            self.assertEqual(
                results["execution_control"]["output_data"]["assault_summary"],
                "Assault unit captured the beachhead.",
            )

    @patch("closed_loop_agent.main._closed_loop_optimization", return_value=sample_closed_loop_result())
    def test_closed_loop_agent_send_message_returns_standard_response(self, _mock_opt):
        agent, temp_dir = _agent_with_temp_store(8016)
        self.addCleanup(temp_dir.cleanup)
        client = TestClient(agent.app)
        payload = {
            "schema_version": "1.0",
            "workflow_id": "wf-1",
            "work_item": "wf-1:5:closed_loop",
            "command": "closed_loop_optimization",
            "required_skill": "closed_loop_optimization",
            "input": {"cycles": 1, "target_count": 2},
            "output_hint": "closed_loop_result",
            "work_list": [
                {
                    "activatity_id": "activatity-005-closed-loop",
                    "work_item": "wf-1:5:closed_loop",
                    "status": "running",
                }
            ],
        }

        response = client.post(
            "/sendMessage",
            json=payload,
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(is_success_response(body))
        self.assertEqual(body["workflow_id"], "wf-1")
        self.assertEqual(body["work_item"], "wf-1:5:closed_loop")
        self.assertEqual(body["role"], "closed_loop")
        self.assertIn("closed_loop_result", body["output"])
        self.assertIn("metrics", body)
        self.assertEqual(body["metrics"]["duration_ms"], body["metrics"]["latency_ms"])

    @patch("closed_loop_agent.main._closed_loop_optimization", return_value=sample_closed_loop_result())
    def test_closed_loop_agent_reuses_work_item_cache(self, mock_opt):
        agent, temp_dir = _agent_with_temp_store(8016)
        self.addCleanup(temp_dir.cleanup)
        client = TestClient(agent.app)
        payload = {
            "schema_version": "1.0",
            "workflow_id": "wf-cache",
            "work_item": "wf-cache:5:closed_loop",
            "command": "closed_loop_optimization",
            "required_skill": "closed_loop_optimization",
            "input": {"cycles": 1},
            "output_hint": "closed_loop_result",
        }

        first = client.post(
            "/sendMessage",
            json=payload,
            headers={"Authorization": "Bearer test-token"},
        ).json()
        second = client.post(
            "/sendMessage",
            json=payload,
            headers={"Authorization": "Bearer test-token"},
        ).json()

        self.assertFalse(first.get("cached"))
        self.assertTrue(second.get("cached"))
        self.assertEqual(mock_opt.call_count, 1)

    def test_closed_loop_agent_unsupported_command_returns_business_error(self):
        agent, temp_dir = _agent_with_temp_store(8016)
        self.addCleanup(temp_dir.cleanup)
        send_message_endpoint = next(
            route.endpoint for route in agent.app.routes if getattr(route, "path", None) == "/sendMessage"
        )
        response = asyncio.run(
            send_message_endpoint(
                {
                    "schema_version": "1.0",
                    "workflow_id": "wf-invalid-cmd",
                    "work_item": "wf-invalid-cmd:5:closed_loop",
                    "command": "invalid_command",
                    "required_skill": "closed_loop_optimization",
                    "input": {"cycles": 1},
                    "output_hint": "closed_loop_result",
                },
                token="test-token",
            )
        )
        self.assertEqual(response["status"], "failed")
        self.assertEqual(response["error_code"], "AGENT_BUSINESS_ERROR")
        self.assertIn("Unsupported command", response["error"])

    def test_closed_loop_failover_reassigns_to_backup_agent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            commander = CommanderAgent(mode="local", state_dir=temp_dir)
            calls = []

            class FakeRegistry:
                def __init__(self):
                    self.instances = [
                        {
                            "ip": "10.0.0.21",
                            "port": 8016,
                            "metadata": {
                                "role": "closed_loop",
                                "status": "idle",
                                "skills": "closed_loop_optimization",
                            },
                        },
                        {
                            "ip": "10.0.0.22",
                            "port": 8016,
                            "metadata": {
                                "role": "closed_loop",
                                "status": "idle",
                                "skills": "closed_loop_optimization",
                            },
                        },
                    ]

                def discover_service(self, service_name, required_tags=None):
                    return [
                        instance
                        for instance in self.instances
                        if all(
                            instance["metadata"].get(key) == value
                            for key, value in (required_tags or {}).items()
                        )
                    ]

                def update_instance_metadata(
                    self,
                    service_name,
                    instance,
                    metadata_updates=None,
                    remove_keys=None,
                ):
                    instance["metadata"].update(metadata_updates or {})
                    for key in remove_keys or []:
                        instance["metadata"].pop(key, None)
                    return instance["metadata"]

            def fake_remote_candidate(role, target, payload, stream=False, **kwargs):
                calls.append(target["ip"])
                if target["ip"] == "10.0.0.21":
                    return False, requests.exceptions.ConnectionError("connection refused")
                return True, None

            registry = FakeRegistry()
            commander.mode = "remote"
            commander.registry = registry
            commander.lease_manager = None
            commander._delegate_remote_candidate = fake_remote_candidate

            success = commander.delegate_task(
                "closed_loop",
                {
                    "workflow_id": "wf-closed-loop-failover",
                    "work_item": "wf-closed-loop-failover:5:closed_loop",
                    "command": "closed_loop_optimization",
                    "required_skill": "closed_loop_optimization",
                    "required_skills": ["closed_loop_optimization"],
                    "output_hint": "closed_loop_result",
                },
            )

            self.assertTrue(success)
            self.assertEqual(calls, ["10.0.0.21", "10.0.0.22"])
            self.assertEqual(registry.instances[0]["metadata"]["status"], "unavailable")

    @patch("closed_loop_agent.closed_loop_core._closed_loop_optimization", return_value=sample_closed_loop_result())
    def test_local_runtime_closed_loop_returns_standard_response(self, _mock_opt):
        runtime = LocalAgentRuntime()
        response, events = runtime.execute(
            "closed_loop",
            {
                "schema_version": "1.0",
                "workflow_id": "wf-local",
                "work_item": "wf-local:5:closed_loop",
                "command": "closed_loop_optimization",
                "required_skill": "closed_loop_optimization",
                "input": {"cycles": 1, "target_count": 2},
                "output_hint": "closed_loop_result",
            },
            stream=False,
        )
        self.assertEqual(response["role"], "closed_loop")
        self.assertTrue(is_success_response(response))
        self.assertIn("closed_loop_result", response["output"])


class AgentResultsMappingTest(unittest.TestCase):
    def test_mission_vector_from_results_doc_example(self):
        from closed_loop_agent.agent_results_mapping import mission_vector_from_results

        results = {
            "perception_detection": {"output_data": {"detections": [{"conf": 0.9}, {"conf": 0.8}]}},
            "resource_allocation": {"output_data": {"readiness": 0.75, "supply_pressure": 0.55}},
            "execution_control": {"output_data": {"latency_ms": 300}},
            "threat_evaluation": {"output_data": {"ranked_targets": [{"score": 0.7}, {"score": 0.6}]}},
            "communication": {"output_data": {"delivery_rate": 0.92}},
            "damage_confirmation": {"output_data": {"engaged_targets": 40, "confirmed_destroyed": 30}},
        }
        vector = mission_vector_from_results(results)
        self.assertEqual(vector, [0.75, 0.75, 0.85, 0.85, 0.65, 0.55, 0.92])

    def test_mission_features_uses_communication_and_execution_control(self):
        from closed_loop_agent.closed_loop_core import _mission_features

        targets = [
            {
                "detection_confidence": 0.9,
                "threat_score": 0.7,
                "ammo_need": 0.5,
                "damage_probability": 0.6,
            }
        ]
        results = {
            "execution_control": {"output_data": {"latency_ms": 300}},
            "communication": {"output_data": {"delivery_rate": 0.92}},
        }
        vector = _mission_features(targets, [0.6], results)
        self.assertEqual(vector[2], 0.85)
        self.assertEqual(vector[6], 0.92)


if __name__ == "__main__":
    unittest.main()
