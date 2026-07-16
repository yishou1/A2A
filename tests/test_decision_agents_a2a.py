import json
import asyncio
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from bpel_workflow import BPELWorkflowCatalog
from commander_agent.main import CommanderAgent
from decision_agents.common.a2a_adapter import DecisionAlgorithmA2AAgent
from decision_agents.compliance_authorization.agent import ComplianceAuthorizationAgent
from decision_agents.decision_planning.agent import DecisionPlanningAgent


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def route_endpoint(app, path: str):
    return next(route.endpoint for route in app.routes if getattr(route, "path", None) == path)


def sample_payload(name: str) -> dict:
    return json.loads((PROJECT_ROOT / "data" / "samples" / name).read_text())


class DecisionAgentsA2ATest(unittest.TestCase):
    def test_decision_planning_generates_candidate_plans_from_external_input(self):
        response = DecisionPlanningAgent().handle_query(
            json.dumps(sample_payload("decision_planning_input.json"), ensure_ascii=False)
        )

        self.assertEqual(response.status, "completed")
        self.assertGreaterEqual(len(response.result["candidate_plans"]), 3)
        self.assertTrue(response.result["recommended_plan_id"])
        self.assertEqual(response.result["method"], "template_generation_logistic_lstm_scoring")
        self.assertGreaterEqual(len(response.result["plan_scores"]), 3)
        self.assertTrue(response.result["target_trends"])
        self.assertTrue(response.result["rag_evidence"])
        self.assertTrue(response.result["rag_answer"])
        self.assertIn("decision_planning_logistic", response.selected_algorithms)
        self.assertIn("decision_planning_lstm", response.selected_algorithms)

    def test_compliance_authorization_returns_structured_decision(self):
        response = ComplianceAuthorizationAgent().handle_query(
            json.dumps(sample_payload("compliance_authorization_input.json"), ensure_ascii=False)
        )

        self.assertEqual(response.status, "completed")
        self.assertIn(response.result["decision"], {"approved", "blocked", "review_required"})
        self.assertIn("per_plan_results", response.result)
        self.assertEqual(response.result["method"], "rule_table_rag_logistic_calibration")
        self.assertIn("risk_probability", response.result)
        self.assertIn("compliance_probability", response.result)
        self.assertIn("logistic_features", response.result)
        self.assertTrue(response.result["rag_evidence"])
        self.assertTrue(response.result["rag_answer"])
        self.assertTrue(
            any(
                violation.get("evidence_rule_ids")
                for plan in response.result["per_plan_results"]
                for violation in plan.get("violations", [])
            )
        )
        self.assertIn("compliance_authorization_logistic", response.selected_algorithms)

    def test_a2a_send_message_returns_standard_output(self):
        agent = DecisionAlgorithmA2AAgent(
            algorithm_agent=DecisionPlanningAgent(),
            name="Decision_Planning_Agent",
            description="Test planning agent.",
            role="decision_planning",
            port=10202,
        )
        payload = {
            "schema_version": "1.0",
            "workflow_id": "wf-decision-agent",
            "work_item": "wf-decision-agent:decision-planning",
            "command": "decision_planning",
            "required_skill": "decision_planning_analysis",
            "input": {"agent_request": sample_payload("decision_planning_input.json")},
            "output_hint": "decision_planning_result",
            "work_list": [
                {
                    "activatity_id": "activatity-001-decision-planning",
                    "work_item": "wf-decision-agent:decision-planning",
                    "status": "running",
                }
            ],
        }

        send_message = route_endpoint(agent.app, "/sendMessage")
        work_list = route_endpoint(agent.app, "/workflows/{workflow_id}/work-list")
        body = asyncio.run(send_message(payload, token="test-token"))
        work_list_body = asyncio.run(work_list("wf-decision-agent"))

        self.assertEqual(body["status"], "completed")
        self.assertIn("output", body)
        self.assertIn("agent_response", body["output"])
        self.assertIn("decision_planning_result", body["output"])
        self.assertTrue(body["output"]["rag_evidence"])
        self.assertTrue(body["output"]["decision_planning_result"]["candidate_plans"])
        self.assertEqual(body["agent_response"]["agent"], "decision_planning_agent")

        self.assertEqual(work_list_body["work_list"], payload["work_list"])

    def test_a2a_send_message_reports_missing_planning_input(self):
        agent = DecisionAlgorithmA2AAgent(
            algorithm_agent=DecisionPlanningAgent(),
            name="Decision_Planning_Agent",
            description="Test planning agent.",
            role="decision_planning",
            port=10202,
        )
        payload = {
            "schema_version": "1.0",
            "workflow_id": "wf-missing-input",
            "work_item": "wf-missing-input:decision-planning",
            "command": "decision_planning",
            "required_skill": "decision_planning_analysis",
            "input": {"agent_request": {"request_id": "missing-input"}},
            "output_hint": "decision_planning_result",
            "work_list": [],
        }

        send_message = route_endpoint(agent.app, "/sendMessage")
        body = asyncio.run(send_message(payload, token="test-token"))

        self.assertEqual(body["status"], "failed")
        self.assertIn("missing:scheduled_tasks", body["agent_response"]["warnings"])
        self.assertIn("missing:resources", body["agent_response"]["warnings"])

    def test_decision_support_bpel_runs_two_agents_in_local_mode(self):
        definition = BPELWorkflowCatalog(PROJECT_ROOT).load("DecisionSupportWorkflow")
        invoked_roles = [
            item["role"]
            for item in definition.initial_work_list("wf-decision-support")
            if item["type"] == "invoke"
        ]
        self.assertEqual(invoked_roles, ["decision_planning", "compliance_authorization"])

        with tempfile.TemporaryDirectory() as temp_dir:
            with contextlib.redirect_stdout(io.StringIO()):
                commander = CommanderAgent(
                    mode="local",
                    workflow="bpel",
                    workflow_file="DecisionSupportWorkflow",
                    workflow_id="wf-decision-support",
                    state_dir=temp_dir,
                    initial_context=sample_payload("decision_planning_input.json"),
                )
                context = commander.run_bpel_workflow()

        self.assertEqual(context["workflow_status"], "completed")
        self.assertTrue(context["candidate_plans"])
        self.assertTrue(context["decision_planning_result"])
        self.assertTrue(context["compliance_authorization_result"])
        self.assertIn(context["compliance_decision"], {"approved", "blocked", "review_required"})
        self.assertTrue(context["agent_results"])


if __name__ == "__main__":
    unittest.main()
