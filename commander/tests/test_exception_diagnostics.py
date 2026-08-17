from __future__ import annotations

import asyncio
import tempfile
import unittest
from unittest.mock import patch

from a2a_protocol.server import A2ABaseAgent
from commander_agent.main import CommanderAgent
from observability import exception_diagnostics


class FailingAgent(A2ABaseAgent):
    def execute_task(self, payload):
        raise ValueError("simulated agent failure")


class ExceptionDiagnosticsTest(unittest.TestCase):
    def test_exception_diagnostics_contains_type_message_and_stack(self):
        def failing_function():
            raise RuntimeError("diagnostic failure")

        try:
            failing_function()
        except RuntimeError as exc:
            diagnostics = exception_diagnostics(exc)

        self.assertEqual(diagnostics["error_type"], "RuntimeError")
        self.assertEqual(diagnostics["error"], "diagnostic failure")
        self.assertIn("failing_function", diagnostics["traceback"])
        self.assertIn("RuntimeError: diagnostic failure", diagnostics["traceback"])

    def test_commander_failed_call_writes_traceback_to_trace_event(self):
        with tempfile.TemporaryDirectory() as state_dir:
            commander = CommanderAgent(mode="local", state_dir=state_dir, max_retries=0)
            commander.mode = "remote"
            client = unittest.mock.Mock()
            client.discover.side_effect = ConnectionError("agent card unavailable")

            with patch("commander_agent.main.A2AClient", return_value=client):
                success, error = commander._delegate_remote_candidate(
                    "recon",
                    {"ip": "10.0.0.1", "port": 8012},
                    {"workflow_id": "wf-1", "work_item": "wf-1:recon"},
                )

            self.assertFalse(success)
            self.assertIsInstance(error, ConnectionError)
            event = commander.workflow_context["trace"][-1]
            self.assertEqual(event["event_type"], "agent_call_failed")
            self.assertEqual(event["error_type"], "ConnectionError")
            self.assertIn("agent card unavailable", event["traceback"])

    def test_agent_keeps_traceback_server_side_only(self):
        with tempfile.TemporaryDirectory() as state_dir:
            agent = FailingAgent(
                "Failing_Agent",
                "test",
                "recon",
                8012,
                idempotency_db_path=f"{state_dir}/idempotency.db",
            )
            send_message_endpoint = next(
                route.endpoint
                for route in agent.app.routes
                if getattr(route, "path", None) == "/sendMessage"
            )
            response = asyncio.run(
                send_message_endpoint(
                    {
                        "schema_version": "1.0",
                        "workflow_id": "wf-1",
                        "work_item": "wf-1:recon",
                        "command": "scan_beach_defenses",
                        "required_skill": "scan_beach_defenses",
                        "input": {"sector": "Sector_A"},
                        "output_hint": "recon_report",
                    },
                    token="test-token",
                )
            )

            self.assertEqual(response["status"], "failed")
            self.assertNotIn("traceback", response)
            self.assertNotIn("last_error_details", agent.metrics_snapshot())
            details = agent.last_error_diagnostics()
            self.assertEqual(details["error_type"], "ValueError")
            self.assertIn("execute_task", details["traceback"])


if __name__ == "__main__":
    unittest.main()
