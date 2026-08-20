from __future__ import annotations

import tempfile
import unittest

from commander_agent.scheduling_policy import JsonSchedulerFeedbackStore, SchedulingPolicy


def candidate(ip: str, port: int, **metadata):
    base = {"role": "recon", "status": "idle"}
    base.update({key: str(value) for key, value in metadata.items()})
    return {"ip": ip, "port": port, "metadata": base}


class SchedulingPolicyTest(unittest.TestCase):
    def test_ranks_low_load_high_quality_candidate_first(self):
        policy = SchedulingPolicy()
        slow_busy = candidate(
            "10.0.0.1",
            8012,
            resource_cpu_percent=85,
            resource_memory_percent=90,
            active_tasks=1,
            max_concurrent_tasks=2,
            quality_success_rate=0.7,
            quality_avg_latency_ms=1800,
        )
        fast_idle = candidate(
            "10.0.0.2",
            8013,
            resource_cpu_percent=15,
            resource_memory_percent=35,
            active_tasks=0,
            max_concurrent_tasks=2,
            quality_success_rate=0.98,
            quality_avg_latency_ms=120,
        )

        ranked = policy.rank([slow_busy, fast_idle], instance_key=lambda item: f"{item['ip']}:{item['port']}")

        self.assertEqual(ranked[0]["port"], 8013)
        decision = ranked[0]["_scheduling_decision"]
        self.assertTrue(decision["accepted"])
        self.assertIn("ranked_by_resource_capacity_quality_feedback", decision["reasons"])
        self.assertGreater(decision["components"]["resource_score"], 0)

    def test_resource_limits_filter_overloaded_candidate(self):
        policy = SchedulingPolicy(resource_limits={"cpu_percent": 80})
        overloaded = candidate("10.0.0.1", 8012, resource_cpu_percent=95)
        healthy = candidate("10.0.0.2", 8013, resource_cpu_percent=30)

        ranked = policy.rank([overloaded, healthy], instance_key=lambda item: f"{item['ip']}:{item['port']}")

        self.assertEqual([item["port"] for item in ranked], [8013])
        self.assertFalse(overloaded["_scheduling_decision"]["accepted"])
        self.assertIn("exceeds", overloaded["_scheduling_decision"]["reasons"][0])

    def test_feedback_changes_future_ranking(self):
        policy = SchedulingPolicy()
        first = candidate("10.0.0.1", 8012, resource_cpu_percent=30, resource_memory_percent=30)
        second = candidate("10.0.0.2", 8013, resource_cpu_percent=30, resource_memory_percent=30)
        policy.record_feedback("10.0.0.1:8012", success=False, latency_ms=3000, error_code="AGENT_TIMEOUT")
        policy.record_feedback("10.0.0.1:8012", success=False, latency_ms=3000, error_code="AGENT_TIMEOUT")
        policy.record_feedback("10.0.0.2:8013", success=True, latency_ms=100)

        ranked = policy.rank([first, second], instance_key=lambda item: f"{item['ip']}:{item['port']}")

        self.assertEqual(ranked[0]["port"], 8013)
        snapshot = policy.feedback_snapshot()
        self.assertEqual(snapshot["10.0.0.1:8012"]["failures"], 2)

    def test_json_feedback_store_persists_scores(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = f"{temp_dir}/feedback.json"
            first = JsonSchedulerFeedbackStore(path)
            first.record("10.0.0.1:8012", success=False, latency_ms=250)

            second = JsonSchedulerFeedbackStore(path)
            snapshot = second.snapshot()

            self.assertEqual(snapshot["10.0.0.1:8012"]["attempts"], 1)
            self.assertEqual(snapshot["10.0.0.1:8012"]["last_error_code"], None)


if __name__ == "__main__":
    unittest.main()
