"""Cross-checkpoint handoff and authorization behavior used by AMOS containers."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from algolib_bridge import AlgorithmLibraryError
from commander_agent.main import verified_stage_contexts
from execution_control_agent.algolib_runtime import run_execution_control_via_algolib
from protocol_contracts import ContractValidationError


def mission(run_id: str, previous_id: str | None = None) -> dict:
    supplemental = {}
    if previous_id:
        supplemental["workflow_result_ref"] = {
            "workflow_id": previous_id,
            "source_run_id": run_id,
            "status": "completed",
            "package_verified": True,
        }
    return {"metadata": {"run_id": run_id}, "stage_transfer": {"supplemental_inputs": supplemental}}


class FakeStore:
    def __init__(self, states: dict):
        self.states = states

    def exists(self, workflow_id: str) -> bool:
        return workflow_id in self.states

    def load(self, workflow_id: str) -> dict:
        return self.states[workflow_id]


class StageHandoffTest(unittest.TestCase):
    def test_loads_only_completed_checkpoints_from_same_run(self):
        plan_id = "amos-" + "a" * 24
        fusion_id = "amos-" + "b" * 24
        store = FakeStore({
            plan_id: {"status": "completed", "context": {
                "mission_input": mission("run-1", fusion_id), "decision_planning_result": ["verified plan"],
            }},
            fusion_id: {"status": "completed", "context": {
                "mission_input": mission("run-1"), "threat_assessment_result": ["verified threat"],
            }},
        })
        contexts = verified_stage_contexts(store, mission("run-1", plan_id))
        self.assertEqual([row.get("decision_planning_result") for row in contexts], [["verified plan"], None])
        self.assertEqual(contexts[1]["threat_assessment_result"], ["verified threat"])

    def test_rejects_cross_run_reference(self):
        plan_id = "amos-" + "a" * 24
        store = FakeStore({plan_id: {"status": "completed", "context": {"mission_input": mission("run-2")}}})
        with self.assertRaises(ContractValidationError):
            verified_stage_contexts(store, mission("run-1", plan_id))


class ExecutionAuthorizationTest(unittest.TestCase):
    def _run(self, commands: list[dict]) -> dict:
        settings = SimpleNamespace(
            enable_llm=False, default_backend_type="python_http_service", default_version="1.0.0",
        )

        class FakeClient:
            def __init__(self, _settings):
                pass

            def run_outputs(self, **_kwargs):
                return {"phase": "strike", "commands": commands, "matched_rules": []}

        with patch("execution_control_agent.algolib_runtime.AlgolibSettings.load", return_value=settings):
            with patch("execution_control_agent.algolib_runtime.AlgorithmLibraryClient", FakeClient):
                return run_execution_control_via_algolib({
                    "phase": "strike", "results": {}, "context": {
                        "authorization": {"status": "pending_review"},
                    },
                })

    def test_pending_approval_preserves_proposal_without_execution(self):
        result = self._run([{"executor_role": "artillery", "action": "precision_strike"}])
        output = result["output_data"]
        self.assertEqual(output["commands"], [])
        self.assertEqual(len(output["proposed_commands"]), 1)
        self.assertTrue(output["execution_blocked"])
        self.assertEqual(output["assessment_status"], "awaiting_authorization")
        self.assertEqual(len(output["algorithm_invocations"][0]["output"]["commands"]), 1)

    def test_missing_planner_commands_still_fails(self):
        with self.assertRaisesRegex(AlgorithmLibraryError, "commands_missing_or_empty"):
            self._run([])


if __name__ == "__main__":
    unittest.main()
