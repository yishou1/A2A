from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch

from commander_agent.agent_leases import AgentLease
from commander_agent.main import CommanderAgent


class EventuallyAvailableLeaseManager:
    def __init__(self, lease: AgentLease):
        self.lease = lease
        self.calls = 0

    def acquire_one(self, *args, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return None
        return self.lease


class AgentDiscoveryRetryTest(unittest.TestCase):
    def test_single_dispatch_retries_nacos_discovery_before_failing(self):
        target = {
            "ip": "127.0.0.1",
            "port": 10200,
            "metadata": {"role": "track_threat", "status": "idle"},
        }
        lease = AgentLease(
            instance_key="127.0.0.1:10200",
            service_name="A2A-Agent",
            role="track_threat",
            workflow_id="wf-retry",
            work_item="wf-retry:threat",
            acquired_at="2026-08-16T00:00:00Z",
            target=target,
        )

        with tempfile.TemporaryDirectory() as state_dir:
            commander = CommanderAgent(
                mode="local",
                workflow_id="wf-retry",
                state_dir=state_dir,
            )
            lease_manager = EventuallyAvailableLeaseManager(lease)
            commander.lease_manager = lease_manager
            commander.agent_discovery_retries = 2
            commander.agent_discovery_retry_interval = 0.25
            commander._delegate_leased_candidate = lambda *args, **kwargs: (True, None)

            with patch("commander_agent.main.time.sleep") as sleep:
                success = commander._delegate_task_with_lease(
                    "track_threat",
                    {"work_item": "wf-retry:threat", "required_skill": "threat_ranking"},
                )

        self.assertTrue(success)
        self.assertEqual(lease_manager.calls, 2)
        sleep.assert_called_once_with(0.25)
        retry_events = [
            event
            for event in commander.workflow_context["trace"]
            if event["event_type"] == "agent_discovery_retry"
        ]
        self.assertEqual(len(retry_events), 1)
        self.assertEqual(retry_events[0]["role"], "track_threat")


if __name__ == "__main__":
    unittest.main()
