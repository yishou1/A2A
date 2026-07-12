from __future__ import annotations

import tempfile
import unittest

from a2a_protocol.messages import build_task_response
from supervisor import SupervisorStore
from task_pool import JsonTaskPool


def ok_resources():
    return {
        "resource_state": "ok",
        "system": {
            "cpu_percent": 20.0,
            "memory_percent": 30.0,
            "disk_percent": 40.0,
        },
        "gpu": [
            {
                "memory_total_mb": 24 * 1024,
                "memory_used_mb": 4 * 1024,
                "memory_percent": 16.7,
                "utilization_percent": 25.0,
            }
        ],
    }


class SupervisorTaskPoolTest(unittest.TestCase):
    def test_supervisor_allows_matching_agent_and_releases_on_result(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            supervisor = SupervisorStore(f"{temp_dir}/supervisor.json")
            supervisor.register_agent(
                {
                    "agent_id": "agent-recon-01",
                    "role": "recon",
                    "skills": ["scan_beach_defenses"],
                    "resources": ok_resources(),
                    "ready": True,
                    "max_concurrency": 1,
                }
            )
            pool = JsonTaskPool(
                f"{temp_dir}/task_pool.json",
                supervisor=supervisor,
                supervisor_required=True,
            )
            payload = {
                "workflow_id": "wf-1",
                "work_item": "wf-1:activity-001-scan",
                "activity_id": "activity-001-scan",
                "activity_skill": "scan_beach_defenses",
                "required_skills": ["scan_beach_defenses"],
                "resource_requirements": {
                    "min_gpu_count": 1,
                    "min_gpu_vram_gb": 8,
                    "max_cpu_percent": 80,
                },
                "output_hint": "recon_report",
            }

            task = pool.publish(payload)
            claim = pool.claim_next(
                agent_id="agent-recon-01",
                agent_skills=["scan_beach_defenses"],
            )

            self.assertTrue(claim["claimed"])
            self.assertEqual(supervisor.get_agent("agent-recon-01")["active_tasks"], 1)

            response = build_task_response(
                workflow_id="wf-1",
                work_item=payload["work_item"],
                agent="agent-recon-01",
                role="scan_beach_defenses",
                output={"recon_report": "ok"},
            )
            submitted = pool.submit_result(
                task["task_id"],
                claim_id=claim["claim_id"],
                agent_id="agent-recon-01",
                response=response,
            )

            self.assertTrue(submitted["submitted"])
            self.assertEqual(submitted["task"]["status"], "completed")
            self.assertEqual(supervisor.get_agent("agent-recon-01")["active_tasks"], 0)

    def test_submit_result_is_idempotent_for_same_claim(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            supervisor = SupervisorStore(f"{temp_dir}/supervisor.json")
            supervisor.register_agent(
                {
                    "agent_id": "agent-recon-01",
                    "role": "recon",
                    "skills": ["scan_beach_defenses"],
                    "resources": ok_resources(),
                    "ready": True,
                    "max_concurrency": 1,
                }
            )
            pool = JsonTaskPool(
                f"{temp_dir}/task_pool.json",
                supervisor=supervisor,
                supervisor_required=True,
            )
            payload = {
                "workflow_id": "wf-1",
                "work_item": "wf-1:activity-001-scan",
                "activity_skill": "scan_beach_defenses",
                "required_skills": ["scan_beach_defenses"],
                "output_hint": "recon_report",
            }

            task = pool.publish(payload)
            claim = pool.claim_next(
                agent_id="agent-recon-01",
                agent_skills=["scan_beach_defenses"],
            )
            response = build_task_response(
                workflow_id="wf-1",
                work_item=payload["work_item"],
                agent="agent-recon-01",
                role="scan_beach_defenses",
                output={"recon_report": "ok"},
            )

            first = pool.submit_result(
                task["task_id"],
                claim_id=claim["claim_id"],
                agent_id="agent-recon-01",
                response=response,
            )
            second = pool.submit_result(
                task["task_id"],
                claim_id=claim["claim_id"],
                agent_id="agent-recon-01",
                response=response,
            )

            self.assertTrue(first["submitted"])
            self.assertTrue(second["submitted"])
            self.assertTrue(second["idempotent"])
            self.assertEqual(len(second["task"]["results"]), 1)
            self.assertEqual(supervisor.get_agent("agent-recon-01")["active_tasks"], 0)

    def test_claim_rolls_back_when_required_supervisor_cannot_record_start(self):
        class FailingStartSupervisor(SupervisorStore):
            def task_started(self, agent_id: str, *, task_id: str = None, work_item: str = None):
                return {
                    "allowed": False,
                    "reason": "supervisor_unavailable",
                    "error": "boom",
                }

        with tempfile.TemporaryDirectory() as temp_dir:
            supervisor = FailingStartSupervisor(f"{temp_dir}/supervisor.json")
            supervisor.register_agent(
                {
                    "agent_id": "agent-recon-01",
                    "role": "recon",
                    "skills": ["scan_beach_defenses"],
                    "resources": ok_resources(),
                    "ready": True,
                    "max_concurrency": 1,
                }
            )
            pool = JsonTaskPool(
                f"{temp_dir}/task_pool.json",
                supervisor=supervisor,
                supervisor_required=True,
            )
            task = pool.publish(
                {
                    "workflow_id": "wf-1",
                    "work_item": "wf-1:activity-001-scan",
                    "activity_skill": "scan_beach_defenses",
                    "required_skills": ["scan_beach_defenses"],
                }
            )

            claim = pool.claim_next(
                agent_id="agent-recon-01",
                agent_skills=["scan_beach_defenses"],
            )
            stored = pool.get_task(task["task_id"])

            self.assertFalse(claim["claimed"])
            self.assertEqual(claim["reason"], "supervisor_unavailable")
            self.assertEqual(stored["status"], "pending")
            self.assertEqual(stored["claims"], [])

    def test_supervisor_rejects_critical_resource_agent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            supervisor = SupervisorStore(f"{temp_dir}/supervisor.json")
            resources = ok_resources()
            resources["resource_state"] = "critical"
            supervisor.register_agent(
                {
                    "agent_id": "agent-recon-01",
                    "skills": ["scan_beach_defenses"],
                    "resources": resources,
                    "ready": True,
                }
            )
            pool = JsonTaskPool(
                f"{temp_dir}/task_pool.json",
                supervisor=supervisor,
                supervisor_required=True,
            )
            pool.publish(
                {
                    "workflow_id": "wf-1",
                    "work_item": "wf-1:activity-001-scan",
                    "activity_skill": "scan_beach_defenses",
                    "required_skills": ["scan_beach_defenses"],
                }
            )

            claim = pool.claim_next(
                agent_id="agent-recon-01",
                agent_skills=["scan_beach_defenses"],
            )

            self.assertFalse(claim["claimed"])
            self.assertEqual(claim["reason"], "resource_critical")

    def test_task_pool_records_task_lifecycle_events(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            supervisor = SupervisorStore(f"{temp_dir}/supervisor.json")
            supervisor.register_agent(
                {
                    "agent_id": "agent-recon-01",
                    "role": "recon",
                    "skills": ["scan_beach_defenses"],
                    "resources": ok_resources(),
                    "ready": True,
                }
            )
            pool = JsonTaskPool(
                f"{temp_dir}/task_pool.json",
                supervisor=supervisor,
                supervisor_required=True,
            )
            payload = {
                "workflow_id": "wf-events",
                "work_item": "wf-events:scan",
                "activity_skill": "scan_beach_defenses",
                "required_skills": ["scan_beach_defenses"],
            }

            task = pool.publish(payload)
            claim = pool.claim_next(
                agent_id="agent-recon-01",
                agent_skills=["scan_beach_defenses"],
            )
            response = build_task_response(
                workflow_id="wf-events",
                work_item=payload["work_item"],
                agent="agent-recon-01",
                role="scan_beach_defenses",
                output={"recon_report": "ok"},
            )
            pool.submit_result(
                task["task_id"],
                claim_id=claim["claim_id"],
                agent_id="agent-recon-01",
                response=response,
            )

            event_types = [event["type"] for event in pool.list_events(workflow_id="wf-events")]

            self.assertEqual(
                event_types,
                ["task.created", "task.claimed", "task.completed"],
            )

    def test_wait_all_completion_policy_waits_for_every_claim(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            supervisor = SupervisorStore(f"{temp_dir}/supervisor.json")
            for agent_id in ["agent-recon-01", "agent-recon-02"]:
                supervisor.register_agent(
                    {
                        "agent_id": agent_id,
                        "role": "recon",
                        "skills": ["scan_beach_defenses"],
                        "resources": ok_resources(),
                        "ready": True,
                        "max_concurrency": 1,
                    }
                )
            pool = JsonTaskPool(
                f"{temp_dir}/task_pool.json",
                supervisor=supervisor,
                supervisor_required=True,
            )
            payload = {
                "workflow_id": "wf-policy",
                "work_item": "wf-policy:scan",
                "activity_skill": "scan_beach_defenses",
                "required_skills": ["scan_beach_defenses"],
                "completionPolicy": {"type": "wait_all"},
            }

            task = pool.publish(payload, max_claims=2)
            first_claim = pool.claim_next(
                agent_id="agent-recon-01",
                agent_skills=["scan_beach_defenses"],
            )
            second_claim = pool.claim_next(
                agent_id="agent-recon-02",
                agent_skills=["scan_beach_defenses"],
            )
            first_response = build_task_response(
                workflow_id="wf-policy",
                work_item=payload["work_item"],
                agent="agent-recon-01",
                role="scan_beach_defenses",
                output={"recon_report": "first"},
            )
            first_submit = pool.submit_result(
                task["task_id"],
                claim_id=first_claim["claim_id"],
                agent_id="agent-recon-01",
                response=first_response,
            )

            self.assertTrue(first_submit["submitted"])
            self.assertEqual(first_submit["task"]["status"], "pending")

            second_response = build_task_response(
                workflow_id="wf-policy",
                work_item=payload["work_item"],
                agent="agent-recon-02",
                role="scan_beach_defenses",
                output={"recon_report": "second"},
            )
            second_submit = pool.submit_result(
                task["task_id"],
                claim_id=second_claim["claim_id"],
                agent_id="agent-recon-02",
                response=second_response,
            )

            self.assertTrue(second_submit["submitted"])
            self.assertEqual(second_submit["task"]["status"], "completed")

    def test_min_results_completion_policy_is_read_from_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            supervisor = SupervisorStore(f"{temp_dir}/supervisor.json")
            for agent_id in ["agent-recon-01", "agent-recon-02"]:
                supervisor.register_agent(
                    {
                        "agent_id": agent_id,
                        "role": "recon",
                        "skills": ["scan_beach_defenses"],
                        "resources": ok_resources(),
                        "ready": True,
                        "max_concurrency": 1,
                    }
                )
            pool = JsonTaskPool(
                f"{temp_dir}/task_pool.json",
                supervisor=supervisor,
                supervisor_required=True,
            )
            payload = {
                "workflow_id": "wf-policy-min",
                "work_item": "wf-policy-min:scan",
                "activity_skill": "scan_beach_defenses",
                "required_skills": ["scan_beach_defenses"],
                "completionPolicy": {"type": "min_results", "min_results": 2},
            }

            task = pool.publish(payload, max_claims=2)
            self.assertEqual(task["completion_policy"]["type"], "min_results")
            self.assertEqual(task["min_results"], 2)

            first_claim = pool.claim_next(
                agent_id="agent-recon-01",
                agent_skills=["scan_beach_defenses"],
            )
            second_claim = pool.claim_next(
                agent_id="agent-recon-02",
                agent_skills=["scan_beach_defenses"],
            )
            for claim, agent_id in [
                (first_claim, "agent-recon-01"),
                (second_claim, "agent-recon-02"),
            ]:
                response = build_task_response(
                    workflow_id="wf-policy-min",
                    work_item=payload["work_item"],
                    agent=agent_id,
                    role="scan_beach_defenses",
                    output={"agent": agent_id},
                )
                submitted = pool.submit_result(
                    task["task_id"],
                    claim_id=claim["claim_id"],
                    agent_id=agent_id,
                    response=response,
                )

            self.assertEqual(submitted["task"]["status"], "completed")


if __name__ == "__main__":
    unittest.main()
