"""subskill_config 应把 inference 顶层键传给各算法后端。"""

from __future__ import annotations

import unittest

from agent.skills.base import subskill_config


class SubskillConfigTest(unittest.TestCase):
    def test_merges_inference_into_rt_detr(self):
        parent = {
            "detection_model": "models/checkpoints/battlefield_rtdetr.pt",
            "confidence_threshold": 0.35,
            "rt_detr_odconv": {"confidence_threshold": 0.4},
            "edl": {},
        }
        cfg = subskill_config(parent, "rt_detr_odconv")
        self.assertEqual(cfg["detection_model"], "models/checkpoints/battlefield_rtdetr.pt")
        self.assertEqual(cfg["confidence_threshold"], 0.4)
        self.assertNotIn("rt_detr_odconv", cfg)
        self.assertNotIn("edl", cfg)


if __name__ == "__main__":
    unittest.main()
