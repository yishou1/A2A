"""Tests for algolib bridge and agent backend fallbacks."""
from __future__ import annotations

import os
import unittest
from unittest import mock

from algolib_bridge.client import AlgorithmLibraryClient, AlgorithmLibraryError
from algolib_bridge.config import AlgolibSettings, use_algolib_backend
from closed_loop_agent.algolib_runtime import run_closed_loop_with_backend
from execution_control_agent.algolib_runtime import run_execution_control_with_backend
from a2a_sdk import AgentRuntimeSDK
from execution_control_agent.main import ExecutionControlAgent


class AlgolibBridgeConfigTest(unittest.TestCase):
    def test_default_backend_is_local(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("A2A_ALGORITHM_BACKEND", None)
            os.environ.pop("EXECUTION_CONTROL_BACKEND", None)
            self.assertFalse(use_algolib_backend(agent_backend_env="EXECUTION_CONTROL_BACKEND"))

    def test_agent_env_overrides_global(self):
        with mock.patch.dict(
            os.environ,
            {
                "A2A_ALGORITHM_BACKEND": "local",
                "EXECUTION_CONTROL_BACKEND": "algolib",
                "ALGOLIB_TRANSPORT": "direct",
            },
            clear=False,
        ):
            settings = AlgolibSettings.load(agent_backend_env="EXECUTION_CONTROL_BACKEND")
            self.assertEqual(settings.backend, "algolib")
            self.assertEqual(settings.transport, "direct")


class ExecutionControlBackendTest(unittest.TestCase):
    def test_local_backend_still_works(self):
        with mock.patch.dict(os.environ, {"A2A_ALGORITHM_BACKEND": "local"}, clear=False):
            os.environ.pop("EXECUTION_CONTROL_BACKEND", None)
            result = run_execution_control_with_backend(
                {
                    "phase": "strike",
                    "results": {
                        "threat_evaluation": {"output_data": {"priority_score": 0.75}},
                        "perception_detection": {"output_data": {"detections": [{"conf": 0.9}]}},
                        "resource_allocation": {"output_data": {"readiness": 0.85}},
                        "communication": {"output_data": {"delivery_rate": 0.9}},
                    },
                }
            )
            self.assertEqual(result["task_type"], "execution_control")
            self.assertEqual(result["output_data"].get("backend"), "local")
            self.assertIn("commands", result["output_data"])

    def test_algolib_failure_falls_back_local(self):
        with mock.patch.dict(
            os.environ,
            {
                "EXECUTION_CONTROL_BACKEND": "algolib",
                "ALGOLIB_TRANSPORT": "direct",
                "ALGOLIB_FALLBACK_LOCAL": "true",
            },
            clear=False,
        ):
            with mock.patch(
                "execution_control_agent.algolib_runtime.run_execution_control_via_algolib",
                side_effect=AlgorithmLibraryError("down"),
            ):
                result = run_execution_control_with_backend({"phase": "strike", "results": {}})
            self.assertEqual(result["output_data"].get("backend"), "local_fallback")
            self.assertTrue(
                any(str(item).startswith("algolib_fallback:") for item in result["output_data"].get("warnings", []))
            )


class ClosedLoopBackendTest(unittest.TestCase):
    def test_algolib_failure_falls_back_local(self):
        with mock.patch.dict(
            os.environ,
            {
                "CLOSED_LOOP_BACKEND": "algolib",
                "ALGOLIB_FALLBACK_LOCAL": "true",
            },
            clear=False,
        ):
            with mock.patch(
                "closed_loop_agent.algolib_runtime.run_closed_loop_via_algolib",
                side_effect=AlgorithmLibraryError("down"),
            ):
                result = run_closed_loop_with_backend({"seed": 1, "cycles": 1, "target_count": 4})
            self.assertEqual(result["output_data"].get("backend"), "local_fallback")


class DamageInputModeInterfaceTest(unittest.TestCase):
    def test_auto_prefers_images_when_complete(self):
        from closed_loop_agent.algolib_runtime import (
            build_damage_assessor_inputs,
            has_images_inputs,
            resolve_damage_input_mode,
        )

        target = {
            "target_id": "T-1",
            "pre_image": {"path": "pre.png"},
            "post_image": {"path": "post.png"},
            "polygon": [[0, 0], [10, 0], [10, 10], [0, 10]],
            "spectral_delta": 0.2,
        }
        self.assertTrue(has_images_inputs(target))
        self.assertEqual(resolve_damage_input_mode(target), "images")
        inputs, mode, warnings = build_damage_assessor_inputs(target, sample_id="T-1")
        self.assertEqual(mode, "images")
        self.assertEqual(inputs["input_mode"], "images")
        self.assertEqual(inputs["polygon"][0], [0, 0])
        self.assertFalse(warnings)

    def test_auto_falls_back_to_features(self):
        from closed_loop_agent.algolib_runtime import build_damage_assessor_inputs

        target = {
            "target_id": "T-2",
            "pre_image": {"path": "pre.png"},
            # missing post + polygon
            "spectral_delta": 0.3,
            "texture_delta": 0.1,
            "heat_signature": 0.2,
        }
        inputs, mode, _warnings = build_damage_assessor_inputs(target, preferred_mode="auto")
        self.assertEqual(mode, "features")
        self.assertEqual(inputs["input_mode"], "features")
        self.assertIn("spectral_delta", inputs["handcrafted_features"])

    def test_nested_image_pair_and_force_features(self):
        from closed_loop_agent.algolib_runtime import build_damage_assessor_inputs

        target = {
            "image_pair": {"pre": "base64pre", "post": "base64post"},
            "geometry": {"polygon": [[1, 1], [2, 1], [2, 2]]},
            "pre_area": 0.1,
            "spectral_delta": 0.4,
        }
        inputs, mode, _ = build_damage_assessor_inputs(target, preferred_mode="images")
        self.assertEqual(mode, "images")
        inputs_f, mode_f, _ = build_damage_assessor_inputs(target, preferred_mode="features")
        self.assertEqual(mode_f, "features")
        self.assertEqual(inputs_f["input_mode"], "features")


class AgentRuntimeSdkTest(unittest.TestCase):
    def test_from_agent_builds_registration_metadata(self):
        agent = ExecutionControlAgent(port=18017)
        runtime = AgentRuntimeSDK.from_agent(agent, heartbeat_interval=5.0)
        metadata = runtime.build_registration_metadata()
        self.assertEqual(metadata.get("role"), "execution_control")
        self.assertIn("skill_ids", metadata)
        self.assertTrue(metadata.get("skill_ids"))
        heartbeat = runtime.heartbeat_metadata()
        self.assertIn("status", heartbeat)
        self.assertIn("agent_run_state", heartbeat)


class DirectClientMockTest(unittest.TestCase):
    def test_direct_run_outputs(self):
        settings = AlgolibSettings(
            backend="algolib",
            transport="direct",
            base_url="http://127.0.0.1:8088",
            timeout_seconds=5.0,
            fallback_local=True,
            default_version="1.0.0",
            default_backend_type="python_http_service",
        )
        client = AlgorithmLibraryClient(settings)
        fake = {
            "ok": True,
            "outputs": {"phase": "strike", "commands": [], "tracks": [], "coordination": {"groups": []}},
        }
        with mock.patch("algolib_bridge.client.requests.post") as post:
            response = mock.Mock()
            response.raise_for_status = mock.Mock()
            response.json = mock.Mock(return_value=fake)
            post.return_value = response
            outputs = client.run_outputs(
                algorithm_id="execution_control_planner",
                inputs={"phase": "strike", "results": {}},
            )
            self.assertEqual(outputs["phase"], "strike")
            self.assertIn("/predict", post.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
