from __future__ import annotations

import tempfile
import unittest

from artillery_agent.main import execute_artillery_command
from commander_agent.main import CommanderAgent
from evaluator_agent.main import evaluate_strike
from closed_loop_agent.closed_loop_core import _closed_loop_optimization
from services.a2a_algorithms_common.rule_based_execution import (
    simulate_rule_based_execution,
)


def execution_payload(*, decision: str = "approved", profile: str = "medium") -> dict:
    blocked = decision not in {"approved", "authorized"}
    return {
        "command": "precision_strike",
        "input": {
            "coordinates": "120.5E, 35.1N",
            "simulation_profile": profile,
            "execution_command": {
                "action": "precision_strike",
                "target_id": "T-001",
                "aim_point": {"x": 15.8, "y": 22.6},
                "command_id": "CMD-STR-001",
            },
            "execution_control_result": {
                "output_data": {
                    "phase": "strike",
                    "situation": {
                        "intel_confidence": 0.88,
                        "resource_readiness": 0.84,
                        "communication_quality": 0.91,
                        "threat_score": 0.64,
                        "supply_pressure": 0.2,
                    },
                    "authorization": {
                        "decision": decision,
                        "execution_blocked": blocked,
                    },
                }
            },
        },
    }


class RuleBasedExecutionTest(unittest.TestCase):
    def test_missing_upstream_data_is_not_reported_as_success(self):
        result = simulate_rule_based_execution(
            {"input": {"execution_command": {}}},
            action_kind="artillery",
            profile_name="medium",
        )

        output = result["output_data"]
        self.assertEqual(output["decision"], "insufficient_data")
        self.assertIsNone(output["simulated_effect_score"])
        self.assertFalse(output["is_real_execution"])
        self.assertFalse(output["actuator_connected"])

    def test_denied_authorization_blocks_simulation(self):
        result = simulate_rule_based_execution(
            execution_payload(decision="denied"),
            action_kind="artillery",
            profile_name="medium",
        )

        output = result["output_data"]
        self.assertEqual(output["decision"], "blocked")
        self.assertIn("authorization", output["rules_failed"])
        self.assertEqual(output["simulated_effect_score"], 0.0)

    def test_same_input_produces_same_decision_score_and_evidence(self):
        first = simulate_rule_based_execution(
            execution_payload(), action_kind="artillery", profile_name="medium"
        )
        second = simulate_rule_based_execution(
            execution_payload(), action_kind="artillery", profile_name="medium"
        )

        keys = ("decision", "simulated_effect_score", "evidence")
        self.assertEqual(
            {key: first["output_data"][key] for key in keys},
            {key: second["output_data"][key] for key in keys},
        )

    def test_profiles_apply_distinct_thresholds(self):
        payload = execution_payload()
        situation = payload["input"]["execution_control_result"]["output_data"]["situation"]
        situation.update(
            {
                "intel_confidence": 0.75,
                "resource_readiness": 0.72,
                "communication_quality": 0.74,
                "threat_score": 0.8,
            }
        )

        low = simulate_rule_based_execution(
            payload, action_kind="artillery", profile_name="low"
        )["output_data"]
        high = simulate_rule_based_execution(
            payload, action_kind="artillery", profile_name="high"
        )["output_data"]

        self.assertNotEqual(low["decision"], "blocked")
        self.assertEqual(high["decision"], "blocked")
        self.assertTrue(
            {"intel_confidence", "resource_readiness", "communication_quality", "threat_pressure"}
            & set(high["rules_failed"])
        )

    def test_effect_evaluator_requires_tagged_simulation_evidence(self):
        invalid, _ = evaluate_strike(
            {"input": {"strike_result": {"output_data": {"simulated_effect_score": 0.9}}}}
        )
        self.assertEqual(invalid["assessment_status"], "insufficient_data")
        self.assertEqual(invalid["eval_score"], 0)

        strike, _ = execute_artillery_command(execution_payload())
        evaluated, _ = evaluate_strike({"input": {"strike_result": strike}})
        self.assertEqual(evaluated["assessment_status"], "complete")
        self.assertEqual(
            evaluated["eval_score"],
            round(strike["output_data"]["simulated_effect_score"] * 100),
        )
        self.assertFalse(evaluated["is_real_evaluation"])


class CommanderClosedLoopMappingTest(unittest.TestCase):
    def test_simulated_damage_is_forwarded_with_provenance(self):
        strike, _ = execute_artillery_command(execution_payload())
        with tempfile.TemporaryDirectory() as state_dir:
            commander = CommanderAgent(
                mode="local", workflow_id="wf-rule-simulation", state_dir=state_dir
            )
            context = commander.initial_workflow_context()
            context["strike_result"] = [{"value": strike, "status": "completed"}]
            context["execution_control_result"] = [
                {
                    "value": execution_payload()["input"]["execution_control_result"],
                    "status": "completed",
                }
            ]

            task, _ = commander.build_task_payload(
                "closed_loop", context, activatity_index=7
            )

        target = task["input"]["targets"][0]
        self.assertEqual(target["target_id"], "T-001")
        self.assertEqual(
            target["damage_probability"],
            strike["output_data"]["damage_probability"],
        )
        self.assertEqual(target["effect_source"], "rule_based_simulation")
        self.assertFalse(target["is_real_execution"])
        self.assertEqual(target["damage_evidence"]["type"], "simulation")

    def test_closed_loop_reports_missing_target_fields_without_crashing(self):
        result = _closed_loop_optimization(
            {
                "targets": [
                    {
                        "target_id": "T-INCOMPLETE",
                        "detection_confidence": 0.9,
                        "threat_score": 0.7,
                        "damage_probability": 0.5,
                    }
                ],
                "target_count": 1,
                "cycles": 1,
                "enforce_min_target_count": False,
            }
        )

        output = result["output_data"]
        self.assertEqual(output["assessment_status"], "insufficient_data")
        missing = output["source_info"]["missing_by_target"]["T-INCOMPLETE"]
        self.assertEqual(set(missing), {"velocity_norm", "uncertainty", "ammo_need"})


if __name__ == "__main__":
    unittest.main()
