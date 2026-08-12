import json
import os
import unittest

from pathlib import Path
from unittest.mock import patch

from decision_agents.compliance_authorization.agent import ComplianceAuthorizationAgent
from decision_agents.common.algolib_client import AlgorithmLibraryError
from decision_agents.common.algolib_runtime import (
    _llm_algorithm_catalog,
    _llm_request_view,
    _select_algorithm_call,
)
from decision_agents.common.schemas import AgentRequest
from decision_agents.decision_planning.agent import DecisionPlanningAgent
from llm.client import LLMClientError


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def sample_payload(name: str) -> dict:
    return json.loads((PROJECT_ROOT / "data" / "samples" / name).read_text())


class FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class FakeLLM:
    calls = []

    def __init__(self, *_args, **_kwargs):
        pass

    def chat_json(self, *, system_prompt: str, user_prompt: str) -> dict:
        self.calls.append({"system_prompt": system_prompt, "user_prompt": user_prompt})
        if "decision_planning_agent" in system_prompt:
            algorithm_id = "decision_planning_core"
        else:
            algorithm_id = "compliance_authorization_core"
        request = json.loads(user_prompt.split("AgentRequest JSON:\n", 1)[1])
        return {
            "intent": "test",
            "algorithm_calls": [
                {
                    "algorithm_id": algorithm_id,
                    "version": "1.0.0",
                    "backend_type": "python_http_service",
                    "inputs": request,
                    "params": {},
                    "reason": "agent-specific prompt selected the core algorithm",
                }
            ],
            "missing_fields": [],
            "explanation": "ok",
        }


class DecisionAgentsAlgolibRuntimeTest(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.copy()
        os.environ["DECISION_AGENT_BACKEND"] = "algolib"
        os.environ["ENABLE_LLM"] = "true"
        os.environ["TOOL_LLM_URL"] = "http://llm.local/v1"
        os.environ["TOOL_LLM_NAME"] = "qwen3-bb"
        FakeLLM.calls = []

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    def test_decision_planning_uses_planning_prompt_and_algolib(self):
        response = self._run_with_algolib(
            DecisionPlanningAgent(),
            sample_payload("decision_planning_input.json"),
            {
                "candidate_plans": [{"id": "PLAN-1"}],
                "recommended_plan_id": "PLAN-1",
                "method": "template_generation_logistic_lstm_scoring",
            },
        )

        self.assertEqual(response.status, "completed")
        self.assertEqual(response.selected_algorithms, ["decision_planning_core"])
        self.assertIn("decision_planning_agent", FakeLLM.calls[0]["system_prompt"])
        self.assertNotIn("compliance_authorization_agent", FakeLLM.calls[0]["system_prompt"])

    def test_compliance_authorization_uses_compliance_prompt_and_algolib(self):
        response = self._run_with_algolib(
            ComplianceAuthorizationAgent(),
            sample_payload("compliance_authorization_input.json"),
            {
                "decision": "approved",
                "requires_human_approval": False,
                "selected_plan_id": "PLAN-1",
                "per_plan_results": [],
                "risk_probability": 0.1,
                "compliance_probability": 0.9,
            },
        )

        self.assertEqual(response.status, "completed")
        self.assertEqual(response.selected_algorithms, ["compliance_authorization_core"])
        self.assertIn("compliance_authorization_agent", FakeLLM.calls[0]["system_prompt"])
        self.assertNotIn("decision_planning_agent", FakeLLM.calls[0]["system_prompt"])

    def test_validated_request_replaces_llm_generated_algorithm_inputs(self):
        request = AgentRequest.model_validate(sample_payload("decision_planning_input.json"))
        algorithms = [
            {
                "algorithm_id": "decision_planning_core",
                "version": "1.0.0",
                "backend_type": "python_http_service",
            }
        ]
        llm_plan = {
            "intent": "test",
            "algorithm_calls": [
                {
                    "algorithm_id": "decision_planning_core",
                    "version": "1.0.0",
                    "backend_type": "python_http_service",
                    "inputs": {"request_id": request.request_id},
                    "params": {},
                }
            ],
            "missing_fields": [],
        }

        with patch("decision_agents.common.algolib_runtime.llm_enabled", return_value=True):
            with patch("decision_agents.common.algolib_runtime._llm_plan", return_value=llm_plan):
                call, normalized_plan = _select_algorithm_call(
                    "decision_planning_agent",
                    request,
                    algorithms,
                )

        expected_inputs = request.model_dump(mode="json")
        self.assertEqual(call.inputs, expected_inputs)
        self.assertEqual(normalized_plan["algorithm_calls"][0]["inputs"], expected_inputs)

    def test_llm_selection_view_is_compact_but_execution_keeps_full_request(self):
        request = AgentRequest.model_validate(
            sample_payload("compliance_authorization_input.json")
        )
        compact = _llm_request_view(request)

        self.assertEqual(len(compact.candidate_plans), len(request.candidate_plans))
        self.assertTrue(any(plan.actions for plan in request.candidate_plans))
        self.assertTrue(all(not plan.actions for plan in compact.candidate_plans))
        self.assertEqual(compact.authorization, request.authorization)

    def test_llm_catalog_only_contains_algorithms_allowed_for_agent(self):
        algorithms = [
            {
                "algorithm_id": "decision_planning_core",
                "version": "1.0.0",
                "backend_type": "python_http_service",
            },
            {
                "algorithm_id": "compliance_authorization_core",
                "version": "1.0.0",
                "backend_type": "python_http_service",
            },
        ]

        catalog = _llm_algorithm_catalog("compliance_authorization_agent", algorithms)

        self.assertEqual(
            [item["algorithm_id"] for item in catalog],
            ["compliance_authorization_core"],
        )

    def test_planning_ignores_llm_claim_that_candidate_plans_are_missing(self):
        payload = sample_payload("decision_planning_input.json")
        payload.pop("candidate_plans", None)
        request = AgentRequest.model_validate(payload)
        algorithms = [
            {
                "algorithm_id": "decision_planning_core",
                "version": "1.0.0",
                "backend_type": "python_http_service",
            }
        ]
        llm_plan = {
            "intent": "generate candidate plans",
            "algorithm_calls": [
                {
                    "algorithm_id": "decision_planning_core",
                    "version": "1.0.0",
                    "backend_type": "python_http_service",
                    "inputs": {},
                    "params": {},
                }
            ],
            "missing_fields": ["candidate_plans"],
        }

        with patch("decision_agents.common.algolib_runtime.llm_enabled", return_value=True):
            with patch("decision_agents.common.algolib_runtime._llm_plan", return_value=llm_plan):
                call, _normalized_plan = _select_algorithm_call(
                    "decision_planning_agent",
                    request,
                    algorithms,
                )

        self.assertEqual(call.algorithm_id, "decision_planning_core")
        self.assertEqual(call.inputs["candidate_plans"], [])

    def test_algolib_connection_failure_uses_runtime_error_code(self):
        with patch(
            "decision_agents.common.algolib_runtime.AlgorithmLibraryClient.list_algorithms",
            side_effect=AlgorithmLibraryError("offline"),
        ):
            response = DecisionPlanningAgent().handle_query(
                json.dumps(sample_payload("decision_planning_input.json"), ensure_ascii=False)
            )

        self.assertEqual(response.status, "error")
        self.assertEqual(response.error_code, "ALGORITHM_RUNTIME_ERROR")

    def test_llm_failure_uses_provider_error_code(self):
        algorithms = [
            {
                "algorithm_id": "decision_planning_core",
                "version": "1.0.0",
                "backend_type": "python_http_service",
            }
        ]
        with patch(
            "decision_agents.common.algolib_runtime.AlgorithmLibraryClient.list_algorithms",
            return_value=algorithms,
        ):
            with patch(
                "decision_agents.common.algolib_runtime._llm_plan",
                side_effect=LLMClientError("timeout"),
            ):
                response = DecisionPlanningAgent().handle_query(
                    json.dumps(sample_payload("decision_planning_input.json"), ensure_ascii=False)
                )

        self.assertEqual(response.status, "input_required")
        self.assertEqual(response.error_code, "LLM_PROVIDER_ERROR")

    def test_missing_algorithm_input_uses_input_error_code(self):
        payload = sample_payload("decision_planning_input.json")
        payload["scheduled_tasks"] = []
        with patch(
            "decision_agents.common.algolib_runtime.AlgorithmLibraryClient.list_algorithms",
            return_value=[],
        ):
            response = DecisionPlanningAgent().handle_query(
                json.dumps(payload, ensure_ascii=False)
            )

        self.assertEqual(response.status, "input_required")
        self.assertEqual(response.error_code, "ALGORITHM_INPUT_ERROR")

    def _run_with_algolib(self, agent, request_payload: dict, outputs: dict):
        algorithms = {
            "ok": True,
            "algorithms": [
                {
                    "algorithm_id": "decision_planning_core",
                    "version": "1.0.0",
                    "backend_type": "python_http_service",
                },
                {
                    "algorithm_id": "compliance_authorization_core",
                    "version": "1.0.0",
                    "backend_type": "python_http_service",
                },
            ],
        }
        run_result = {
            "ok": True,
            "algorithm_id": agent.agent_name.replace("_agent", "_core"),
            "version": "1.0.0",
            "outputs": outputs,
            "usage": {"latency_ms": 1.0},
        }
        with patch("decision_agents.common.algolib_client.httpx.get", return_value=FakeResponse(algorithms)):
            with patch("decision_agents.common.algolib_client.httpx.post", return_value=FakeResponse(run_result)):
                with patch("decision_agents.common.algolib_runtime.OpenAICompatibleClient", FakeLLM):
                    return agent.handle_query(json.dumps(request_payload, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
