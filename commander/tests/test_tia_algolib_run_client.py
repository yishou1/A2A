"""TIA 集中式 /algorithms + /run 调用路径单测。"""

from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from agent.algorithm_library.catalog import build_algorithm_catalog
from agent.algorithm_library.client import AlgorithmLibraryClient, AlgorithmLibraryError, AlgorithmRunCall
from agent.algorithm_library.planner_runtime import plan_algorithms
from agent.models.schemas import SensorBatch, SensorFrame, SensorModality
from datetime import datetime, timezone


class FakeHTTPResponse:
    def __init__(self, payload: dict, status: int = 200):
        self._payload = payload
        self.status = status

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class AlgolibRunClientTest(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.copy()
        os.environ["ALGOLIB_BASE_URL"] = "http://127.0.0.1:8088"
        os.environ["TIA_ALGOLIB_CALL_MODE"] = "run"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    def test_list_algorithms_and_run(self):
        client = AlgorithmLibraryClient({"base_url": "http://127.0.0.1:8088", "call_mode": "run"})

        def fake_urlopen(req, timeout=None):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            if "/algorithms" in url and not url.rstrip("/").endswith("/run"):
                return FakeHTTPResponse(
                    {
                        "algorithms": [
                            {
                                "algorithm_id": "battlefield_rtdetr_detector",
                                "version": "1.0.0",
                                "backend_type": "python_http_service",
                                "status": "active",
                                "task_family": "detection",
                                "agent_card": {"summary": "det"},
                                "input_schema_summary": {"required": ["frames"]},
                            }
                        ]
                    }
                )
            if url.endswith("/run"):
                body = json.loads(req.data.decode("utf-8"))
                self.assertEqual(body["algorithm_id"], "battlefield_rtdetr_detector")
                self.assertEqual(body["backend_type"], "python_http_service")
                return FakeHTTPResponse(
                    {
                        "ok": True,
                        "algorithm_id": body["algorithm_id"],
                        "version": body["version"],
                        "outputs": {"detections": [{"class_name": "ship"}]},
                        "usage": {"latency_ms": 1.0},
                    }
                )
            raise AssertionError(url)

        with patch("agent.algorithm_library.client.urlopen", side_effect=fake_urlopen):
            algos = client.list_algorithms()
            self.assertEqual(algos[0]["algorithm_id"], "battlefield_rtdetr_detector")
            outputs = client.predict(
                "battlefield_rtdetr_detector",
                {"frames": []},
                version="1.0.0",
            )
            self.assertEqual(outputs["detections"][0]["class_name"], "ship")

            raw = client.run_algorithm(
                request_id="r1",
                trace_id="t1",
                call=AlgorithmRunCall(
                    algorithm_id="battlefield_rtdetr_detector",
                    version="1.0.0",
                    backend_type="python_http_service",
                    inputs={"frames": []},
                    params={},
                ),
            )
            self.assertTrue(raw["ok"])

    def test_runtime_catalog_exposes_track_threat_mounted_algorithms(self):
        catalog = build_algorithm_catalog(include_scheduling=True)
        algorithm_ids = {item["algorithm_id"] for item in catalog}

        self.assertGreaterEqual(len(catalog), 15)
        self.assertTrue(
            {
                "multimodal_feature_fuser",
                "target_type_classifier",
                "track_state_updater",
                "trajectory_predictor",
                "graph_relation_reasoner",
            }.issubset(algorithm_ids)
        )
        target_type = next(
            item for item in catalog if item["algorithm_id"] == "target_type_classifier"
        )
        self.assertEqual(
            target_type["predict_endpoint"],
            "http://127.0.0.1:9042/target_type_classifier/predict",
        )
        self.assertEqual(target_type["model_profile"]["parameter_count_text"], "rule-based")

        supcon_meta = next(
            item for item in catalog if item["algorithm_id"] == "supcon_meta_classifier"
        )
        self.assertEqual(
            supcon_meta["model_profile"]["parameter_count_text"],
            "296K (SupConMetaNet in_dim=1024 medium profile)",
        )
        self.assertEqual(supcon_meta["model_profile"]["parameter_count"], 295808)

    def test_run_error_raises(self):
        client = AlgorithmLibraryClient({"call_mode": "run", "base_url": "http://127.0.0.1:8088"})

        def fake_urlopen(req, timeout=None):
            return FakeHTTPResponse(
                {
                    "ok": False,
                    "error": {"code": "UPSTREAM_UNAVAILABLE", "message": "down"},
                    "outputs": {},
                }
            )

        with patch("agent.algorithm_library.client.urlopen", side_effect=fake_urlopen):
            with self.assertRaises(AlgorithmLibraryError):
                client.predict("battlefield_rtdetr_detector", {"frames": []})


class PlannerUsesRemoteCatalogTest(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.copy()
        os.environ["TIA_ALGORITHM_PLANNER"] = "llm"
        os.environ["ENABLE_LLM"] = "true"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    def test_llm_plan_uses_gateway_catalog(self):
        class FakeAlgolib:
            def list_algorithms(self, *, active_only=True):
                return [
                    {
                        "algorithm_id": "battlefield_rtdetr_detector",
                        "version": "1.0.0",
                        "backend_type": "python_http_service",
                        "status": "active",
                        "task_family": "detection",
                        "agent_card": {"summary": "det"},
                        "input_schema_summary": {"required": ["frames"]},
                    },
                    {
                        "algorithm_id": "edl_evidential_verifier",
                        "version": "1.0.0",
                        "backend_type": "python_http_service",
                        "status": "active",
                        "agent_card": {"summary": "edl"},
                        "input_schema_summary": {"required": ["detections"]},
                    },
                    {
                        "algorithm_id": "motr_neural_kalman_tracker",
                        "version": "1.0.0",
                        "backend_type": "python_http_service",
                        "status": "active",
                        "agent_card": {"summary": "motr"},
                        "input_schema_summary": {"required": ["verified_detections"]},
                    },
                    {
                        "algorithm_id": "knowledge_semantic_comm",
                        "version": "1.0.0",
                        "backend_type": "python_http_service",
                        "status": "active",
                        "agent_card": {"summary": "comm"},
                        "input_schema_summary": {"required": []},
                    },
                ]

        class FakeLLM:
            def chat_json(self, *, system_prompt, user_prompt):
                self.user_prompt = user_prompt
                return {
                    "intent": "min",
                    "algorithm_calls": [
                        {
                            "algorithm_id": "battlefield_rtdetr_detector",
                            "version": "1.0.0",
                            "backend_type": "python_http_service",
                            "inputs": {"hack": 1},
                            "params": {},
                            "reason": "det",
                        },
                        {
                            "algorithm_id": "edl_evidential_verifier",
                            "version": "1.0.0",
                            "backend_type": "python_http_service",
                            "inputs": {},
                            "params": {},
                            "reason": "edl",
                        },
                        {
                            "algorithm_id": "motr_neural_kalman_tracker",
                            "version": "1.0.0",
                            "backend_type": "python_http_service",
                            "inputs": {},
                            "params": {},
                            "reason": "trk",
                        },
                    ],
                    "missing_fields": [],
                    "explanation": "ok",
                }

        batch = SensorBatch(
            mission_id="wf",
            frames=[
                SensorFrame(
                    sensor_id="EO-1",
                    modality=SensorModality.EO_IR,
                    timestamp=datetime.now(timezone.utc),
                    payload={"image_uri": "https://x/a.png"},
                )
            ],
            context={},
        )
        llm = FakeLLM()
        plan = plan_algorithms(
            batch,
            config={"algorithm_planner": {"mode": "llm", "fallback_to_fixed": False}, "tool_llm": {"enable": True}},
            llm_client=llm,
            algolib_client=FakeAlgolib(),
        )
        self.assertEqual(plan.catalog_source, "algolib:/algorithms")
        self.assertIn("battlefield_rtdetr_detector", llm.user_prompt)
        self.assertTrue(plan.is_enabled("battlefield_rtdetr_detector"))
        self.assertEqual(plan.algorithm_calls[0].to_dict()["inputs"], {})


class GatewayAppTest(unittest.TestCase):
    def test_gateway_algorithms_and_run_forward(self):
        from fastapi.testclient import TestClient
        from services.tia_algolib_gateway.app.main import app

        client = TestClient(app)
        health = client.get("/health")
        self.assertEqual(health.status_code, 200)
        self.assertTrue(health.json().get("ok"))

        listed = client.get("/algorithms")
        self.assertEqual(listed.status_code, 200)
        algos = listed.json()["algorithms"]
        self.assertTrue(any(a["algorithm_id"] == "battlefield_rtdetr_detector" for a in algos))
        self.assertTrue(any(a["algorithm_id"] == "marl_ppo_task_scheduler" for a in algos))

        with patch(
            "services.tia_algolib_gateway.app.main._forward_predict",
            return_value={
                "ok": True,
                "algorithm_id": "battlefield_rtdetr_detector",
                "version": "1.0.0",
                "outputs": {"detections": []},
                "usage": {"latency_ms": 2.0},
            },
        ):
            resp = client.post(
                "/run",
                json={
                    "request_id": "r1",
                    "trace_id": "t1",
                    "algorithm_id": "battlefield_rtdetr_detector",
                    "version": "1.0.0",
                    "backend_type": "python_http_service",
                    "inputs": {"frames": []},
                    "params": {},
                },
            )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["ok"])


if __name__ == "__main__":
    unittest.main()
