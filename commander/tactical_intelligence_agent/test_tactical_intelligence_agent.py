"""战术情报 Agent — A2A Commander 协议与宕机恢复接入测试。"""

from __future__ import annotations

import json
import os
import unittest
from unittest import mock
from datetime import datetime, timezone

os.environ.setdefault("TIA_CONFIG", "config/default.yaml")
os.environ.setdefault("TIA_SKIP_WARMUP", "1")

from a2a_protocol.messages import is_success_response
from agent.models.schemas import PerceptionOutput, SemanticIntelligencePacket
from agent.algorithm_library.planner_runtime import AlgorithmCall, AlgorithmPlan
from agent.skills.perception.motr_neural_kalman_tracker import MOTRNeuralKalmanTracker
from agent.skills.communication.skill import CommunicationSkill
from tactical_intelligence_agent.downstream_adapter import to_trajectory_predictor_input
from tactical_intelligence_agent.payload_adapter import commander_payload_to_batch
from tactical_intelligence_agent.service import TacticalIntelligenceCommanderAgent
from agent.track_packet import accumulate_track_history
from workflow_payloads import build_attachment_ref
from agent.pipeline import load_config
from agent.skills.cognition.skill import CognitionSkill


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

    def test_bpel_mission_input_is_unwrapped(self):
        batch = commander_payload_to_batch(
            {
                "workflow_id": "wf-bpel",
                "input": {
                    "mission_input": {
                        "recon_report": "Hostile UAV approaching Sector_A.",
                        "sector": "Sector_A",
                        "coordinates": "120.5E, 35.1N",
                    }
                },
            }
        )
        self.assertEqual(len(batch.frames), 3)
        self.assertEqual(batch.context["sector"], "Sector_A")

    def test_amos_contacts_preserve_identity_confidence_and_geo(self):
        batch = commander_payload_to_batch({
            "workflow_id": "wf-amos",
            "input": {
                "mission_input": {
                    "contacts": [{
                        "track_id": "TRK-AMOS-01",
                        "classification": "unknown",
                        "geo": {"lat": 22.123, "lon": 121.456},
                        "metadata": {
                            "confidence": 0.97,
                            "domain_hint": "maritime",
                        },
                    }],
                    "perception_frames": [{
                        "task_id": "frame-1",
                        "detections": [{
                            "detection_id": "TRK-AMOS-01-t1",
                            "object_type": "ship",
                            "lat": 22.124,
                            "lon": 121.457,
                            "confidence": 0.99,
                            "metadata": {"amos_track_id": "TRK-AMOS-01"},
                        }],
                    }],
                },
            },
        })

        contact = batch.frames[0].payload["detections"][0]
        historical = batch.frames[1].payload["detections"][0]
        self.assertEqual(contact["track_id"], "TRK-AMOS-01")
        self.assertEqual(contact["confidence"], 0.97)
        self.assertEqual(contact["geo"], {"lat": 22.123, "lon": 121.456, "alt_m": 0.0})
        self.assertEqual(historical["track_id"], "TRK-AMOS-01")
        self.assertEqual(historical["class_name"], "ship")
        self.assertEqual(historical["geo"]["lat"], 22.124)

    def test_motr_mock_keeps_amos_track_identity_and_latest_geo(self):
        tracker = MOTRNeuralKalmanTracker(use_mock=True)
        result = tracker.run({
            "verified_detections": [
                {
                    "track_id": "TRK-AMOS-01",
                    "class_name": "ship",
                    "confidence": 0.97,
                    "bbox": [0, 0, 0, 0],
                    "geo": {"lat": 22.123, "lon": 121.456, "alt_m": 0},
                },
                {
                    "track_id": "TRK-AMOS-01",
                    "class_name": "ship",
                    "confidence": 0.99,
                    "bbox": [0, 0, 0, 0],
                    "geo": {"lat": 22.124, "lon": 121.457, "alt_m": 0},
                },
            ],
            "prior_tracks": [],
            "batch_context": {},
        })

        self.assertEqual(len(result["tracks"]), 1)
        track = result["tracks"][0]
        self.assertEqual(track["track_id"], "TRK-AMOS-01")
        self.assertEqual(track["geo"]["lat"], 22.124)
        self.assertEqual(track["geo"]["lon"], 121.457)
        self.assertEqual(track["geo"]["geo_method"], "provided_detection_geo")

    def test_current_maritime_observables_classify_hostile_and_fishing_contacts(self):
        batch = commander_payload_to_batch({
            "workflow_id": "wf-maritime",
            "input": {
                "mission_input": {
                    "stage_transfer": {
                        "checkpoint_id": "MAR-CP-PERCEPTION",
                        "phase": "FIX",
                    },
                    "contacts": [
                        {
                            "track_id": "TRK-HOSTILE",
                            "classification": "fast_attack_craft",
                            "geo": {"lat": 22.2, "lon": 121.5},
                            "metadata": {
                                "domain_hint": "maritime",
                                "speed_kts": 14.0,
                                "ais_observed": False,
                                "sensor_sources": ["RADAR"],
                            },
                        },
                        {
                            "track_id": "TRK-FISHING",
                            "geo": {"lat": 22.1, "lon": 121.4},
                            "metadata": {
                                "domain_hint": "maritime",
                                "speed_kts": 6.0,
                                "ais_observed": True,
                                "sensor_sources": ["RADAR", "AIS", "IR"],
                            },
                        },
                    ],
                },
            },
        })
        classifications = CognitionSkill._apply_observable_classification_evidence(
            batch,
            [
                {"target_id": "TRK-HOSTILE", "label": "unknown", "confidence": 0.6},
                {"target_id": "TRK-FISHING", "label": "unknown", "confidence": 0.6},
            ],
        )
        by_id = {item["target_id"]: item for item in classifications}

        self.assertEqual(by_id["TRK-HOSTILE"]["label"], "hostile")
        self.assertEqual(by_id["TRK-HOSTILE"]["object_class"], "fast_attack_craft")
        self.assertEqual(by_id["TRK-FISHING"]["label"], "neutral")
        self.assertEqual(by_id["TRK-FISHING"]["object_class"], "fishing_vessel")

    def test_current_multisensor_ground_observables_classify_coastal_missile_site(self):
        batch = commander_payload_to_batch({
            "workflow_id": "wf-coastal-joint",
            "input": {
                "mission_input": {
                    "stage_transfer": {
                        "checkpoint_id": "CJR-CP-IDENTIFY",
                        "phase": "TRACK",
                    },
                    "contacts": [{
                        "track_id": "TRK-GROUND-01",
                        "geo": {"lat": 22.34, "lon": 120.93},
                        "metadata": {
                            "domain_hint": "ground",
                            "speed_kts": 0.0,
                            "sensor_sources": ["SAR", "EO/IR", "ELINT"],
                        },
                    }],
                },
            },
        })

        classifications = CognitionSkill._apply_observable_classification_evidence(
            batch,
            [{"target_id": "TRK-GROUND-01", "label": "unknown", "confidence": 0.6}],
        )

        self.assertEqual(classifications[0]["label"], "hostile")
        self.assertEqual(classifications[0]["affiliation"], "red")
        self.assertEqual(classifications[0]["object_class"], "coastal_missile_site")
        self.assertEqual(
            classifications[0]["classification_basis"],
            "stationary_multisensor_ground_site_evidence",
        )

    def test_find_phase_does_not_identify_contacts_early(self):
        batch = commander_payload_to_batch({
            "workflow_id": "wf-find",
            "input": {
                "mission_input": {
                    "stage_transfer": {"checkpoint_id": "MAR-CP-FIND", "phase": "FIND"},
                    "contacts": [
                        {
                            "track_id": "TRK-FISHING",
                            "metadata": {
                                "domain_hint": "maritime",
                                "speed_kts": 6.0,
                                "ais_observed": True,
                                "sensor_sources": ["RADAR", "AIS"],
                            },
                        }
                    ],
                },
            },
        })

        classifications = CognitionSkill._apply_observable_classification_evidence(
            batch,
            [{"target_id": "TRK-FISHING", "label": "unknown", "confidence": 0.6}],
        )

        self.assertEqual(classifications[0]["label"], "unknown")
        self.assertNotIn("object_class", classifications[0])

    def test_observable_maritime_safety_classification_survives_llm_classifier_skip(self):
        batch = commander_payload_to_batch({
            "workflow_id": "wf-llm-minimal",
            "input": {
                "mission_input": {
                    "stage_transfer": {"checkpoint_id": "MAR-CP-ASSESS"},
                    "contacts": [
                        {
                            "track_id": "TRK-HOSTILE",
                            "geo": {"lat": 22.2, "lon": 121.5},
                            "metadata": {
                                "domain_hint": "maritime",
                                "speed_kts": 24.0,
                                "ais_observed": False,
                                "sensor_sources": ["RADAR", "EO/IR", "ELINT"],
                            },
                        },
                        {
                            "track_id": "TRK-FISHING",
                            "geo": {"lat": 22.1, "lon": 121.4},
                            "metadata": {
                                "domain_hint": "maritime",
                                "speed_kts": 6.0,
                                "ais_observed": True,
                                "sensor_sources": ["RADAR", "AIS"],
                            },
                        },
                    ],
                },
            },
        })
        plan = AlgorithmPlan(
            mode="llm",
            intent="minimal required pipeline",
            algorithm_calls=[
                AlgorithmCall(algorithm_id="battlefield_rtdetr_detector"),
                AlgorithmCall(algorithm_id="edl_evidential_verifier"),
                AlgorithmCall(algorithm_id="motr_neural_kalman_tracker"),
            ],
        )
        perception = PerceptionOutput(
            detections=[
                {"track_id": "TRK-HOSTILE", "class_name": "ship", "confidence": 0.8},
                {"track_id": "TRK-FISHING", "class_name": "ship", "confidence": 0.8},
            ],
            tracks=[
                {"track_id": "TRK-HOSTILE", "confidence": 0.8},
                {"track_id": "TRK-FISHING", "confidence": 0.8},
            ],
        )
        result = CognitionSkill(use_mock=True).execute(
            batch,
            perception,
            plan=plan,
        )
        by_id = {item["target_id"]: item for item in result.classifications}
        packet = CommunicationSkill(use_mock=True).execute(
            batch.mission_id,
            perception,
            result,
            plan=plan,
        )
        targets = {item["track_id"]: item for item in packet.targets}

        self.assertEqual(by_id["TRK-HOSTILE"]["object_class"], "fast_attack_craft")
        self.assertEqual(by_id["TRK-FISHING"]["object_class"], "fishing_vessel")
        self.assertEqual(targets["TRK-HOSTILE"]["class"], "fast_attack_craft")
        self.assertEqual(targets["TRK-FISHING"]["class"], "fishing_vessel")

    def test_tia_use_mock_environment_overrides_yaml(self):
        with mock.patch.dict(os.environ, {"TIA_USE_MOCK": "1"}):
            self.assertTrue(load_config()["use_mock"])


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


class CommunicationSkillTest(unittest.TestCase):
    def test_skipped_compression_uses_passthrough_source(self):
        class _StubRouter:
            name = "router_stub"

            @staticmethod
            def run(payload):
                return {"routes": []}

        class _StubDet:
            def __init__(self):
                self.track_id = "T-0001"
                self.class_name = "ship"
                self.confidence = 0.91
                self.geo = {"lat": 31.2, "lon": 121.4, "alt_m": 0.0}
                self.damage_score = 0.3

        class _StubPacket:
            def __init__(self):
                self.tracks = [{"track_id": "T-0001"}]
                self.detections = [_StubDet()]
                self.task_schedule = None
                self.algorithm_trace = {"perception": "ok"}
                self.algorithm_invocations = []

            def model_dump(self, **_kwargs):
                return {}

        skill = CommunicationSkill(use_mock=True, config={})
        skill.router = _StubRouter()
        skill.compression = type(
            "StubCompression",
            (),
            {"name": "semantic_comm_stub"},
        )()

        perception = _StubPacket()
        cognition = type(
            "StubCognition",
            (),
            {
                "algorithm_trace": {"cognition": "ok"},
                "algorithm_invocations": [],
                "model_dump": lambda self, **_kwargs: {},
            },
        )()
        plan = mock.Mock()
        plan.is_enabled.side_effect = lambda aid: aid != "knowledge_semantic_comm"
        plan.algorithm_calls = []
        plan.params_for.return_value = {}

        packet = skill.execute(
            "wf-comm-test",
            perception,
            cognition,
            subscriber_agents=["commander"],
            plan=plan,
        )

        self.assertIn("source=perception_detections", packet.summary)
        self.assertEqual(packet.provenance["targets"]["source"], "perception_detections")
        self.assertEqual(packet.provenance["targets"]["count"], 1)


if __name__ == "__main__":
    unittest.main()
