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


if __name__ == "__main__":
    unittest.main()
