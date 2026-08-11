"""AMOS adapter + TaskSchedulingAgent unit tests."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from task_scheduling_agent.amos_adapter import situation_from_amos
from task_scheduling_agent.agent import TaskSchedulingAgent
from task_scheduling_agent.engine import mock_schedule_from_situation

SAMPLE = ROOT / "examples" / "amos_schedule_inputs" / "sample_amos_request.json"


class TestAmosAdapter(unittest.TestCase):
    def test_sample_maps_core_fields(self):
        payload = json.loads(SAMPLE.read_text(encoding="utf-8"))
        situation = situation_from_amos(payload)

        self.assertGreaterEqual(len(situation.targets), 2)
        self.assertGreaterEqual(len(situation.sensors), 1)
        self.assertGreaterEqual(len(situation.strike_assets), 1)
        self.assertEqual(situation.phase, "recon")

        # UAV-LOWBAT: battery 0.08 → unavailable, high load
        low = next(s for s in situation.sensors if s.sensor_id == "UAV-LOWBAT")
        self.assertFalse(low.available)
        self.assertGreater(low.load, 0.8)

        # ARTY-1 ammo 0.6
        arty = next(a for a in situation.strike_assets if a.asset_id == "ARTY-1")
        self.assertTrue(arty.available)
        self.assertAlmostEqual(arty.remaining_ammo, 0.6, places=3)

    def test_out_of_window_task_downweighted(self):
        payload = {
            "now": "2026-08-11T08:05:00Z",
            "tasks": [
                {
                    "target_id": "OUT",
                    "threat_score": 0.9,
                    "damage_score": 0.1,
                    "time_window": {
                        "start": "2026-08-11T09:00:00Z",
                        "end": "2026-08-11T10:00:00Z",
                    },
                },
                {
                    "target_id": "IN",
                    "threat_score": 0.5,
                    "damage_score": 0.1,
                    "time_window": {
                        "start": "2026-08-11T08:00:00Z",
                        "end": "2026-08-11T08:30:00Z",
                    },
                },
            ],
            "platforms": [
                {
                    "platform_id": "S1",
                    "role": "sensor",
                    "available": True,
                    "battery": 0.9,
                    "link_quality": 0.9,
                }
            ],
        }
        situation = situation_from_amos(payload)
        by_id = {t.target_id: t for t in situation.targets}
        self.assertLess(by_id["OUT"].threat_score, by_id["IN"].threat_score)
        self.assertFalse(by_id["OUT"].needs_reattack)

    def test_low_link_marks_unavailable(self):
        payload = {
            "tasks": [{"target_id": "E1", "threat_score": 0.7}],
            "platforms": [
                {
                    "platform_id": "BAD-LINK",
                    "role": "sensor",
                    "available": True,
                    "battery": 0.9,
                    "link_quality": 0.1,
                }
            ],
        }
        situation = situation_from_amos(payload)
        self.assertFalse(situation.sensors[0].available)


class TestTaskSchedulingAgent(unittest.TestCase):
    def test_mock_run_sample(self):
        payload = json.loads(SAMPLE.read_text(encoding="utf-8"))
        agent = TaskSchedulingAgent(use_mock=True)
        result = agent.run(payload)
        self.assertEqual(result["algorithm"], "mock-heuristic")
        self.assertIn("sensor_assignments", result)
        self.assertIn("reattack_plan", result)
        self.assertIn("task_schedule", result)
        self.assertTrue(result["sensor_assignments"])

    def test_mock_schedule_skips_unavailable_sensor_target(self):
        payload = {
            "tasks": [
                {"target_id": "E1", "threat_score": 0.8, "damage_score": 0.2},
            ],
            "platforms": [
                {
                    "platform_id": "DEAD",
                    "role": "sensor",
                    "available": True,
                    "battery": 0.05,
                    "link_quality": 0.9,
                },
                {
                    "platform_id": "OK",
                    "role": "sensor",
                    "available": True,
                    "battery": 0.8,
                    "link_quality": 0.9,
                },
            ],
        }
        situation = situation_from_amos(payload)
        raw = mock_schedule_from_situation(situation)
        by_id = {a["sensor_id"]: a for a in raw["sensor_assignments"]}
        self.assertEqual(by_id["DEAD"]["priority"], "idle")
        self.assertIsNone(by_id["DEAD"]["target_id"])
        self.assertIsNotNone(by_id["OK"]["target_id"])


if __name__ == "__main__":
    unittest.main()
