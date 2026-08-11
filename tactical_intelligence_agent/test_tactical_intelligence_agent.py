"""战术情报 Agent — A2A Commander 协议与宕机恢复接入测试。"""

from __future__ import annotations

import json
import os
import unittest
from datetime import datetime, timezone

os.environ.setdefault("TIA_CONFIG", "config/default.yaml")
os.environ.setdefault("TIA_SKIP_WARMUP", "1")

from a2a_protocol.messages import is_success_response
from agent.models.schemas import SemanticIntelligencePacket
from tactical_intelligence_agent.downstream_adapter import to_trajectory_predictor_input
from tactical_intelligence_agent.payload_adapter import commander_payload_to_batch
from tactical_intelligence_agent.service import TacticalIntelligenceCommanderAgent
from agent.track_packet import accumulate_track_history
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

    def test_empty_payload_rejects_without_mock(self):
        with self.assertRaises(ValueError):
            commander_payload_to_batch({"workflow_id": "wf-empty"})


class TrackPacketTest(unittest.TestCase):
    def test_accumulate_history_and_trajectory_adapter(self):
        t0 = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        t1 = datetime(2026, 6, 1, 12, 0, 10, tzinfo=timezone.utc)
        frame0 = [
            {
                "track_id": "T-0001",
                "class_name": "ship",
                "confidence": 0.9,
                "geo": {"lat": 31.230, "lon": 121.470, "alt_m": 0.0},
            }
        ]
        prior = accumulate_track_history(frame0, [], timestamp=t0)
        frame1 = [
            {
                "track_id": "T-0001",
                "class_name": "ship",
                "confidence": 0.92,
                "geo": {"lat": 31.232, "lon": 121.478, "alt_m": 0.0},
            }
        ]
        merged = accumulate_track_history(frame1, prior, timestamp=t1)
        self.assertEqual(len(merged), 1)
        track = merged[0]
        self.assertEqual(track["object_type"], "ship")
        self.assertEqual(len(track["history_path"]), 2)
        self.assertIn("speed", track["history_path"][-1])
        self.assertIn("heading", track["history_path"][-1])

        packet = {
            "schema_version": "1.0",
            "tracks": merged,
            "targets": [{"track_id": "T-0001", "class": "ship"}],
        }
        traj = to_trajectory_predictor_input(packet)
        self.assertEqual(len(traj["tracks"]), 1)
        self.assertEqual(traj["tracks"][0]["track_id"], "T-0001")
        self.assertGreaterEqual(len(traj["tracks"][0]["history_path"]), 2)


class _StubEngine:
    """单测用：不跑真实推理，只返回标准 packet。"""

    def process(self, batch):
        return SemanticIntelligencePacket(
            schema_version="1.0",
            mission_id=batch.mission_id,
            summary="stub packet",
            tracks=[
                {
                    "track_id": "T-0001",
                    "object_type": "ship",
                    "timestamp": 1718000050.0,
                    "lat": 31.24,
                    "lon": 121.51,
                    "alt": 0.0,
                    "speed": 12.0,
                    "heading": 90.0,
                    "confidence": 0.9,
                    "history_path": [
                        {
                            "timestamp": 1718000040.0,
                            "lat": 31.239,
                            "lon": 121.50,
                            "alt": 0.0,
                            "speed": 11.5,
                            "heading": 89.0,
                            "confidence": 0.88,
                        }
                    ],
                }
            ],
            targets=[{"track_id": "T-0001", "class": "ship", "threat_level": "high"}],
            consumer_guide={"schema_version": "1.0", "sections": {}},
        )


class TacticalIntelligenceCommanderAgentTest(unittest.IsolatedAsyncioTestCase):
    def _demo_payload(self, work_item: str) -> dict:
        attachment = build_attachment_ref(
            "https://minio.example.local/a2a/recon/frame-001.jpg",
            sha256="deadbeef",
            kind="image",
            attachment_id="att-demo",
            meta={"sensor_id": "EO-1", "modality": "eo_ir"},
        )
        return {
            "schema_version": "1.0",
            "workflow_id": "wf-stream",
            "work_item": work_item,
            "command": "process_intelligence",
            "required_skill": "tactical_intelligence_analysis",
            "output_hint": "intelligence_packet",
            "input": {"agent_request": {"recon_report": "Test recon."}},
            "attachments": [attachment],
        }

    def _agent(self) -> TacticalIntelligenceCommanderAgent:
        return TacticalIntelligenceCommanderAgent(port=0, engine=_StubEngine())

    async def test_execute_stream_completed(self):
        agent = self._agent()
        payload = self._demo_payload("wf-stream:activatity-001")
        events = []
        async for event in agent.execute_stream(payload):
            events.append(event)

        self.assertGreaterEqual(len(events), 4)
        last = json.loads(events[-1].removeprefix("data: ").strip())
        self.assertEqual(last["status"], "Completed")
        self.assertIn("intelligence_packet", last)
        self.assertIn("output", last)
        packet = last["intelligence_packet"]
        self.assertEqual(packet["schema_version"], "1.0")
        self.assertIn("tracks", packet)
        self.assertGreaterEqual(len(packet["tracks"]), 1)

    def test_execute_task_output_envelope(self):
        agent = self._agent()
        payload = self._demo_payload("wf-task:activatity-001")
        output, message = agent.execute_task(payload)
        self.assertIn("intelligence_packet", output)
        self.assertIn("track_count", output)
        self.assertIn("target_count", output)
        self.assertIn("consumer_guide", output)
        self.assertTrue(message.startswith("Tactical intelligence completed"))
        packet = output["intelligence_packet"]
        self.assertEqual(packet["schema_version"], "1.0")
        self.assertIn("tracks", packet)
        self.assertIn("targets", packet)

    def test_execute_task_idempotent(self):
        agent = self._agent()
        payload = self._demo_payload("wf-idem:activatity-001")
        first, _ = agent.execute_task(payload)
        second, _ = agent.execute_task(payload)
        self.assertEqual(
            first["intelligence_packet"]["packet_id"],
            second["intelligence_packet"]["packet_id"],
        )
        self.assertEqual(first["track_count"], second["track_count"])

    def test_health_ready_endpoints_exist(self):
        agent = self._agent()
        from fastapi.testclient import TestClient

        client = TestClient(agent.app)
        health = client.get("/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json().get("status"), "ok")

        ready = client.get("/ready")
        self.assertEqual(ready.status_code, 200)
        self.assertTrue(ready.json().get("ready"))

    def test_send_message_unified_envelope(self):
        agent = self._agent()
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
        out = body.get("output", {})
        self.assertIn("intelligence_packet", out)
        self.assertIn("tracks", out["intelligence_packet"])
        self.assertIn("latency_ms", body.get("metrics", {}))


if __name__ == "__main__":
    unittest.main()
