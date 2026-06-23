from __future__ import annotations

import unittest
import tempfile

import requests

from commander_agent.agent_leases import AgentLeaseManager
from commander_agent.circuit_breaker import AgentCircuitBreaker
from commander_agent.main import CommanderAgent


class MutableClock:
    def __init__(self, value=1000.0):
        self.value = value

    def __call__(self):
        return self.value


class FakeRegistry:
    def __init__(self, instance):
        self.instance = instance

    def discover_service(self, service_name, required_tags=None):
        metadata = self.instance["metadata"]
        if all(metadata.get(key) == value for key, value in (required_tags or {}).items()):
            return [self.instance]
        return []

    def update_instance_metadata(self, service_name, instance, metadata_updates=None, remove_keys=None):
        instance["metadata"].update(metadata_updates or {})
        for key in remove_keys or []:
            instance["metadata"].pop(key, None)
        return instance["metadata"]


class AgentCircuitBreakerTest(unittest.TestCase):
    def test_closed_opens_after_threshold_and_half_open_probe_recovers(self):
        clock = MutableClock()
        breaker = AgentCircuitBreaker(failure_threshold=3, recovery_timeout=10, clock=clock)
        target = {"ip": "10.0.0.1", "port": 8012, "metadata": {"status": "idle"}}

        self.assertTrue(breaker.allow_request(target))
        self.assertEqual(breaker.record_failure(target)["state"], "closed")
        self.assertEqual(breaker.record_failure(target)["state"], "closed")
        opened = breaker.record_failure(target)
        self.assertEqual(opened["state"], "open")
        self.assertFalse(breaker.allow_request(target))

        clock.value += 10
        self.assertTrue(breaker.allow_request(target))
        self.assertEqual(breaker.snapshot(target)["state"], "half_open")
        self.assertFalse(breaker.allow_request(target))

        breaker.record_success(target)
        self.assertEqual(breaker.snapshot(target)["state"], "closed")
        self.assertTrue(breaker.allow_request(target))

    def test_half_open_probe_failure_reopens_circuit(self):
        clock = MutableClock()
        breaker = AgentCircuitBreaker(failure_threshold=1, recovery_timeout=5, clock=clock)
        target = {"ip": "10.0.0.1", "port": 8012, "metadata": {"status": "idle"}}

        breaker.record_failure(target)
        clock.value += 5
        self.assertTrue(breaker.allow_request(target))

        reopened = breaker.record_failure(target)
        self.assertEqual(reopened["state"], "open")
        self.assertEqual(reopened["open_until_ts"], clock.value + 5)

    def test_lease_manager_skips_open_instance_then_allows_half_open_probe(self):
        clock = MutableClock()
        breaker = AgentCircuitBreaker(failure_threshold=1, recovery_timeout=10, clock=clock)
        instance = {
            "ip": "10.0.0.1",
            "port": 8012,
            "metadata": {"role": "recon", "status": "unavailable"},
        }
        registry = FakeRegistry(instance)
        leases = AgentLeaseManager(registry, circuit_breaker=breaker)
        breaker.record_failure(instance)
        instance["metadata"].update(breaker.metadata(instance))

        self.assertIsNone(leases.acquire_one("recon", "wf-1", "wf-1:recon"))

        clock.value += 10
        lease = leases.acquire_one("recon", "wf-2", "wf-2:recon")
        self.assertIsNotNone(lease)
        self.assertEqual(instance["metadata"]["status"], "busy")
        self.assertEqual(instance["metadata"]["circuit_state"], "half_open")

    def test_commander_opens_circuit_and_stops_dispatching_to_failed_agent(self):
        with tempfile.TemporaryDirectory() as state_dir:
            commander = CommanderAgent(mode="local", state_dir=state_dir)
            instance = {
                "ip": "10.0.0.1",
                "port": 8012,
                "metadata": {"role": "recon", "status": "idle"},
            }
            registry = FakeRegistry(instance)
            breaker = AgentCircuitBreaker(failure_threshold=2, recovery_timeout=60)
            commander.mode = "remote"
            commander.registry = registry
            commander.circuit_breaker = breaker
            commander.lease_manager = AgentLeaseManager(registry, circuit_breaker=breaker)
            calls = []

            def fail_candidate(role, target, payload, stream=False, **kwargs):
                calls.append(target["port"])
                return False, requests.ConnectionError("connection refused")

            commander._delegate_remote_candidate = fail_candidate
            payload = {"workflow_id": "wf-circuit", "work_item": "wf-circuit:recon"}

            self.assertFalse(commander.delegate_task("recon", payload))
            self.assertFalse(commander.delegate_task("recon", payload))
            self.assertEqual(instance["metadata"]["status"], "unavailable")
            self.assertEqual(instance["metadata"]["circuit_state"], "open")

            self.assertFalse(commander.delegate_task("recon", payload))
            self.assertEqual(calls, [8012, 8012])
            trace_types = [event["event_type"] for event in commander.workflow_context["trace"]]
            self.assertIn("agent_circuit_opened", trace_types)


if __name__ == "__main__":
    unittest.main()
