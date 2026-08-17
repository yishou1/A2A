"""战术情报 Agent — A2A Commander 协议与宕机恢复接入测试。"""

from __future__ import annotations

import json
import os
import unittest

os.environ.setdefault("TIA_CONFIG", "config/default.yaml")

from a2a_protocol.messages import is_success_response
from tactical_intelligence_agent.payload_adapter import commander_payload_to_batch
from tactical_intelligence_agent.service import TacticalIntelligenceCommanderAgent
from workflow_payloads import build_attachment_ref


class PayloadAdapterTest(unittest.TestCase):
    def test_commander_payload_to_batch(self):
        attachment = build_attachment_ref(
            "https://minio.example.local/a2a/recon/frame-001.jpg",
            sha256="deadbeef",
            kind="image",
            attachment_id="att-1",
        )
        payload = {
            "workflow_id": "wf-001",
            "work_item": "wf-001:activatity-001",
            "command": "process_intelligence",
            "input": {"recon_report": "Enemy positions observed."},
            "attachments": [attachment],
            "context": {"jamming_level": 0.2},
        }
        batch = commander_payload_to_batch(payload)
        self.assertEqual(batch.mission_id, "wf-001")
        self.assertGreaterEqual(len(batch.frames), 2)
        self.assertEqual(batch.context["command"], "process_intelligence")


class TacticalIntelligenceCommanderAgentTest(unittest.IsolatedAsyncioTestCase):
    def _demo_payload(self, work_item: str) -> dict:
        return {
            "workflow_id": "wf-stream",
            "work_item": work_item,
            "command": "process_intelligence",
            "output_hint": "intelligence_packet",
            "input": {"recon_report": "Test recon."},
        }

    async def test_execute_stream_completed(self):
        agent = TacticalIntelligenceCommanderAgent(port=0)
        payload = self._demo_payload("wf-stream:activatity-001")
        events = []
        async for event in agent.execute_stream(payload):
            events.append(event)

        self.assertGreaterEqual(len(events), 4)
        last = json.loads(events[-1].removeprefix("data: ").strip())
        self.assertEqual(last["status"], "Completed")
        self.assertIn("intelligence_packet", last)
        self.assertIn("output", last)

    def test_execute_task_output_envelope(self):
        agent = TacticalIntelligenceCommanderAgent(port=0)
        payload = self._demo_payload("wf-task:activatity-001")
        output, message = agent.execute_task(payload)
        self.assertIn("intelligence_packet", output)
        self.assertIn("target_count", output)
        self.assertTrue(message.startswith("Tactical intelligence completed"))
        self.assertIsInstance(output["intelligence_packet"], dict)

    def test_execute_task_idempotent(self):
        agent = TacticalIntelligenceCommanderAgent(port=0)
        payload = self._demo_payload("wf-idem:activatity-001")
        first, _ = agent.execute_task(payload)
        second, _ = agent.execute_task(payload)
        self.assertEqual(
            first["intelligence_packet"]["packet_id"],
            second["intelligence_packet"]["packet_id"],
        )
        self.assertEqual(first["target_count"], second["target_count"])

    def test_health_ready_endpoints_exist(self):
        agent = TacticalIntelligenceCommanderAgent(port=0)
        from fastapi.testclient import TestClient

        client = TestClient(agent.app)
        health = client.get("/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json().get("status"), "ok")

        ready = client.get("/ready")
        self.assertEqual(ready.status_code, 200)
        self.assertTrue(ready.json().get("ready"))

    def test_send_message_unified_envelope(self):
        agent = TacticalIntelligenceCommanderAgent(port=0)
        from fastapi.testclient import TestClient

        client = TestClient(agent.app)
        payload = self._demo_payload("wf-http:activatity-001")
        response = client.post(
            "/sendMessage",
            json=payload,
            headers={"Authorization": "Bearer mock-jwt-token-abcd"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(is_success_response(body))
        self.assertEqual(body.get("role"), "tactical_intelligence")
        self.assertEqual(body.get("work_item"), payload["work_item"])
        self.assertIn("intelligence_packet", body.get("output", {}))
        self.assertIn("latency_ms", body.get("metrics", {}))


if __name__ == "__main__":
    unittest.main()
