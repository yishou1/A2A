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
    def test_snapshot_contains_system_process_and_state(self):
        monitor = ResourceMonitor(sampler=lambda: fake_sampler())

        snapshot = monitor.snapshot(force=True)

        self.assertTrue(snapshot["monitor_available"])
        self.assertEqual(snapshot["resource_state"], "ok")
        self.assertEqual(snapshot["system"]["cpu_percent"], 10.0)
        self.assertEqual(snapshot["process"]["memory_rss_mb"], 64.0)

    def test_thresholds_mark_critical_and_not_ready(self):
        monitor = ResourceMonitor(
            sampler=lambda: fake_sampler(cpu=99.0),
            cpu_critical_percent=95.0,
        )

        snapshot = monitor.snapshot(force=True)

        self.assertEqual(snapshot["resource_state"], "critical")
        self.assertFalse(monitor.ready())
        self.assertEqual(snapshot["violations"][0]["resource"], "cpu")

    def test_heartbeat_metadata_is_flat_for_nacos(self):
        monitor = ResourceMonitor(sampler=lambda: fake_sampler(memory=88.0))

        metadata = monitor.heartbeat_metadata()

        self.assertEqual(metadata["resource_monitor_available"], "true")
        self.assertEqual(metadata["resource_state"], "warn")
        self.assertEqual(metadata["resource_memory_percent"], 88.0)
        self.assertIn("resource_sampled_at", metadata)

    def test_agent_metrics_and_readiness_include_resources(self):
        monitor = ResourceMonitor(sampler=lambda: fake_sampler(cpu=99.0))
        agent = A2ABaseAgent(
            name="TestAgent",
            description="test",
            role="test",
            port=9999,
            resource_monitor=monitor,
        )

        metrics = agent.metrics_snapshot()

        self.assertEqual(metrics["resources"]["resource_state"], "critical")
        self.assertFalse(metrics["resource_ready"])
        accepted, error, code = agent.can_accept_task()
        self.assertFalse(accepted)
        self.assertEqual(code, "AGENT_RESOURCE_EXHAUSTED")
        self.assertIn("critical", error)


if __name__ == "__main__":
    unittest.main()
