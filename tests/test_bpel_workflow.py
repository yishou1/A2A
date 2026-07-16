import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

from bpel_workflow import BPELWorkflowCatalog
from commander_agent.agent_leases import AgentLeaseManager
from commander_agent.main import CommanderAgent
from protocol_contracts import ContractValidationError
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
        self.assertEqual(artillery["required_skill"], "suppress_beach_sector_A")
        self.assertIn("suppress_beach_sector_A", artillery["required_skills"])
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
            original_single = commander.delegate_task
            original_parallel = commander.delegate_parallel_task

            def fake_delegate(role, payload, stream=False):
                calls.append(role)
                if payload.get("required_skill") == "suppress_beach_sector_A":
                    return original_parallel(role, payload, stream=stream)
                return original_single(role, payload, stream=stream)

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

    def test_bpel_required_skill_is_passed_to_task_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            commander = CommanderAgent(
                mode="local",
                workflow="bpel",
                workflow_file="beachhead_workflow",
                state_dir=temp_dir,
            )
            definition = commander.bpel_definition
            recon_activity = next(
                activity
                for activity in definition.activatities
                if activity.role == "recon"
            )

            payload, _ = commander._build_bpel_task_payload(
                recon_activity,
                commander.workflow_context,
            )

            self.assertEqual(payload["required_skill"], "scan_beach_defenses")
            self.assertEqual(payload["required_skills"], ["scan_beach_defenses"])

    def test_bpel_payload_rejects_missing_upstream_input(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            commander = CommanderAgent(
                mode="local",
                workflow="bpel",
                workflow_file="beachhead_workflow",
                state_dir=temp_dir,
            )
            replanning_activity = next(
                activity
                for activity in commander.bpel_definition.activatities
                if activity.required_skill == "analyze_and_replanning"
            )

            with self.assertRaises(ContractValidationError) as raised:
                commander._build_bpel_task_payload(
                    replanning_activity,
                    commander.workflow_context,
                )

            self.assertEqual(raised.exception.code, "MISSING_ACTIVITY_INPUT")

    def test_skill_only_bpel_can_orchestrate_without_partner_link_role(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            bpel_path = Path(temp_dir) / "skill_only.bpel"
            bpel_path.write_text(
                """
<process name="SkillOnlyWorkflow" targetNamespace="http://a2a.test/workflow">
  <variables>
    <variable name="ReconReport" type="String"/>
  </variables>
  <sequence>
    <invoke name="SkillOnlyRecon"
            requiredSkill="scan_beach_defenses"
            operation="scanBeachDefenses"
            inputVariable="Sector_A"
            outputVariable="ReconReport"/>
  </sequence>
</process>
""",
                encoding="utf-8",
            )
            commander = CommanderAgent(
                mode="local",
                workflow="bpel",
                workflow_file=str(bpel_path),
                state_dir=temp_dir,
            )
            calls = []

            def fake_delegate(dispatch_key, payload, stream=False):
                calls.append(
                    (
                        dispatch_key,
                        payload["required_skill"],
                        payload["required_skills"],
                    )
                )
                commander._remember_task_response(
                    payload["work_item"],
                    {
                        "status": "completed",
                        "output": {"recon_report": "skill-only recon ok"},
                    },
                    role=dispatch_key,
                    target="test",
                )
                return True

            commander.delegate_task = fake_delegate
            context = commander.run_bpel_workflow()

            self.assertEqual(
                calls,
                [
                    (
                        "scan_beach_defenses",
                        "scan_beach_defenses",
                        ["scan_beach_defenses"],
                    )
                ],
            )
            self.assertEqual(context["workflow_status"], "completed")
            self.assertEqual(context["recon_report"][0]["value"], "skill-only recon ok")

    def test_parallel_dispatch_targets_same_role_instances_concurrently(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            commander = CommanderAgent(mode="local", state_dir=temp_dir, max_agent_workers=2)
            timing = {}

            class FakeRegistry:
                def discover_service(self, service_name, required_tags=None):
                    self.required_tags = required_tags
                    return [
                        {"ip": "10.0.0.1", "port": 8013},
                        {"ip": "10.0.0.2", "port": 8013},
                    ]

            def fake_remote_candidate(role, target, payload, stream=False, **kwargs):
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

    def test_single_remote_dispatch_reassigns_when_agent_is_down(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            commander = CommanderAgent(mode="local", state_dir=temp_dir)
            calls = []

            class FakeRegistry:
                def __init__(self):
                    self.instances = [
                        {
                            "ip": "10.0.0.1",
                            "port": 8012,
                            "metadata": {"role": "recon", "status": "idle"},
                        },
                        {
                            "ip": "10.0.0.2",
                            "port": 8012,
                            "metadata": {"role": "recon", "status": "idle"},
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
                if target["ip"] == "10.0.0.1":
                    return False, requests.exceptions.ConnectionError("connection refused")
                return True, None

            registry = FakeRegistry()
            commander.mode = "remote"
            commander.registry = registry
            commander.lease_manager = None
            commander._delegate_remote_candidate = fake_remote_candidate

            success = commander.delegate_task(
                "recon",
                {"workflow_id": "wf-failover", "work_item": "wf-failover:recon"},
            )

            self.assertTrue(success)
            self.assertEqual(calls, ["10.0.0.1", "10.0.0.2"])
            self.assertEqual(registry.instances[0]["metadata"]["status"], "unavailable")
            self.assertEqual(
                registry.instances[0]["metadata"]["unavailable_error_code"],
                "AGENT_UNAVAILABLE",
            )
            self.assertEqual(registry.instances[1]["metadata"]["status"], "idle")

    def test_leased_remote_dispatch_reassigns_when_agent_is_down(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            commander = CommanderAgent(mode="local", state_dir=temp_dir)
            calls = []

            class FakeRegistry:
                def __init__(self):
                    self.instances = [
                        {
                            "ip": "10.0.0.1",
                            "port": 8012,
                            "metadata": {"role": "recon", "status": "idle"},
                        },
                        {
                            "ip": "10.0.0.2",
                            "port": 8012,
                            "metadata": {"role": "recon", "status": "idle"},
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
                if target["ip"] == "10.0.0.1":
                    return False, requests.exceptions.ConnectionError("connection refused")
                return True, None

            registry = FakeRegistry()
            commander.mode = "remote"
            commander.registry = registry
            commander.lease_manager = AgentLeaseManager(registry)
            commander._delegate_remote_candidate = fake_remote_candidate

            success = commander.delegate_task(
                "recon",
                {"workflow_id": "wf-failover", "work_item": "wf-failover:recon"},
            )

            self.assertTrue(success)
            self.assertEqual(calls, ["10.0.0.1", "10.0.0.2"])
            self.assertEqual(registry.instances[0]["metadata"]["status"], "idle")
            self.assertEqual(registry.instances[0]["metadata"]["circuit_state"], "closed")
            self.assertEqual(registry.instances[0]["metadata"]["circuit_failure_count"], 1)
            self.assertEqual(registry.instances[1]["metadata"]["status"], "idle")
            self.assertEqual(commander.lease_manager.list_leases(), [])

    def test_active_lease_heartbeat_loss_reassigns_running_task(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            commander = CommanderAgent(mode="local", state_dir=temp_dir)
            commander.lease_heartbeat_check_interval = 0.02
            calls = []
            late_responses = []

            class FakeRegistry:
                heartbeat_grace_seconds = 0.05

                def __init__(self):
                    now = time.time()
                    self.instances = [
                        {
                            "ip": "10.0.0.1",
                            "port": 8012,
                            "metadata": {
                                "role": "recon",
                                "status": "idle",
                                "heartbeat_ts": now,
                            },
                        },
                        {
                            "ip": "10.0.0.2",
                            "port": 8012,
                            "metadata": {
                                "role": "recon",
                                "status": "idle",
                                "heartbeat_ts": now,
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

                def find_instance(self, service_name, target):
                    for instance in self.instances:
                        if (
                            instance["ip"] == target["ip"]
                            and instance["port"] == target["port"]
                        ):
                            return instance
                    return None

                def is_instance_fresh(self, instance):
                    heartbeat_ts = float(instance["metadata"].get("heartbeat_ts", 0))
                    return (time.time() - heartbeat_ts) <= self.heartbeat_grace_seconds

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
                if target["ip"] == "10.0.0.1":
                    target["metadata"]["heartbeat_ts"] = time.time() - 10
                    time.sleep(0.2)
                    allowed = commander._lease_allows_response(
                        kwargs.get("lease"),
                        f"{target['ip']}:{target['port']}",
                        payload["work_item"],
                        role,
                    )
                    late_responses.append("accepted" if allowed else "ignored")
                    return True, None
                return True, None

            registry = FakeRegistry()
            commander.mode = "remote"
            commander.registry = registry
            commander.lease_manager = AgentLeaseManager(registry)
            commander._delegate_remote_candidate = fake_remote_candidate

            success = commander.delegate_task(
                "recon",
                {"workflow_id": "wf-heartbeat", "work_item": "wf-heartbeat:recon"},
            )

            self.assertTrue(success)
            deadline = time.time() + 1.0
            while time.time() < deadline and not late_responses:
                time.sleep(0.01)
            self.assertEqual(calls[:2], ["10.0.0.1", "10.0.0.2"])
            self.assertEqual(late_responses, ["ignored"])
            self.assertEqual(registry.instances[0]["metadata"]["status"], "idle")
            self.assertEqual(registry.instances[0]["metadata"]["circuit_state"], "closed")
            self.assertEqual(registry.instances[0]["metadata"]["circuit_failure_count"], 1)
            self.assertEqual(registry.instances[1]["metadata"]["status"], "idle")
            trace_types = {
                event["event_type"]
                for event in commander.workflow_context.get("trace", [])
            }
            self.assertIn("agent_heartbeat_lost", trace_types)
            self.assertIn("agent_failover_reassigning", trace_types)
            self.assertIn("agent_late_response_ignored", trace_types)

    def test_parallel_dispatch_continues_when_one_agent_is_down(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            commander = CommanderAgent(mode="local", state_dir=temp_dir, max_workers=2)
            calls = []

            class FakeRegistry:
                def __init__(self):
                    self.instances = [
                        {
                            "ip": "10.0.0.1",
                            "port": 8013,
                            "metadata": {"role": "artillery", "status": "idle"},
                        },
                        {
                            "ip": "10.0.0.2",
                            "port": 8013,
                            "metadata": {"role": "artillery", "status": "idle"},
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
                if target["ip"] == "10.0.0.1":
                    return False, requests.exceptions.ConnectionError("connection refused")
                return True, None

            registry = FakeRegistry()
            commander.mode = "remote"
            commander.registry = registry
            commander.lease_manager = None
            commander._delegate_remote_candidate = fake_remote_candidate

            success = commander.delegate_parallel_task(
                "artillery",
                {"workflow_id": "wf-failover", "work_item": "wf-failover:artillery"},
                stream=True,
            )

            self.assertTrue(success)
            self.assertCountEqual(calls, ["10.0.0.1", "10.0.0.2"])
            self.assertEqual(registry.instances[0]["metadata"]["status"], "unavailable")
            self.assertEqual(registry.instances[1]["metadata"]["status"], "idle")

    def test_max_agent_workers_limits_same_role_parallel_dispatch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            commander = CommanderAgent(mode="local", state_dir=temp_dir, max_agent_workers=1)
            timing = {}

            class FakeRegistry:
                def discover_service(self, service_name, required_tags=None):
                    return [
                        {"ip": "10.0.0.1", "port": 8013},
                        {"ip": "10.0.0.2", "port": 8013},
                    ]

            def fake_remote_candidate(role, target, payload, stream=False):
                label = target["ip"]
                timing[f"{label}_start"] = time.perf_counter()
                time.sleep(0.1)
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
            self.assertGreaterEqual(
                timing["10.0.0.2_start"],
                timing["10.0.0.1_end"],
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
                max_activity_workers=2,
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
            self.assertEqual(context["recon_report"][0]["value"], "recon-result")
            self.assertEqual(context["eval_score"][0]["value"], 88)
            self.assertEqual(context["assault_result"][0]["value"], "assault-result")
            self.assertLess(
                max(timing["recon_start"], timing["evaluator_start"]),
                min(timing["recon_end"], timing["evaluator_end"]),
            )
            self.assertGreater(
                timing["assault_start"],
                max(timing["recon_end"], timing["evaluator_end"]),
            )
            self.assertEqual(context["active_activatities"], [])

    def test_max_activity_workers_limits_bpel_flow_activity_concurrency(self):
        bpel_text = """<?xml version="1.0" encoding="UTF-8"?>
<process name="LimitedActivityFlowWorkflow">
  <sequence name="RootSequence">
    <flow name="ParallelAssessment">
      <invoke name="ReconBranch" partnerLink="ReconAgent" operation="scanBeachDefenses" inputVariable="Sector_A" outputVariable="ReconReport"/>
      <invoke name="EvalBranch" partnerLink="EvaluatorAgent" operation="evaluateStrike" inputVariable="StrikeCoordinates" outputVariable="EvalScore"/>
    </flow>
  </sequence>
</process>
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            workflow_path = Path(temp_dir) / "limited_activity_flow.bpel"
            workflow_path.write_text(bpel_text, encoding="utf-8")
            commander = CommanderAgent(
                mode="local",
                workflow="bpel",
                workflow_file=str(workflow_path),
                state_dir=temp_dir,
                mock_eval_score=88,
                max_activity_workers=1,
                max_agent_workers=8,
            )
            timing = {}

            def fake_delegate(role, payload, stream=False):
                timing[f"{role}_start"] = time.perf_counter()
                time.sleep(0.1)
                timing[f"{role}_end"] = time.perf_counter()
                commander._remember_task_response(
                    payload["work_item"],
                    {"output": {payload["output_hint"]: payload["input"].get("mock_eval_score", f"{role}-result")}},
                    role=role,
                    target="test",
                )
                return True

            commander.delegate_task = fake_delegate
            context = commander.run_bpel_workflow()

            self.assertEqual(context["workflow_status"], "completed")
            self.assertGreaterEqual(
                timing["evaluator_start"],
                timing["recon_end"],
            )

    def test_bpel_flow_collects_parallel_output_writers(self):
        bpel_text = """<?xml version="1.0" encoding="UTF-8"?>
<process name="CollectedOutputFlowWorkflow">
  <sequence name="RootSequence">
    <flow name="CollectedOutputFlow">
      <invoke name="ReconBranch" partnerLink="ReconAgent" operation="scanBeachDefenses" inputVariable="Sector_A" outputVariable="ReconReport"/>
      <invoke name="ArtilleryBranch" partnerLink="ArtilleryAgent" operation="suppressBeachSector" inputVariable="StrikeCoordinates" outputVariable="ReconReport"/>
    </flow>
  </sequence>
</process>
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            workflow_path = Path(temp_dir) / "collected_output_flow.bpel"
            workflow_path.write_text(bpel_text, encoding="utf-8")
            commander = CommanderAgent(
                mode="local",
                workflow="bpel",
                workflow_file=str(workflow_path),
                state_dir=temp_dir,
                max_activity_workers=2,
            )
            calls = []

            def fake_delegate(role, payload, stream=False):
                calls.append(role)
                commander._remember_task_response(
                    payload["work_item"],
                    {
                        "status": "completed",
                        "output": {payload["output_hint"]: f"{role}-output"},
                        "metrics": {"duration_ms": 12.5},
                    },
                    role=role,
                    target="test",
                )
                return True

            commander.delegate_task = fake_delegate
            context = commander.run_bpel_workflow()

            self.assertEqual(context["workflow_status"], "completed")
            self.assertCountEqual(calls, ["recon", "artillery"])
            self.assertIsInstance(context["recon_report"], list)
            self.assertEqual(len(context["recon_report"]), 2)
            self.assertCountEqual(
                [entry["role"] for entry in context["recon_report"]],
                ["recon", "artillery"],
            )
            self.assertCountEqual(
                [entry["value"] for entry in context["recon_report"]],
                ["recon-output", "artillery-output"],
            )
            self.assertTrue(
                all(entry["activity_id"] and entry["work_item"] for entry in context["recon_report"])
            )
            self.assertTrue(all(entry["created_at"] for entry in context["recon_report"]))
            self.assertTrue(all(entry["status"] == "completed" for entry in context["recon_report"]))
            self.assertTrue(all(entry["error"] is None for entry in context["recon_report"]))
            self.assertTrue(all(entry["duration_ms"] == 12.5 for entry in context["recon_report"]))

    def test_bpel_input_variable_receives_all_output_entries(self):
        bpel_text = """<?xml version="1.0" encoding="UTF-8"?>
<process name="CollectedInputWorkflow">
  <sequence name="RootSequence">
    <flow name="CollectedReconFlow">
      <invoke name="ReconA" partnerLink="ReconAgent" operation="scanBeachDefenses" inputVariable="Sector_A" outputVariable="ReconReport"/>
      <invoke name="ReconB" partnerLink="ReconAgent" operation="scanBeachDefenses" inputVariable="Sector_A" outputVariable="ReconReport"/>
    </flow>
    <invoke name="EvalCollectedRecon" partnerLink="EvaluatorAgent" operation="evaluateStrike" inputVariable="ReconReport" outputVariable="EvalScore"/>
  </sequence>
</process>
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            workflow_path = Path(temp_dir) / "collected_input_flow.bpel"
            workflow_path.write_text(bpel_text, encoding="utf-8")
            commander = CommanderAgent(
                mode="local",
                workflow="bpel",
                workflow_file=str(workflow_path),
                state_dir=temp_dir,
                mock_eval_score=91,
                max_activity_workers=2,
            )
            evaluator_inputs = []

            def fake_delegate(role, payload, stream=False):
                if role == "evaluator":
                    evaluator_inputs.append(payload["input"]["recon_report"])
                output_value = (
                    payload["input"].get("mock_eval_score")
                    if role == "evaluator"
                    else f"{payload['activatity_id']}-output"
                )
                commander._remember_task_response(
                    payload["work_item"],
                    {"output": {payload["output_hint"]: output_value}},
                    role=role,
                    target="test",
                )
                return True

            commander.delegate_task = fake_delegate
            context = commander.run_bpel_workflow()

            self.assertEqual(context["workflow_status"], "completed")
            self.assertEqual(len(context["recon_report"]), 2)
            self.assertEqual(len(evaluator_inputs), 1)
            self.assertEqual(len(evaluator_inputs[0]), 2)
            self.assertCountEqual(
                [entry["value"] for entry in evaluator_inputs[0]],
                [entry["value"] for entry in context["recon_report"]],
            )
            self.assertEqual(context["eval_score"][0]["value"], 91)

    def test_bpel_flow_dag_schedules_cross_branch_input_dependencies(self):
        bpel_text = """<?xml version="1.0" encoding="UTF-8"?>
<process name="DependentFlowWorkflow">
  <sequence name="RootSequence">
    <flow name="DependentFlow">
      <invoke name="ReconBranch" partnerLink="ReconAgent" operation="scanBeachDefenses" inputVariable="Sector_A" outputVariable="ReconReport"/>
      <invoke name="EvalBranch" partnerLink="EvaluatorAgent" operation="evaluateStrike" inputVariable="ReconReport" outputVariable="EvalScore"/>
    </flow>
  </sequence>
</process>
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            workflow_path = Path(temp_dir) / "dependent_flow.bpel"
            workflow_path.write_text(bpel_text, encoding="utf-8")
            commander = CommanderAgent(
                mode="local",
                workflow="bpel",
                workflow_file=str(workflow_path),
                state_dir=temp_dir,
                max_activity_workers=2,
            )
            calls = []
            timing = {}
            evaluator_inputs = []

            def fake_delegate(role, payload, stream=False):
                calls.append(role)
                timing[f"{role}_start"] = time.perf_counter()
                if role == "recon":
                    time.sleep(0.1)
                if role == "evaluator":
                    evaluator_inputs.append(payload["input"]["recon_report"])
                timing[f"{role}_end"] = time.perf_counter()
                commander._remember_task_response(
                    payload["work_item"],
                    {
                        "status": "completed",
                        "output": {
                            payload["output_hint"]: (
                                payload["input"].get("mock_eval_score")
                                if role == "evaluator"
                                else "recon-output"
                            )
                        },
                        "metrics": {"duration_ms": 7.0},
                    },
                    role=role,
                    target="test",
                )
                return True

            commander.delegate_task = fake_delegate
            context = commander.run_bpel_workflow()

            self.assertEqual(context["workflow_status"], "completed")
            self.assertEqual(calls, ["recon", "evaluator"])
            self.assertGreaterEqual(timing["evaluator_start"], timing["recon_end"])
            self.assertEqual(len(evaluator_inputs), 1)
            self.assertEqual(evaluator_inputs[0][0]["value"], "recon-output")
            self.assertEqual(context["eval_score"][0]["status"], "completed")
            self.assertEqual(context["eval_score"][0]["duration_ms"], 7.0)

    def test_bpel_flow_respects_explicit_depends_on(self):
        bpel_text = """<?xml version="1.0" encoding="UTF-8"?>
<process name="ExplicitDependencyWorkflow">
  <sequence name="RootSequence">
    <flow name="ExplicitDependencyFlow">
      <invoke name="ReconBranch" partnerLink="ReconAgent" operation="scanBeachDefenses" inputVariable="Sector_A" outputVariable="ReconReport"/>
      <invoke name="EvalBranch" partnerLink="EvaluatorAgent" operation="evaluateStrike" inputVariable="StrikeCoordinates" outputVariable="EvalScore" dependsOn="ReconBranch"/>
    </flow>
  </sequence>
</process>
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            workflow_path = Path(temp_dir) / "explicit_dependency_flow.bpel"
            workflow_path.write_text(bpel_text, encoding="utf-8")
            commander = CommanderAgent(
                mode="local",
                workflow="bpel",
                workflow_file=str(workflow_path),
                state_dir=temp_dir,
                mock_eval_score=86,
                max_activity_workers=2,
            )
            calls = []
            timing = {}

            eval_item = next(item for item in commander.workflow_context["work_list"] if item["name"] == "EvalBranch")
            self.assertEqual(eval_item["depends_on"], ["ReconBranch"])

            def fake_delegate(role, payload, stream=False):
                calls.append(role)
                timing[f"{role}_start"] = time.perf_counter()
                if role == "recon":
                    time.sleep(0.1)
                timing[f"{role}_end"] = time.perf_counter()
                commander._remember_task_response(
                    payload["work_item"],
                    {
                        "status": "completed",
                        "output": {
                            payload["output_hint"]: (
                                payload["input"].get("mock_eval_score")
                                if role == "evaluator"
                                else "recon-output"
                            )
                        },
                    },
                    role=role,
                    target="test",
                )
                return True

            commander.delegate_task = fake_delegate
            context = commander.run_bpel_workflow()

            self.assertEqual(context["workflow_status"], "completed")
            self.assertEqual(calls, ["recon", "evaluator"])
            self.assertGreaterEqual(timing["evaluator_start"], timing["recon_end"])
            self.assertEqual(context["eval_score"][0]["value"], 86)
            flow_started = next(
                event
                for event in context["trace"]
                if event["event_type"] == "flow_activity_started"
            )
            self.assertEqual(flow_started["execution_mode"], "dag")
            self.assertTrue(
                any(
                    edge.get("source") == "dependsOn"
                    and edge.get("reference") == "ReconBranch"
                    for edge in flow_started["dependencies"]
                )
            )

    def test_bpel_resume_recovers_interrupted_running_activity(self):
        bpel_text = """<?xml version="1.0" encoding="UTF-8"?>
<process name="InterruptedResumeWorkflow">
  <sequence name="RootSequence">
    <invoke name="ReconBranch" partnerLink="ReconAgent" operation="scanBeachDefenses" inputVariable="Sector_A" outputVariable="ReconReport"/>
  </sequence>
</process>
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            workflow_path = Path(temp_dir) / "interrupted_resume.bpel"
            workflow_path.write_text(bpel_text, encoding="utf-8")
            workflow_id = "wf-interrupted"
            seed = CommanderAgent(
                mode="local",
                workflow="bpel",
                workflow_file=str(workflow_path),
                workflow_id=workflow_id,
                state_dir=temp_dir,
            )
            context = seed.workflow_context
            recon_item = next(item for item in context["work_list"] if item["role"] == "recon")
            recon_item["status"] = "running"
            recon_item["started_at"] = "2026-01-01T00:00:00+00:00"
            context["active_activatities"] = [recon_item["activatity_id"]]
            context["active_activities"] = list(context["active_activatities"])
            seed.state_store.save(
                workflow_id,
                {
                    "workflow_id": workflow_id,
                    "workflow": "bpel",
                    "mode": "local",
                    "status": "paused",
                    "context": context,
                },
            )

            resumed = CommanderAgent(
                mode="local",
                workflow="bpel",
                workflow_file=str(workflow_path),
                workflow_id=workflow_id,
                state_dir=temp_dir,
                resume=True,
            )

            recovered_item = next(item for item in resumed.workflow_context["work_list"] if item["role"] == "recon")
            self.assertEqual(recovered_item["status"], "pending")
            self.assertEqual(resumed.workflow_context["active_activatities"], [])
            self.assertIn("Recovered interrupted running activity", recovered_item["error"])
            self.assertTrue(
                any(
                    event["event_type"] == "interrupted_activities_recovered"
                    for event in resumed.workflow_context["trace"]
                )
            )

    def test_bpel_resume_clears_failed_node_downstream_outputs(self):
        bpel_text = """<?xml version="1.0" encoding="UTF-8"?>
<process name="DagCleanupResumeWorkflow">
  <sequence name="RootSequence">
    <flow name="DependentFlow">
      <invoke name="ReconBranch" partnerLink="ReconAgent" operation="scanBeachDefenses" inputVariable="Sector_A" outputVariable="ReconReport"/>
      <invoke name="EvalBranch" partnerLink="EvaluatorAgent" operation="evaluateStrike" inputVariable="ReconReport" outputVariable="EvalScore"/>
    </flow>
    <invoke name="AssaultAfterEval" partnerLink="AssaultAgent" operation="captureBeachhead" inputVariable="EvalScore" outputVariable="AssaultResult"/>
  </sequence>
</process>
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            workflow_path = Path(temp_dir) / "dag_cleanup_resume.bpel"
            workflow_path.write_text(bpel_text, encoding="utf-8")
            workflow_id = "wf-dag-cleanup"
            seed = CommanderAgent(
                mode="local",
                workflow="bpel",
                workflow_file=str(workflow_path),
                workflow_id=workflow_id,
                state_dir=temp_dir,
                mock_eval_score=90,
            )
            context = seed.workflow_context
            by_name = {
                activity.name: activity
                for activity in seed.bpel_definition.activatities_by_id.values()
            }
            by_id = {
                item["activatity_id"]: item
                for item in context["work_list"]
            }
            root_id = by_name["RootSequence"].activatity_id
            flow_id = by_name["DependentFlow"].activatity_id
            recon_id = by_name["ReconBranch"].activatity_id
            eval_id = by_name["EvalBranch"].activatity_id
            assault_id = by_name["AssaultAfterEval"].activatity_id
            by_id[root_id]["status"] = "failed"
            by_id[flow_id]["status"] = "failed"
            by_id[recon_id]["status"] = "completed"
            by_id[eval_id]["status"] = "failed"
            by_id[assault_id]["status"] = "completed"
            context["workflow_status"] = "paused"
            context["recon_report"] = [
                seed._make_context_entry(
                    "old-recon",
                    activity_id=recon_id,
                    work_item=by_id[recon_id]["work_item"],
                    role="recon",
                    output={"recon_report": "old-recon"},
                )
            ]
            context["eval_score"] = [
                seed._make_context_entry(
                    10,
                    activity_id=eval_id,
                    work_item=by_id[eval_id]["work_item"],
                    role="evaluator",
                    output={"eval_score": 10},
                    status="failed",
                    error="old evaluator failure",
                )
            ]
            context["assault_result"] = [
                seed._make_context_entry(
                    "stale-assault",
                    activity_id=assault_id,
                    work_item=by_id[assault_id]["work_item"],
                    role="assault",
                    output={"assault_result": "stale-assault"},
                )
            ]
            context["agent_results"] = {
                by_id[eval_id]["work_item"]: {"output": {"eval_score": 10}},
                by_id[assault_id]["work_item"]: {"output": {"assault_result": "stale-assault"}},
            }
            seed.state_store.save(
                workflow_id,
                {
                    "workflow_id": workflow_id,
                    "workflow": "bpel",
                    "mode": "local",
                    "status": "paused",
                    "context": context,
                },
            )

            resumed = CommanderAgent(
                mode="local",
                workflow="bpel",
                workflow_file=str(workflow_path),
                workflow_id=workflow_id,
                state_dir=temp_dir,
                resume=True,
                mock_eval_score=90,
            )
            calls = []

            def fake_delegate(role, payload, stream=False):
                calls.append(role)
                output_value = payload["input"].get("mock_eval_score", f"new-{role}")
                commander_response = {
                    "status": "completed",
                    "output": {payload["output_hint"]: output_value},
                    "metrics": {"duration_ms": 3.0},
                }
                resumed._remember_task_response(
                    payload["work_item"],
                    commander_response,
                    role=role,
                    target="test",
                )
                return True

            resumed.delegate_task = fake_delegate
            final_context = resumed.run_bpel_workflow()

            self.assertEqual(final_context["workflow_status"], "completed")
            self.assertEqual(calls, ["evaluator", "assault"])
            self.assertEqual([entry["value"] for entry in final_context["recon_report"]], ["old-recon"])
            self.assertEqual([entry["value"] for entry in final_context["eval_score"]], [90])
            self.assertEqual([entry["value"] for entry in final_context["assault_result"]], ["new-assault"])
            self.assertEqual(
                final_context["agent_results"][by_id[eval_id]["work_item"]]["output"]["eval_score"],
                90,
            )
            self.assertTrue(
                any(
                    event["event_type"] == "dag_resume_cleanup"
                    and eval_id in event["affected_activity_ids"]
                    and assault_id in event["affected_activity_ids"]
                    for event in final_context["trace"]
                )
            )

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
