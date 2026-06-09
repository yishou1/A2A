import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from bpel_workflow import BPELWorkflowCatalog
from commander_agent.main import CommanderAgent
from scripts.demo_bpel_workflows import main as demo_bpel_workflows_main


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class BPELWorkflowTest(unittest.TestCase):
    def test_catalog_loads_named_workflow_and_builds_work_list(self):
        definition = BPELWorkflowCatalog(PROJECT_ROOT).load("beachhead_workflow")
        work_list = definition.initial_work_list("wf-bpel")

        self.assertEqual(definition.process_name, "BeachheadAssaultWorkflow")
        self.assertTrue(any(item["role"] == "recon" for item in work_list))
        self.assertTrue(any(item["role"] == "artillery" for item in work_list))
        artillery = next(item for item in work_list if item["role"] == "artillery")
        self.assertEqual(artillery["dispatch_mode"], "parallel")
        self.assertTrue(all("work_item" in item for item in work_list))
        self.assertTrue(all("activatity_id" in item for item in work_list))

    def test_catalog_selects_reinforced_workflow_by_process_name(self):
        definition = BPELWorkflowCatalog(PROJECT_ROOT).load("ReinforcedBeachheadWorkflow")
        work_list = definition.initial_work_list("wf-reinforced")

        self.assertEqual(definition.source_path.name, "reinforced_beachhead_workflow.bpel")
        self.assertEqual(definition.process_name, "ReinforcedBeachheadWorkflow")
        parallel_roles = {
            item["role"]
            for item in work_list
            if item["dispatch_mode"] == "parallel"
        }
        self.assertEqual(parallel_roles, {"recon", "artillery", "assault"})

    def test_catalog_selects_quick_strike_workflow(self):
        definition = BPELWorkflowCatalog(PROJECT_ROOT).load("QuickStrikeWorkflow")
        work_list = definition.initial_work_list("wf-quick")

        self.assertEqual(definition.source_path.name, "quick_strike_workflow.bpel")
        self.assertEqual(definition.process_name, "QuickStrikeWorkflow")
        invoked_roles = [
            item["role"]
            for item in work_list
            if item["type"] == "invoke"
        ]
        self.assertEqual(invoked_roles, ["recon", "artillery", "assault"])
        self.assertNotIn("evaluator", invoked_roles)

    def test_bpel_invokes_different_roles_in_workflow_order(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            commander = CommanderAgent(
                mode="local",
                workflow="bpel",
                workflow_file="beachhead_workflow",
                state_dir=temp_dir,
                mock_eval_score=75,
                max_workers=2,
            )
            calls = []

            def fake_delegate(role, payload, stream=False):
                calls.append(role)
                return True

            commander.delegate_task = fake_delegate
            commander.delegate_parallel_task = fake_delegate
            context = commander.run_bpel_workflow()

            self.assertEqual(calls, ["recon", "artillery", "evaluator", "assault"])
            self.assertEqual(context["workflow_status"], "completed")
            self.assertEqual(context["active_activatities"], [])
            self.assertTrue(context["work_list"])
            self.assertTrue(
                all(
                    item["status"] in {"completed", "skipped"}
                    for item in context["work_list"]
                )
            )
            self.assertNotIn("workflow_step", context)
            self.assertNotIn("last_task_id", context)

    def test_parallel_dispatch_targets_same_role_instances_concurrently(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            commander = CommanderAgent(mode="local", state_dir=temp_dir, max_workers=2)
            timing = {}

            class FakeRegistry:
                def discover_service(self, service_name, required_tags=None):
                    self.required_tags = required_tags
                    return [
                        {"ip": "10.0.0.1", "port": 8013},
                        {"ip": "10.0.0.2", "port": 8013},
                    ]

            def fake_remote_candidate(role, target, payload, stream=False):
                label = target["ip"]
                timing[f"{label}_role"] = role
                timing[f"{label}_start"] = time.perf_counter()
                time.sleep(0.2)
                timing[f"{label}_end"] = time.perf_counter()
                return True, None

            commander.mode = "remote"
            commander.registry = FakeRegistry()
            commander._delegate_remote_candidate = fake_remote_candidate

            success = commander.delegate_parallel_task(
                "artillery",
                {"work_item": "wf-bpel:artillery"},
                stream=True,
            )

            self.assertTrue(success)
            self.assertEqual(timing["10.0.0.1_role"], "artillery")
            self.assertEqual(timing["10.0.0.2_role"], "artillery")
            self.assertLess(
                max(timing["10.0.0.1_start"], timing["10.0.0.2_start"]),
                min(timing["10.0.0.1_end"], timing["10.0.0.2_end"]),
            )

    def test_bpel_flow_runs_different_activity_roles_concurrently(self):
        bpel_text = """<?xml version="1.0" encoding="UTF-8"?>
<process name="ActivityLevelFlowWorkflow">
  <sequence name="RootSequence">
    <flow name="ParallelAssessment">
      <invoke name="ReconBranch" partnerLink="ReconAgent" operation="scanBeachDefenses" inputVariable="Sector_A" outputVariable="ReconReport"/>
      <invoke name="EvalBranch" partnerLink="EvaluatorAgent" operation="evaluateStrike" inputVariable="StrikeCoordinates" outputVariable="EvalScore"/>
    </flow>
    <invoke name="AssaultAfterFlow" partnerLink="AssaultAgent" operation="captureBeachhead" inputVariable="StrikeCoordinates" outputVariable="AssaultResult"/>
  </sequence>
</process>
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            workflow_path = Path(temp_dir) / "activity_level_flow.bpel"
            workflow_path.write_text(bpel_text, encoding="utf-8")
            commander = CommanderAgent(
                mode="local",
                workflow="bpel",
                workflow_file=str(workflow_path),
                state_dir=temp_dir,
                mock_eval_score=88,
                max_workers=2,
            )
            timing = {}

            def fake_delegate(role, payload, stream=False):
                timing[f"{role}_start"] = time.perf_counter()
                time.sleep(0.2 if role in {"recon", "evaluator"} else 0.01)
                timing[f"{role}_end"] = time.perf_counter()
                commander._remember_task_response(
                    payload["work_item"],
                    {
                        "output": {
                            payload["output_hint"]: (
                                payload["input"].get("mock_eval_score")
                                if role == "evaluator"
                                else f"{role}-result"
                            )
                        }
                    },
                    role=role,
                    target="test",
                )
                return True

            commander.delegate_task = fake_delegate
            context = commander.run_bpel_workflow()

            self.assertEqual(context["workflow_status"], "completed")
            self.assertEqual(context["recon_report"], "recon-result")
            self.assertEqual(context["eval_score"], 88)
            self.assertEqual(context["assault_result"], "assault-result")
            self.assertLess(
                max(timing["recon_start"], timing["evaluator_start"]),
                min(timing["recon_end"], timing["evaluator_end"]),
            )
            self.assertGreater(
                timing["assault_start"],
                max(timing["recon_end"], timing["evaluator_end"]),
            )
            self.assertEqual(context["active_activatities"], [])

    def test_bpel_flow_rejects_parallel_output_conflicts(self):
        bpel_text = """<?xml version="1.0" encoding="UTF-8"?>
<process name="ConflictFlowWorkflow">
  <sequence name="RootSequence">
    <flow name="ConflictingFlow">
      <invoke name="ReconBranch" partnerLink="ReconAgent" operation="scanBeachDefenses" inputVariable="Sector_A" outputVariable="ReconReport"/>
      <invoke name="ArtilleryBranch" partnerLink="ArtilleryAgent" operation="suppressBeachSector" inputVariable="StrikeCoordinates" outputVariable="ReconReport"/>
    </flow>
  </sequence>
</process>
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            workflow_path = Path(temp_dir) / "conflict_flow.bpel"
            workflow_path.write_text(bpel_text, encoding="utf-8")
            commander = CommanderAgent(
                mode="local",
                workflow="bpel",
                workflow_file=str(workflow_path),
                state_dir=temp_dir,
                max_workers=2,
            )
            calls = []

            def fake_delegate(role, payload, stream=False):
                calls.append(role)
                return True

            commander.delegate_task = fake_delegate
            context = commander.run_bpel_workflow()

            self.assertEqual(context["workflow_status"], "paused")
            self.assertIn("conflicting outputVariable", context["last_error"])
            self.assertEqual(calls, [])

    def test_demo_script_runs_both_workflows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch(
                "sys.argv",
                ["demo_bpel_workflows.py", "--state-dir", temp_dir],
            ):
                with patch("builtins.print") as mock_print:
                    demo_bpel_workflows_main()

            output = "\n".join(
                " ".join(str(value) for value in call.args)
                for call in mock_print.call_args_list
            )
            self.assertIn("BeachheadAssaultWorkflow", output)
            self.assertIn("ReinforcedBeachheadWorkflow", output)
            self.assertIn("QuickStrikeWorkflow", output)
            self.assertIn("recon[single] -> artillery[parallel]", output)
            self.assertIn("recon[parallel] -> artillery[parallel]", output)
            self.assertIn("artillery[parallel] -> assault[single]", output)


if __name__ == "__main__":
    unittest.main()
