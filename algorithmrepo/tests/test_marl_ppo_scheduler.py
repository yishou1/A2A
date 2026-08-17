"""MARL-PPO 任务调度子技能测试。"""

from __future__ import annotations

import unittest

from agent.models.schemas import PerceptionOutput, SensorBatch, SensorFrame, SensorModality
from agent.skills.base import subskill_config
from agent.skills.perception.marl_ppo_scheduler import MARLPPOScheduler
from agent.skills.perception.skill import PerceptionSkill
from agent.training.battlefield_scheduling_env import (
    BattlefieldSchedulingEnv,
    BattlefieldSchedulingState,
    SchedulingSensor,
    SchedulingTarget,
    StrikeAsset,
    situation_from_perception,
)


class MARLPPOSchedulerTest(unittest.TestCase):
    def test_subskill_config_key_registered(self):
        parent = {"marl_ppo_scheduler": {"lr": 1e-4}, "edl": {}}
        cfg = subskill_config(parent, "marl_ppo_scheduler")
        self.assertEqual(cfg["lr"], 1e-4)
        self.assertNotIn("marl_ppo_scheduler", cfg)

    def test_mock_scheduler_produces_assignments(self):
        scheduler = MARLPPOScheduler(use_mock=True)
        tracks = [
            {"track_id": "T-1", "class_name": "tank", "geo": {"lat": 30.52, "lon": 114.38}},
            {"track_id": "T-2", "class_name": "vehicle", "geo": {"lat": 30.51, "lon": 114.39}},
        ]
        detections = [
            {"track_id": "T-1", "confidence": 0.9, "damage_score": 0.3, "class_name": "tank"},
            {"track_id": "T-2", "confidence": 0.8, "damage_score": 0.7, "class_name": "vehicle"},
        ]
        frames = [
            {"sensor_id": "EO-FWD-1", "modality": "eo_ir", "metadata": {"platform_lat": 30.5, "platform_lon": 114.37}},
            {"sensor_id": "SAR-1", "modality": "sar", "metadata": {"platform_lat": 30.5, "platform_lon": 114.37}},
        ]
        result = scheduler.run(
            {
                "tracks": tracks,
                "detections": detections,
                "batch_context": {"phase": "bda", "jamming_level": 0.2},
                "frames": frames,
            }
        )
        self.assertGreaterEqual(len(result["sensor_assignments"]), 1)
        self.assertIn("reattack_plan", result)

    def test_situation_from_perception_bda_reattack(self):
        tracks = [{"track_id": "T-1", "geo": {"lat": 30.52, "lon": 114.38}, "class_name": "tank"}]
        detections = [{"track_id": "T-1", "confidence": 0.85, "damage_score": 0.4, "class_name": "tank"}]
        frames = [{"sensor_id": "EO-FWD-1", "modality": "eo_ir", "metadata": {}}]
        sit = situation_from_perception(tracks, detections, {"phase": "bda"}, frames)
        self.assertEqual(len(sit.targets), 1)
        self.assertTrue(sit.targets[0].needs_reattack)

    def test_env_step_returns_rewards(self):
        env = BattlefieldSchedulingEnv()
        state = BattlefieldSchedulingState(
            targets=[
                SchedulingTarget("T-1", 0.8, 0.3, 0.9, 30.52, 114.38, True, "tank"),
            ],
            sensors=[SchedulingSensor("EO-1", "eo_ir", True, 0.0, 30.5, 114.37)],
            strike_assets=[StrikeAsset("ARTY-1", "artillery")],
            jamming_level=0.1,
            phase="bda",
        )
        env.reset(state)
        obs, rewards, done, info = env.step([1, 1])
        self.assertTrue(done)
        self.assertEqual(len(rewards), 2)
        self.assertIn("sensor_assignments", info)

    def test_packet_includes_task_schedule(self):
        from agent.models.schemas import CognitionOutput, PerceptionOutput, SensorAssignment, TaskSchedulePlan
        from agent.skills.communication.skill import CommunicationSkill

        schedule = TaskSchedulePlan(
            sensor_assignments=[
                SensorAssignment(sensor_id="EO-1", target_id="T-1", priority="high"),
            ],
            algorithm="mock-heuristic",
        )
        perception = PerceptionOutput(
            task_schedule=schedule,
            algorithm_trace={"MARL-PPO-Task-Scheduler": "1 sensor tasks, 0 reattack"},
        )
        cognition = CognitionOutput()
        comm = CommunicationSkill(use_mock=True)
        packet = comm.execute("TEST-001", perception, cognition)
        self.assertIsNotNone(packet.task_schedule)
        assert packet.task_schedule is not None
        self.assertEqual(len(packet.task_schedule.sensor_assignments), 1)
        self.assertEqual(packet.task_schedule.sensor_assignments[0].sensor_id, "EO-1")

    def test_perception_skill_includes_task_schedule(self):
        skill = PerceptionSkill(use_mock=True, config={})
        batch = SensorBatch(
            mission_id="TEST-MARL-PPO",
            frames=[
                SensorFrame(
                    sensor_id="EO-FWD-1",
                    modality=SensorModality.EO_IR,
                    payload={"scene_tag": "test"},
                )
            ],
            context={"phase": "recon", "jamming_level": 0.0},
        )
        out = skill.execute(batch)
        self.assertIsInstance(out, PerceptionOutput)
        self.assertIsNotNone(out.task_schedule)
        assert out.task_schedule is not None
        self.assertIn("MARL-PPO-Task-Scheduler", out.algorithm_trace)


if __name__ == "__main__":
    unittest.main()
