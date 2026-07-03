from __future__ import annotations

import csv
import pickle
import tempfile
import unittest
from pathlib import Path

from closed_loop_agent.mission_feature_adapter import (
    build_features_from_agent_results,
    build_features_from_sc2le_proxy,
    bundle_to_vector,
    verify_sc2le_proxy_no_result_leakage,
)
from closed_loop_agent.mission_feature_schema import (
    FEATURE_ORDER,
    FEATURE_SCHEMA,
    FEATURE_VERSION,
    LABEL_FIELDS,
    assert_no_label_leakage,
)
from closed_loop_agent.mission_model_service import (
    load_mission_model,
    load_model_metadata,
    predict_mission_assessment,
    split_replay_ids,
    train_sc2le_proxy_model,
)


class MissionFeatureSchemaTest(unittest.TestCase):
    def test_result_not_used_in_sc2le_proxy_inputs(self):
        bundle = build_features_from_sc2le_proxy(
            mmr=3200.0,
            apm=150.0,
            duration_sec=900.0,
            opponent_mmr=3000.0,
            result="Win",
        )
        assert_no_label_leakage(bundle["values"], context="sc2le_values")
        for forbidden in LABEL_FIELDS:
            self.assertNotIn(forbidden, bundle["values"])

    def test_win_loss_same_features_other_metadata_unchanged(self):
        leakage = verify_sc2le_proxy_no_result_leakage(
            mmr=3200.0,
            apm=150.0,
            duration_sec=900.0,
            opponent_mmr=3000.0,
        )
        self.assertTrue(leakage["passed"])
        self.assertEqual(leakage["win_values"], leakage["loss_values"])

    def test_replay_id_not_split_across_train_and_test(self):
        replay_ids = [f"replay-{index // 2}" for index in range(20)]
        split_ids = split_replay_ids(replay_ids, seed=20260412)
        overlap = split_ids["train"] & split_ids["test"]
        self.assertFalse(overlap)
        for replay_id in replay_ids:
            in_train = replay_id in split_ids["train"]
            in_test = replay_id in split_ids["test"]
            self.assertFalse(in_train and in_test)

    def test_adapters_share_field_order(self):
        proxy = build_features_from_sc2le_proxy(
            mmr=3000.0,
            apm=120.0,
            duration_sec=600.0,
            opponent_mmr=3100.0,
            result="Win",
        )
        agent = build_features_from_agent_results(
            {
                "damage_confirmation": {"output_data": {"engaged_targets": 10, "confirmed_destroyed": 7}},
                "resource_allocation": {"output_data": {"readiness": 0.8, "supply_pressure": 0.4}},
                "execution_control": {"output_data": {"latency_ms": 200}},
                "perception_detection": {"output_data": {"detections": [{"conf": 0.9}]}},
                "threat_evaluation": {"output_data": {"ranked_targets": [{"score": 0.6}]}},
                "communication": {"output_data": {"delivery_rate": 0.9}},
            },
            mode="fixture",
        )
        self.assertEqual(list(proxy["values"].keys()), list(FEATURE_ORDER))
        self.assertEqual(list(agent["values"].keys()), list(FEATURE_ORDER))
        self.assertEqual(bundle_to_vector(proxy), [proxy["values"][name] for name in FEATURE_ORDER])

    def test_all_features_clipped_to_unit_interval(self):
        bundle = build_features_from_sc2le_proxy(
            mmr=99999.0,
            apm=9999.0,
            duration_sec=99999.0,
            opponent_mmr=99999.0,
            result="Win",
        )
        for name in FEATURE_ORDER:
            value = bundle["values"][name]
            self.assertGreaterEqual(value, 0.0)
            self.assertLessEqual(value, 1.0)

    def test_feature_directions_match_schema(self):
        low_latency = build_features_from_agent_results(
            {"execution_control": {"output_data": {"latency_ms": 100}}},
            mode="fixture",
        )
        high_latency = build_features_from_agent_results(
            {"execution_control": {"output_data": {"latency_ms": 1800}}},
            mode="fixture",
        )
        self.assertGreater(
            low_latency["values"]["control_timeliness"],
            high_latency["values"]["control_timeliness"],
        )

        low_threat = build_features_from_agent_results(
            {"threat_evaluation": {"output_data": {"ranked_targets": [{"score": 0.2}]}}},
            mode="fixture",
        )
        high_threat = build_features_from_agent_results(
            {"threat_evaluation": {"output_data": {"ranked_targets": [{"score": 0.9}]}}},
            mode="fixture",
        )
        self.assertGreater(
            high_threat["values"]["threat_pressure"],
            low_threat["values"]["threat_pressure"],
        )
        self.assertEqual(FEATURE_SCHEMA["damage_rate"]["direction"], "higher_is_better")
        self.assertEqual(FEATURE_SCHEMA["threat_pressure"]["direction"], "higher_is_worse")

    def test_strict_mode_returns_insufficient_data(self):
        bundle = build_features_from_agent_results({}, mode="strict")
        self.assertEqual(bundle["assessment_status"], "insufficient_data")
        self.assertTrue(bundle["missing_fields"])

    def test_model_save_reload_prediction_consistent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            csv_path = temp_path / "samples.csv"
            model_path = temp_path / "model.pkl"
            metadata_path = temp_path / "model.metadata.json"
            rows = []
            for replay_index in range(12):
                for player_id in (1, 2):
                    rows.append(
                        {
                            "replay_id": f"replay-{replay_index}",
                            "player_id": player_id,
                            "mmr": 2800 + replay_index * 40 + player_id * 10,
                            "apm": 100 + replay_index * 5,
                            "duration_sec": 600 + replay_index * 30,
                            "opponent_mmr": 2900 + replay_index * 35,
                            "result": "Win" if replay_index % 2 == 0 else "Loss",
                        }
                    )
            with csv_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)

            train_sc2le_proxy_model(csv_path, seed=20260412, model_path=model_path, metadata_path=metadata_path)
            model_a = load_mission_model(model_path)
            model_b = load_mission_model(model_path)
            vector = bundle_to_vector(
                build_features_from_sc2le_proxy(
                    mmr=3100.0,
                    apm=140.0,
                    duration_sec=700.0,
                    opponent_mmr=3050.0,
                    result="Win",
                )
            )
            self.assertAlmostEqual(model_a.predict_one(vector), model_b.predict_one(vector), places=6)

    def test_online_agent_json_maps_to_mission_assessment(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            csv_path = temp_path / "samples.csv"
            model_path = temp_path / "model.pkl"
            metadata_path = temp_path / "model.metadata.json"
            rows = []
            for replay_index in range(10):
                for player_id in (1, 2):
                    rows.append(
                        {
                            "replay_id": f"replay-{replay_index}",
                            "player_id": player_id,
                            "mmr": 3000 + replay_index * 20,
                            "apm": 120 + replay_index,
                            "duration_sec": 800,
                            "opponent_mmr": 3050,
                            "result": "Win" if replay_index % 2 == 0 else "Loss",
                        }
                    )
            with csv_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)
            train_sc2le_proxy_model(csv_path, seed=20260412, model_path=model_path, metadata_path=metadata_path)

            results = {
                "perception_detection": {"output_data": {"detections": [{"conf": 0.9}, {"conf": 0.8}]}},
                "resource_allocation": {"output_data": {"readiness": 0.75, "supply_pressure": 0.55}},
                "execution_control": {"output_data": {"latency_ms": 300}},
                "threat_evaluation": {"output_data": {"ranked_targets": [{"score": 0.7}, {"score": 0.6}]}},
                "communication": {"output_data": {"delivery_rate": 0.92}},
                "damage_confirmation": {"output_data": {"engaged_targets": 40, "confirmed_destroyed": 30}},
            }
            bundle = build_features_from_agent_results(results, mode="fixture")
            assessment = predict_mission_assessment(
                bundle,
                model_path=model_path,
                metadata_path=metadata_path,
            )
            self.assertEqual(assessment["assessment_status"], "proxy_model_estimate")
            self.assertEqual(assessment["feature_version"], FEATURE_VERSION)
            self.assertIn(assessment["mission_result"], {"success", "failure"})
            self.assertIsNotNone(assessment["mission_completion"])
            metadata = load_model_metadata(metadata_path)
            self.assertEqual(metadata["model_source"], "sc2le_proxy")


if __name__ == "__main__":
    unittest.main()
