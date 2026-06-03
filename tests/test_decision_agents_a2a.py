import json
import tempfile
import unittest
from pathlib import Path

import anyio
import httpx

from bpel_workflow import BPELWorkflowCatalog
from commander_agent.main import CommanderAgent
from decision_agents.a2a_adapter import DecisionAlgorithmA2AAgent
from decision_agents.agents import (
    ComplianceAuthorizationAgent,
    DecisionPlanningAgent,
    TrackThreatAgent,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DecisionAgentsA2ATest(unittest.TestCase):
    def test_track_threat_onnx_falls_back_to_deterministic_algorithm(self):
        payload = json.loads((PROJECT_ROOT / "data/samples/track_threat_input.json").read_text())
        payload["algorithm_id"] = "track_threat_onnx"

        response = TrackThreatAgent().handle_query(json.dumps(payload))

        self.assertEqual(response.status, "completed")
        self.assertEqual(response.selected_algorithms[0], "track_threat_onnx")
        self.assertIn("track_threat_large", response.selected_algorithms)
        self.assertTrue(any(item.startswith("onnx_fallback:") for item in response.warnings))
        self.assertTrue(response.result["tracks"])

    def test_a2a_send_message_runs_algorithm_and_exposes_work_list(self):
        agent = DecisionAlgorithmA2AAgent(
            algorithm_agent=TrackThreatAgent(),
            name="Track_Threat_Agent",
            description="Test track threat agent.",
            role="track_threat",
            port=10201,
        )
        payload = {
            "workflow_id": "wf-decision-agent",
            "work_item": "wf-decision-agent:track-threat",
            "command": "track_threat_analysis",
            "input": {
                "agent_request": json.loads(
                    (PROJECT_ROOT / "data/samples/track_threat_input.json").read_text()
                )
            },
            "work_list": [
                {
                    "activatity_id": "activatity-001-track-threat",
                    "work_item": "wf-decision-agent:track-threat",
                    "status": "running",
                }
            ],
        }

        async def call_app():
            transport = httpx.ASGITransport(app=agent.app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
                send_response = await client.post(
                    "/sendMessage",
                    json=payload,
                    headers={"Authorization": "Bearer test-token"},
                )
                work_list_response = await client.get(
                    "/workflows/wf-decision-agent/work-list"
                )
                return send_response, work_list_response

        response, work_list_response = anyio.run(call_app)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "Completed")
        self.assertEqual(body["agent_response"]["agent"], "track_threat_agent")
        self.assertTrue(body["result"]["tracks"])

        self.assertEqual(work_list_response.status_code, 200)
        self.assertEqual(work_list_response.json()["work_list"], payload["work_list"])

    def test_decision_support_bpel_runs_three_agents_in_local_mode(self):
        definition = BPELWorkflowCatalog(PROJECT_ROOT).load("DecisionSupportWorkflow")
        invoked_roles = [
            item["role"]
            for item in definition.initial_work_list("wf-decision-support")
            if item["type"] == "invoke"
        ]
        self.assertEqual(
            invoked_roles,
            ["track_threat", "decision_planning", "compliance_authorization"],
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            commander = CommanderAgent(
                mode="local",
                workflow="bpel",
                workflow_file="DecisionSupportWorkflow",
                workflow_id="wf-decision-support",
                state_dir=temp_dir,
            )
            context = commander.run_bpel_workflow()

        self.assertEqual(context["workflow_status"], "completed")
        self.assertTrue(context["tracks"])
        self.assertTrue(context["risk_assessments"])
        self.assertTrue(context["candidate_plans"])
        self.assertIn(
            context["compliance_decision"],
            {"approved", "blocked", "review_required"},
        )

    def test_other_decision_agents_return_structured_results(self):
        planning = DecisionPlanningAgent().handle_query(
            (PROJECT_ROOT / "data/samples/decision_planning_input.json").read_text()
        )
        compliance = ComplianceAuthorizationAgent().handle_query(
            (PROJECT_ROOT / "data/samples/compliance_authorization_input.json").read_text()
        )

        self.assertEqual(planning.status, "completed")
        self.assertGreaterEqual(len(planning.result["candidate_plans"]), 3)
        self.assertEqual(compliance.status, "completed")
        self.assertEqual(compliance.result["decision"], "review_required")


if __name__ == "__main__":
    unittest.main()
