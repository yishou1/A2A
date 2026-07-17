from __future__ import annotations

import unittest

from a2a_protocol.server import A2ABaseAgent
from resource_monitor import ResourceMonitor


def fake_sampler(cpu=10.0, memory=20.0, disk=30.0):
    return {
        "system": {
            "cpu_percent": cpu,
            "cpu_count": 8,
            "memory_total_bytes": 16 * 1024 * 1024 * 1024,
            "memory_available_bytes": 8 * 1024 * 1024 * 1024,
            "memory_percent": memory,
            "disk_path": ".",
            "disk_total_bytes": 100,
            "disk_used_bytes": disk,
            "disk_free_bytes": 100 - disk,
            "disk_percent": disk,
            "platform": "test",
        },
        "process": {
            "pid": 123,
            "cpu_percent": 4.5,
            "memory_rss_bytes": 64 * 1024 * 1024,
            "memory_vms_bytes": 128 * 1024 * 1024,
            "num_threads": 4,
        },
    }


class ResourceMonitorTest(unittest.TestCase):
    def test_snapshot_contains_system_and_process_values(self):
        monitor = ResourceMonitor(sampler=lambda: fake_sampler())

        snapshot = monitor.snapshot(force=True)

        self.assertTrue(snapshot["monitor_available"])
        self.assertEqual(snapshot["system"]["cpu_percent"], 10.0)
        self.assertEqual(snapshot["system"]["memory_percent"], 20.0)
        self.assertEqual(snapshot["system"]["disk_percent"], 30.0)
        self.assertEqual(snapshot["process"]["memory_rss_mb"], 64.0)

    def test_high_values_are_reported_without_classification(self):
        monitor = ResourceMonitor(sampler=lambda: fake_sampler(cpu=99.0, memory=96.0, disk=98.0))

        snapshot = monitor.snapshot(force=True)

        self.assertEqual(snapshot["system"]["cpu_percent"], 99.0)
        self.assertEqual(snapshot["system"]["memory_percent"], 96.0)
        self.assertEqual(snapshot["system"]["disk_percent"], 98.0)
        self.assertNotIn("resource_state", snapshot)
        self.assertNotIn("thresholds", snapshot)
        self.assertNotIn("violations", snapshot)

    def test_heartbeat_metadata_is_flat_for_nacos(self):
        monitor = ResourceMonitor(sampler=lambda: fake_sampler(memory=88.0))

        metadata = monitor.heartbeat_metadata()

        self.assertEqual(metadata["resource_monitor_available"], "true")
        self.assertEqual(metadata["resource_memory_percent"], 88.0)
        self.assertNotIn("resource_state", metadata)
        self.assertIn("resource_sampled_at", metadata)

    def test_agent_metrics_include_resources_without_rejecting_tasks(self):
        monitor = ResourceMonitor(sampler=lambda: fake_sampler(cpu=99.0))
        agent = A2ABaseAgent(
            name="TestAgent",
            description="test",
            role="test",
            port=9999,
            resource_monitor=monitor,
        )

        metrics = agent.metrics_snapshot()

        self.assertEqual(metrics["resources"]["system"]["cpu_percent"], 99.0)
        self.assertNotIn("resource_ready", metrics)
        accepted, error, code = agent.can_accept_task()
        self.assertTrue(accepted)
        self.assertIsNone(error)
        self.assertIsNone(code)


if __name__ == "__main__":
    unittest.main()
