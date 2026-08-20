"""Algolib closed-loop / execution-control orchestration improvements."""
from __future__ import annotations

import os
import unittest
from unittest import mock

from algolib_bridge.client import AlgorithmLibraryError
from closed_loop_agent.algolib_runtime import run_closed_loop_via_algolib
from execution_control_agent.algolib_runtime import (
    run_execution_control_via_algolib,
    validate_planner_outputs,
)


def _mock_run_outputs(algorithm_id: str, inputs: dict, **kwargs):
    if algorithm_id == "mission_feature_adapter":
        return {
            "feature_version": "mission_features_v2",
            "assessment_status": "ready",
            "values": {
                "damage_rate": 0.7,
                "asset_readiness": 0.8,
                "control_timeliness": 0.85,
                "intel_confidence": 0.9,
                "threat_pressure": 0.6,
                "ammo_pressure": 0.4,
                "comm_quality": 0.92,
            },
            "warnings": [],
        }
    if algorithm_id == "mission_completion_scorer":
        return {
            "mission_completion": 0.72,
            "mission_result": "success",
            "threshold": 0.5,
            "assessment_status": "proxy_model_estimate",
            "feature_version": "mission_features_v2",
            "warnings": [],
        }
    if algorithm_id == "xbd_damage_assessor":
        return {
            "damage_probability": 0.61,
            "damage_label": 1,
            "damage_result": "damaged",
            "assessment_status": "model_estimate",
            "input_mode": inputs.get("input_mode"),
        }
    if algorithm_id == "closed_loop_decision_advisor":
        return {
            "action": "reallocate_sensor",
            "effect_delta": 0.08,
            "recommendation": "reallocate sensors",
        }
    if algorithm_id == "execution_control_planner":
        phase = inputs.get("phase") or "strike"
        role = "artillery" if phase == "strike" else "assault"
        return {
            "phase": phase,
            "commands": [
                {
                    "command_id": "CMD-1",
                    "executor_role": role,
                    "action": "precision_strike" if phase == "strike" else "assault_push",
                    "aim_point": {"x": 1.0, "y": 2.0},
                    "priority": 0.9,
                }
            ],
            "tracks": [],
            "coordination": {"groups": []},
            "matched_rules": [{"confidence": 0.88}],
            "prediction_details": [],
            "latency_ms": 12.0,
        }
    raise AssertionError(f"unexpected algorithm_id={algorithm_id}")


def _mock_run_algorithm(*, request_id: str, trace_id: str, call):
    return {
        "ok": True,
        "request_id": request_id,
        "trace_id": trace_id,
        "algorithm_id": call.algorithm_id,
        "version": call.version,
        "outputs": _mock_run_outputs(call.algorithm_id, call.inputs),
        "usage": {"latency_ms": 4.5},
    }


class ClosedLoopAlgolibOrchestrationTest(unittest.TestCase):
    def test_closed_loop_llm_stage_plans_once_and_reuses(self):
        stage_plan = {
            "intent": "closed_loop_stage_plan",
            "explanation": "plan once",
            "algorithm_calls": [
                {
                    "task": "mission_feature_adaptation",
                    "algorithm_id": "mission_feature_adapter",
                    "version": "1.0.0",
                    "backend_type": "python_http_service",
                    "params": {"mode": "hybrid"},
                    "reason": "feature adapter",
                },
                {
                    "task": "mission_completion_scoring",
                    "algorithm_id": "mission_completion_scorer",
                    "version": "1.0.0",
                    "backend_type": "python_http_service",
                    "params": {},
                    "reason": "completion scorer",
                },
                {
                    "task": "xbd_damage_assessment",
                    "algorithm_id": "xbd_damage_assessor",
                    "version": "1.0.0",
                    "backend_type": "python_http_service",
                    "params": {"damage_input_mode": "features"},
                    "reason": "damage assessor",
                },
                {
                    "task": "closed_loop_decision_advice",
                    "algorithm_id": "closed_loop_decision_advisor",
                    "version": "1.0.0",
                    "backend_type": "python_http_service",
                    "params": {},
                    "reason": "advisor",
                },
            ],
            "missing_fields": [],
        }
        algorithms = [
            {
                "algorithm_id": "mission_feature_adapter",
                "version": "1.0.0",
                "backend_type": "python_http_service",
            },
            {
                "algorithm_id": "mission_completion_scorer",
                "version": "1.0.0",
                "backend_type": "python_http_service",
            },
            {
                "algorithm_id": "xbd_damage_assessor",
                "version": "1.0.0",
                "backend_type": "python_http_service",
            },
            {
                "algorithm_id": "closed_loop_decision_advisor",
                "version": "1.0.0",
                "backend_type": "python_http_service",
            },
        ]
        with mock.patch.dict(
            os.environ,
            {
                "ALGOLIB_ENABLE_LLM": "true",
                "ALGOLIB_LLM_BASE_URL": "http://127.0.0.1:9999/v1",
                "ALGOLIB_LLM_MODEL": "qwen",
                "ALGOLIB_LLM_API_KEY": "secret",
            },
            clear=False,
        ):
            with mock.patch(
                "closed_loop_agent.algolib_runtime.AlgorithmLibraryClient.list_algorithms",
                return_value=algorithms,
            ):
                with mock.patch(
                    "algolib_bridge.llm_planner.OpenAICompatiblePlannerClient.chat_json",
                    return_value=stage_plan,
                ) as chat_json:
                    with mock.patch(
                        "closed_loop_agent.algolib_runtime.AlgorithmLibraryClient.run_algorithm",
                        side_effect=_mock_run_algorithm,
                    ):
                        result = run_closed_loop_via_algolib(
                            {
                                "seed": 7,
                                "cycles": 2,
                                "target_count": 2,
                                "enforce_min_target_count": False,
                                "results": {
                                    "threat_evaluation": {"output_data": {"priority_score": 0.7}},
                                },
                            }
                        )

        output = result["output_data"]
        self.assertEqual(chat_json.call_count, 1)
        self.assertEqual(output["selected_algorithms"], [
            "mission_feature_adapter",
            "mission_completion_scorer",
            "xbd_damage_assessor",
            "closed_loop_decision_advisor",
        ])
        self.assertEqual(len(output["llm_algorithm_plans"]), 1)
        self.assertEqual(output["llm_algorithm_plans"][0]["mode"], "llm")
        self.assertGreaterEqual(len(output["algorithm_invocations"]), 8)
        first_invocation = output["algorithm_invocations"][0]
        self.assertEqual(first_invocation["algorithm_id"], "mission_feature_adapter")
        self.assertEqual(first_invocation["duration_ms"], 4.5)
        self.assertIn("input", first_invocation)
        self.assertIn("output", first_invocation)
        self.assertIn("algorithm_calls", output)

    def test_live_targets_accept_null_optional_numeric_fields(self):
        null_numeric_target = {
            "target_id": "TRK-AMOS-01",
            "target_class": "ship",
            "detection_confidence": None,
            "threat_score": None,
            "initial_effect": None,
            "normalized_distance": None,
            "pre_area": None,
            "spectral_delta": None,
            "texture_delta": None,
            "heat_signature": None,
            "crater_density": None,
            "velocity_norm": None,
            "uncertainty": None,
            "ammo_need": None,
        }
        with mock.patch(
            "closed_loop_agent.algolib_runtime.AlgorithmLibraryClient.run_outputs",
            side_effect=_mock_run_outputs,
        ):
            result = run_closed_loop_via_algolib({
                "seed": 3,
                "cycles": 1,
                "target_count": 1,
                "enforce_min_target_count": False,
                "targets": [null_numeric_target],
            })

        output = result["output_data"]
        self.assertEqual(output["execution_control"]["processed_targets"], 1)
        self.assertEqual(output["effect_assessment"]["target_assessments"][0]["target_id"], "TRK-AMOS-01")

    def test_synthesizes_targets_and_commander_envelope(self):
        with mock.patch.dict(
            os.environ,
            {"CLOSED_LOOP_BACKEND": "algolib", "ALGOLIB_TRANSPORT": "direct"},
            clear=False,
        ):
            with mock.patch(
                "closed_loop_agent.algolib_runtime.AlgorithmLibraryClient.run_outputs",
                side_effect=_mock_run_outputs,
            ):
                result = run_closed_loop_via_algolib(
                    {
                        "seed": 7,
                        "cycles": 2,
                        "target_count": 3,
                        "enforce_min_target_count": False,
                        "results": {
                            "threat_evaluation": {"output_data": {"priority_score": 0.7}},
                        },
                    }
                )
        output = result["output_data"]
        self.assertEqual(output["backend"], "algolib")
        self.assertEqual(output["execution_control"]["processed_targets"], 3)
        self.assertEqual(output["execution_control"]["control_cycles"], 2)
        self.assertEqual(len(output["execution_control"]["commands"]), 3)
        self.assertIn("target_assessments", output["effect_assessment"])
        self.assertEqual(len(output["closed_loop_optimization"]["history"]), 2)
        self.assertIn("requirement_report", output)
        self.assertIn("meets_requirements", output)
        self.assertTrue(any(str(w).startswith("targets_synthesized_from_target_count") for w in output["warnings"]))
        self.assertGreaterEqual(output["mission_completion_final"], 0.0)
        self.assertEqual(
            output["requirement_report"]["xbd_damage_accuracy_note"],
            "not_evaluated_in_algolib_mode_use_local_for_offline_gates",
        )

    def test_beachhead_style_target_count_fifty(self):
        with mock.patch(
            "closed_loop_agent.algolib_runtime.AlgorithmLibraryClient.run_outputs",
            side_effect=_mock_run_outputs,
        ):
            result = run_closed_loop_via_algolib(
                {
                    "seed": 1,
                    "cycles": 1,
                    "target_count": 50,
                    "results": {},
                }
            )
        output = result["output_data"]
        self.assertEqual(output["execution_control"]["processed_targets"], 50)
        self.assertTrue(output["requirement_report"]["meets_target_count"])
        self.assertTrue(output["requirement_report"]["sc2le_proxy_model_loaded"])


class ExecutionControlContractTest(unittest.TestCase):
    def test_validate_planner_outputs_detects_missing_role(self):
        problems = validate_planner_outputs(
            {"commands": [{"action": "precision_strike"}]},
            phase="strike",
        )
        self.assertTrue(any("missing_executor_role" in item for item in problems))

    def test_algolib_success_wrap(self):
        with mock.patch.dict(os.environ, {"ALGOLIB_ENABLE_LLM": "false"}, clear=False):
            with mock.patch(
                "execution_control_agent.algolib_runtime.AlgorithmLibraryClient.run_outputs",
                side_effect=_mock_run_outputs,
            ):
                result = run_execution_control_via_algolib({"phase": "strike", "results": {}})
        self.assertEqual(result["output_data"]["backend"], "algolib")
        self.assertEqual(result["output_data"]["commands"][0]["executor_role"], "artillery")
        invocation = result["output_data"]["algorithm_invocations"][0]
        self.assertEqual(invocation["algorithm_id"], "execution_control_planner")
        self.assertEqual(invocation["duration_ms"], 12.0)
        self.assertEqual(invocation["input"]["phase"], "strike")
        self.assertIn("commands", invocation["output"])

    def test_algolib_llm_planning_success_wrap(self):
        def planned_outputs(**kwargs):
            return _mock_run_outputs(kwargs["default_algorithm_id"], kwargs["inputs"]), {
                "intent": "planned",
                "algorithm_calls": [{"algorithm_id": kwargs["default_algorithm_id"]}],
            }

        with mock.patch.dict(os.environ, {"ALGOLIB_ENABLE_LLM": "true"}, clear=False):
            with mock.patch(
                "execution_control_agent.algolib_runtime.AlgorithmLibraryClient.run_outputs_with_planning",
                side_effect=planned_outputs,
            ):
                result = run_execution_control_via_algolib({"phase": "assault", "results": {}})
        self.assertEqual(result["output_data"]["commands"][0]["executor_role"], "assault")
        self.assertEqual(result["output_data"]["llm_plan"]["intent"], "planned")

    def test_contract_failure_falls_back_local(self):
        def bad_planner(algorithm_id, inputs, **kwargs):
            return {
                "phase": "strike",
                "commands": [{"aim_point": {"x": 1, "y": 2}}],  # missing role/action
                "matched_rules": [],
            }

        with mock.patch.dict(
            os.environ,
            {
                "EXECUTION_CONTROL_BACKEND": "algolib",
                "ALGOLIB_FALLBACK_LOCAL": "true",
                "ALGOLIB_ENABLE_LLM": "false",
            },
            clear=False,
        ):
            with mock.patch(
                "execution_control_agent.algolib_runtime.AlgorithmLibraryClient.run_outputs",
                side_effect=bad_planner,
            ):
                from execution_control_agent.algolib_runtime import run_execution_control_with_backend

                result = run_execution_control_with_backend({"phase": "strike", "results": {}})
        self.assertEqual(result["output_data"]["backend"], "local_fallback")
        self.assertEqual(
            result["output_data"]["algorithm_invocations"][0]["backend_type"],
            "local_fallback",
        )
        self.assertIn("commands", result["output_data"]["algorithm_invocations"][0]["output"])
        self.assertTrue(
            any("contract failed" in str(item) or "algolib_fallback" in str(item) for item in result["output_data"]["warnings"])
        )


if __name__ == "__main__":
    unittest.main()
