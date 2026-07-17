"""战术情报 Agent — A2A Commander 协议单元测试。"""

from __future__ import annotations

import json
import os
import unittest

os.environ.setdefault("TIA_CONFIG", "config/default.yaml")

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
    async def test_execute_stream_completed(self):
        agent = TacticalIntelligenceCommanderAgent(port=0)
        payload = {
            "workflow_id": "wf-stream",
            "work_item": "wf-stream:activatity-001",
            "command": "process_intelligence",
            "input": {"recon_report": "Test recon."},
        }
        events = []
        async for event in agent.execute_stream(payload):
            events.append(event)

        self.assertGreaterEqual(len(events), 4)
        last = json.loads(events[-1].removeprefix("data: ").strip())
        self.assertEqual(last["status"], "Completed")
        self.assertIn("intelligence_packet", last)

    def test_send_message_idempotent(self):
        agent = TacticalIntelligenceCommanderAgent(port=0)
        payload = {
            "workflow_id": "wf-idem",
            "work_item": "wf-idem:activatity-001",
            "command": "process_intelligence",
            "input": {"recon_report": "Idempotent test."},
        }
        first = agent.build_send_message_response(payload, payload["work_item"])
        second = agent.build_send_message_response(payload, payload["work_item"])
        self.assertEqual(first["intelligence_packet_id"], second["intelligence_packet_id"])
        self.assertEqual(first["target_count"], second["target_count"])


if __name__ == "__main__":
    unittest.main()
