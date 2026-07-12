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


if __name__ == "__main__":
    unittest.main()
