from __future__ import annotations

import unittest

from commander_agent.agent_leases import AgentLeaseManager


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
                "port": 8013,
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


class AgentLeaseManagerTest(unittest.TestCase):
    def test_agent_is_busy_until_lease_is_released(self):
        registry = FakeRegistry()
        leases = AgentLeaseManager(registry)

        first = leases.acquire_one("recon", "wf-1", "wf-1:1:recon")
        second = leases.acquire_one("recon", "wf-2", "wf-2:1:recon")
        unavailable = leases.acquire_one("recon", "wf-3", "wf-3:1:recon")

        self.assertEqual(first.instance_key, "10.0.0.1:8012")
        self.assertEqual(second.instance_key, "10.0.0.2:8013")
        self.assertIsNone(unavailable)
        self.assertEqual(first.target["metadata"]["status"], "busy")
        self.assertEqual(len(leases.list_leases()), 2)

        leases.release(first)
        self.assertNotIn("lease_workflow_id", first.target["metadata"])

        replacement = leases.acquire_one("recon", "wf-3", "wf-3:1:recon")
        self.assertEqual(replacement.instance_key, "10.0.0.1:8012")

    def test_agent_with_capacity_can_hold_multiple_slot_leases(self):
        registry = FakeRegistry()
        registry.instances = [
            {
                "ip": "10.0.0.10",
                "port": 8012,
                "metadata": {
                    "role": "recon",
                    "status": "idle",
                    "max_concurrent_tasks": "2",
                    "active_tasks": "0",
                },
            }
        ]
        leases = AgentLeaseManager(registry)

        first = leases.acquire_one("recon", "wf-1", "wf-1:1:recon")
        second = leases.acquire_one("recon", "wf-2", "wf-2:1:recon")
        saturated = leases.acquire_one("recon", "wf-3", "wf-3:1:recon")

        self.assertEqual(first.instance_key, "10.0.0.10:8012")
        self.assertEqual(second.instance_key, "10.0.0.10:8012")
        self.assertEqual(first.slot_id, 0)
        self.assertEqual(second.slot_id, 1)
        self.assertIsNone(saturated)
        self.assertEqual(registry.instances[0]["metadata"]["active_tasks"], "2")
        self.assertEqual(
            registry.instances[0]["metadata"]["task_execution_status"],
            "saturated",
        )
        self.assertEqual(len(leases.list_leases()), 2)

        leases.release(first)
        self.assertEqual(registry.instances[0]["metadata"]["status"], "busy")
        self.assertEqual(registry.instances[0]["metadata"]["active_tasks"], "1")
        self.assertEqual(
            registry.instances[0]["metadata"]["task_execution_status"],
            "busy",
        )

        leases.release(second)
        self.assertEqual(registry.instances[0]["metadata"]["status"], "idle")
        self.assertEqual(registry.instances[0]["metadata"]["active_tasks"], "0")
        self.assertEqual(
            registry.instances[0]["metadata"]["task_execution_status"],
            "idle",
        )

    def test_release_workflow_returns_all_instances_to_idle(self):
        registry = FakeRegistry()
        leases = AgentLeaseManager(registry)

        acquired = leases.acquire_all("recon", "wf-1", "wf-1:1:recon")
        self.assertEqual(len(acquired), 2)

        leases.release_workflow("wf-1")

        self.assertEqual(leases.list_leases(), [])
        self.assertTrue(
            all(instance["metadata"]["status"] == "idle" for instance in registry.instances)
        )

    def test_release_can_mark_down_agent_unavailable(self):
        registry = FakeRegistry()
        leases = AgentLeaseManager(registry)

        acquired = leases.acquire_one("recon", "wf-1", "wf-1:1:recon")
        leases.release(
            acquired,
            status="unavailable",
            metadata_updates={
                "unavailable_reason": "connection refused",
                "unavailable_workflow_id": "wf-1",
            },
        )

        self.assertEqual(leases.list_leases(), [])
        self.assertEqual(acquired.target["metadata"]["status"], "unavailable")
        self.assertEqual(
            acquired.target["metadata"]["unavailable_reason"],
            "connection refused",
        )
        self.assertNotIn("lease_workflow_id", acquired.target["metadata"])

    def test_resource_metadata_ranks_lower_load_first(self):
        registry = FakeRegistry()
        registry.instances[0]["metadata"]["resource_cpu_percent"] = 99.0
        registry.instances[0]["metadata"]["resource_memory_percent"] = 96.0
        leases = AgentLeaseManager(registry)

        acquired = leases.acquire_one("recon", "wf-1", "wf-1:1:recon")

        self.assertEqual(acquired.instance_key, "10.0.0.2:8013")
        self.assertEqual(registry.instances[1]["metadata"]["status"], "busy")

    def test_skill_matching_is_exact_and_does_not_use_substrings(self):
        registry = FakeRegistry()
        registry.instances = [
            {
                "ip": "10.0.0.10",
                "port": 8012,
                "metadata": {
                    "status": "idle",
                    "skill_ids": "scan_beach_defenses_extended",
                },
            }
        ]
        leases = AgentLeaseManager(registry)
        acquired = leases.acquire_one(
            "recon",
            "wf-exact",
            "wf-exact:scan",
            required_skill="scan_beach_defenses",
        )
        self.assertIsNone(acquired)

    def test_acquire_matches_required_skill_without_role_fallback(self):
        registry = FakeRegistry()
        registry.instances = [
            {
                "ip": "10.0.0.10",
                "port": 8012,
                "metadata": {"role": "generalist", "status": "idle", "skills": "scan_beach_defenses,探测"},
            },
            {
                "ip": "10.0.0.20",
                "port": 8012,
                "metadata": {"role": "recon", "status": "idle"},
            },
        ]
        leases = AgentLeaseManager(registry)

        acquired = leases.acquire_one(
            "recon",
            "wf-skill",
            "wf-skill:scan",
            required_skill="scan_beach_defenses",
        )

        self.assertEqual(acquired.instance_key, "10.0.0.10:8012")

        leases.release(acquired)
        registry.instances[0]["metadata"]["status"] = "busy"
        registry.instances[0]["metadata"]["active_tasks"] = "1"
        registry.instances[0]["metadata"]["max_concurrent_tasks"] = "1"
        registry.instances[0]["metadata"]["task_execution_status"] = "saturated"
        no_skill_match = leases.acquire_one(
            "recon",
            "wf-no-skill",
            "wf-no-skill:scan",
            required_skill="scan_beach_defenses",
        )

        self.assertIsNone(no_skill_match)


if __name__ == "__main__":
    unittest.main()
