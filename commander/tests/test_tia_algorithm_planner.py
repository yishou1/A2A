"""TIA 小模型算法规划单测。"""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from agent.algorithm_library.catalog import TIA_DEFAULT_PIPELINE, TIA_REQUIRED_ALGORITHMS
from agent.algorithm_library.planner_runtime import (
    AlgorithmPlannerError,
    plan_algorithms,
    resolve_planner_mode,
)
from agent.models.schemas import SensorBatch, SensorFrame, SensorModality
from agent.orchestrator import TacticalIntelligenceAgent
from agent.skills.perception.skill import PerceptionSkill


class FakeLLM:
    def __init__(self, plan: dict | None = None, *, raise_error: Exception | None = None):
        self.plan = plan
        self.raise_error = raise_error
        self.calls: list[dict] = []

    def chat_json(self, *, system_prompt: str, user_prompt: str) -> dict:
        self.calls.append({"system_prompt": system_prompt, "user_prompt": user_prompt})
        if self.raise_error:
            raise self.raise_error
        assert self.plan is not None
        return self.plan


def _batch(**ctx) -> SensorBatch:
    return SensorBatch(
        mission_id="wf-planner-test",
        frames=[
            SensorFrame(
                sensor_id="EO-1",
                modality=SensorModality.EO_IR,
                timestamp=datetime(2026, 8, 11, tzinfo=timezone.utc),
                payload={"image_uri": "https://example.local/frame.png"},
                metadata={"modality": "eo_ir"},
            )
        ],
        context={
            "command": "process_intelligence",
            "jamming_level": 0.0,
            "subscriber_agents": ["commander"],
            **ctx,
        },
    )


class PlannerRuntimeTest(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.copy()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    def test_fixed_planner_default_pipeline(self):
        os.environ["TIA_ALGORITHM_PLANNER"] = "fixed"
        plan = plan_algorithms(_batch(), config={})
        self.assertEqual(plan.mode, "fixed")
        self.assertEqual(
            [c.algorithm_id for c in plan.algorithm_calls],
            TIA_DEFAULT_PIPELINE,
        )

    def test_resolve_mode_env_overrides_yaml(self):
        os.environ["TIA_ALGORITHM_PLANNER"] = "llm"
        self.assertEqual(resolve_planner_mode({"algorithm_planner": {"mode": "fixed"}}), "llm")

    def test_llm_planner_selects_whitelist(self):
        os.environ["TIA_ALGORITHM_PLANNER"] = "llm"
        os.environ["ENABLE_LLM"] = "true"
        fake = FakeLLM(
            {
                "intent": "eo_track",
                "algorithm_calls": [
                    {
                        "algorithm_id": "battlefield_rtdetr_detector",
                        "version": "1.0.0",
                        "backend_type": "python_http_service",
                        "inputs": {"hack": True},
                        "params": {"confidence_threshold": 0.3},
                        "reason": "detect",
                    },
                    {
                        "algorithm_id": "edl_evidential_verifier",
                        "version": "1.0.0",
                        "backend_type": "python_http_service",
                        "inputs": {},
                        "params": {},
                        "reason": "verify",
                    },
                    {
                        "algorithm_id": "motr_neural_kalman_tracker",
                        "version": "1.0.0",
                        "backend_type": "python_http_service",
                        "inputs": {},
                        "params": {},
                        "reason": "track",
                    },
                    {
                        "algorithm_id": "knowledge_semantic_comm",
                        "version": "1.0.0",
                        "backend_type": "python_http_service",
                        "inputs": {},
                        "params": {},
                        "reason": "packet",
                    },
                ],
                "missing_fields": [],
                "explanation": "最小感知链路",
            }
        )
        plan = plan_algorithms(
            _batch(),
            config={"tool_llm": {"enable": True}, "algorithm_planner": {"mode": "llm"}},
            llm_client=fake,
        )
        self.assertEqual(plan.mode, "llm")
        self.assertTrue(fake.calls)
        self.assertIn("tactical_intelligence_agent", fake.calls[0]["system_prompt"])
        # LLM inputs 被丢弃
        for call in plan.algorithm_calls:
            self.assertEqual(call.to_dict()["inputs"], {})
        self.assertTrue(plan.is_enabled("battlefield_rtdetr_detector"))
        self.assertFalse(plan.is_enabled("synapse_rag_retriever"))
        self.assertEqual(plan.params_for("battlefield_rtdetr_detector").get("confidence_threshold"), 0.3)

    def test_llm_rejects_unknown_algorithm(self):
        os.environ["TIA_ALGORITHM_PLANNER"] = "llm"
        os.environ["ENABLE_LLM"] = "true"
        fake = FakeLLM(
            {
                "intent": "bad",
                "algorithm_calls": [
                    {
                        "algorithm_id": "not_a_real_algo",
                        "version": "1.0.0",
                        "backend_type": "python_http_service",
                        "inputs": {},
                        "params": {},
                    }
                ],
                "missing_fields": [],
                "explanation": "",
            }
        )
        with self.assertRaises(AlgorithmPlannerError):
            plan_algorithms(
                _batch(),
                config={"algorithm_planner": {"mode": "llm", "fallback_to_fixed": False}},
                llm_client=fake,
            )

    def test_llm_cannot_skip_required_auto_inject(self):
        os.environ["TIA_ALGORITHM_PLANNER"] = "llm"
        fake = FakeLLM(
            {
                "intent": "skip_required",
                "algorithm_calls": [
                    {
                        "algorithm_id": "knowledge_semantic_comm",
                        "version": "1.0.0",
                        "backend_type": "python_http_service",
                        "inputs": {},
                        "params": {},
                        "reason": "only comm",
                    }
                ],
                "missing_fields": [],
                "explanation": "",
            }
        )
        plan = plan_algorithms(
            _batch(),
            config={"algorithm_planner": {"mode": "llm", "fallback_to_fixed": False}},
            llm_client=fake,
        )
        for required in TIA_REQUIRED_ALGORITHMS:
            self.assertTrue(plan.is_enabled(required), required)
        # 顺序：感知必选在前
        ids = [c.algorithm_id for c in plan.algorithm_calls]
        self.assertLess(ids.index("battlefield_rtdetr_detector"), ids.index("knowledge_semantic_comm"))

    def test_llm_failure_falls_back_to_fixed(self):
        from tactical_intelligence_agent.llm.client import LLMClientError

        os.environ["TIA_ALGORITHM_PLANNER"] = "llm"
        fake = FakeLLM(raise_error=LLMClientError("down"))
        plan = plan_algorithms(
            _batch(),
            config={"algorithm_planner": {"mode": "llm", "fallback_to_fixed": True}},
            llm_client=fake,
        )
        self.assertEqual(plan.mode, "fixed")
        self.assertIn("llm_plan_failed", plan.fallback_reason)
        self.assertEqual(len(plan.algorithm_calls), len(TIA_DEFAULT_PIPELINE))


class PerceptionSkipTest(unittest.TestCase):
    def test_optional_frame_objects_are_omitted_instead_of_null(self):
        class Stub:
            def __init__(self, name, result):
                self.name = name
                self.result = result
                self.inputs = None

            def run(self, inputs):
                self.inputs = inputs
                return self.result

        skill = PerceptionSkill(use_mock=True, config={"execution_mode": "in_process"})
        skill.detector = Stub("det", [])
        skill.damage = Stub("dmg", [])
        skill.edl = Stub("edl", [])
        skill.tracker = Stub("trk", {"tracks": []})
        batch = SensorBatch(mission_id="no-visual-input", frames=[], context={})

        skill.execute(batch)

        self.assertNotIn("reference_frame", skill.damage.inputs)
        self.assertNotIn("visual_frame", skill.tracker.inputs)

    def test_skip_damage_and_no_scheduler(self):
        from agent.algorithm_library.planner_runtime import AlgorithmCall, AlgorithmPlan

        plan = AlgorithmPlan(
            mode="llm",
            intent="min",
            algorithm_calls=[
                AlgorithmCall(algorithm_id="battlefield_rtdetr_detector"),
                AlgorithmCall(algorithm_id="edl_evidential_verifier"),
                AlgorithmCall(algorithm_id="motr_neural_kalman_tracker"),
            ],
        )

        class Stub:
            def __init__(self, name, result):
                self.name = name
                self.result = result
                self.calls = 0

            def run(self, inputs):
                self.calls += 1
                return self.result

        skill = PerceptionSkill(use_mock=True, config={"execution_mode": "in_process"})
        skill.detector = Stub("det", [{"class_name": "ship", "confidence": 0.9, "bbox": [0, 0, 10, 10], "sensor_id": "EO-1"}])
        skill.damage = Stub("dmg", [])
        skill.edl = Stub("edl", [{"class_name": "ship", "confidence": 0.9, "bbox": [0, 0, 10, 10], "sensor_id": "EO-1"}])
        skill.tracker = Stub(
            "trk",
            {
                "tracks": [
                    {
                        "track_id": "T-0001",
                        "class_name": "ship",
                        "confidence": 0.9,
                        "geo": {"lat": 30.5, "lon": 114.3, "alt_m": 0},
                    }
                ]
            },
        )

        out = skill.execute(_batch(), prior_tracks=[], plan=plan)
        self.assertEqual(skill.damage.calls, 0)
        self.assertFalse(hasattr(skill, "scheduler"))
        self.assertEqual(skill.detector.calls, 1)
        self.assertEqual(skill.tracker.calls, 1)
        self.assertEqual(len(out.tracks), 1)
        self.assertIsNone(out.task_schedule)
        self.assertEqual(out.algorithm_trace.get("task_scheduling"), "delegated_to_task_scheduling_agent")
        self.assertEqual(out.algorithm_trace.get(skill.damage.name), "skipped")


class OrchestratorPlanProvenanceTest(unittest.TestCase):
    def test_packet_contains_llm_plan_provenance(self):
        from agent.algorithm_library.planner_runtime import AlgorithmCall, AlgorithmPlan
        from agent.models.schemas import CognitionOutput, PerceptionOutput, SemanticIntelligencePacket

        agent = TacticalIntelligenceAgent(
            use_mock=True,
            config={
                "execution_mode": "in_process",
                "algorithm_planner": {"mode": "llm"},
                "tool_llm": {"enable": True},
            },
        )

        planned = AlgorithmPlan(
            mode="llm",
            intent="test",
            algorithm_calls=[
                AlgorithmCall(algorithm_id=x)
                for x in (
                    "battlefield_rtdetr_detector",
                    "edl_evidential_verifier",
                    "motr_neural_kalman_tracker",
                    "knowledge_semantic_comm",
                )
            ],
            explanation="unit",
        )

        class FakePlanner:
            @staticmethod
            def side_effect(*_a, **_k):
                return planned

        class FakePerception:
            def execute(self, batch, prior_tracks=None, plan=None):
                self.plan = plan
                return PerceptionOutput(
                    tracks=[
                        {
                            "track_id": "T-0001",
                            "class_name": "ship",
                            "confidence": 0.9,
                            "geo": {"lat": 30.5, "lon": 114.3, "alt_m": 0},
                            "lat": 30.5,
                            "lon": 114.3,
                            "history_path": [],
                        }
                    ],
                    algorithm_trace={"det": "1"},
                )

        class FakeCognition:
            def execute(self, batch, perception, plan=None):
                return CognitionOutput(algorithm_trace={"cog": "1"})

        class FakeComm:
            def execute(self, *args, plan=None, **kwargs):
                return SemanticIntelligencePacket(
                    mission_id="wf-planner-test",
                    summary="ok",
                    tracks=[],
                    targets=[],
                    provenance={},
                )

        agent.perception = FakePerception()
        agent.cognition = FakeCognition()
        agent.communication = FakeComm()

        with patch(
            "agent.orchestrator.plan_algorithms",
            side_effect=FakePlanner.side_effect,
        ):
            with patch("agent.orchestrator.prepare_batch_for_inference", side_effect=lambda b: b):
                with patch("agent.orchestrator.finalize_batch_inference"):
                    with patch("agent.orchestrator.accumulate_track_history", side_effect=lambda tracks, *a, **k: tracks):
                        packet = agent.process(_batch())

        self.assertIn("llm_plan", packet.provenance)
        self.assertEqual(packet.provenance["llm_plan"]["mode"], "llm")
        self.assertIn("battlefield_rtdetr_detector", packet.provenance["selected_algorithms"])
        self.assertIs(agent.perception.plan, planned)


if __name__ == "__main__":
    unittest.main()
