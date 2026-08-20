import time
import unittest
from tempfile import TemporaryDirectory

from integrated_system.mission_library import get_default_demo_mission, list_demo_missions
from integrated_system.orchestrator import IntegratedDemoOrchestrator


class IntegratedDemoOrchestratorTest(unittest.TestCase):
    def test_demo_workflow_runs_to_completion(self):
        with TemporaryDirectory() as temp_dir:
            orchestrator = IntegratedDemoOrchestrator(state_dir=temp_dir, max_workflows=1)
            try:
                mission = orchestrator.submit_mission(get_default_demo_mission())
                workflow_id = mission["workflow_id"]
                deadline = time.time() + 20
                while time.time() < deadline:
                    state = orchestrator.get_mission(workflow_id)
                    if state["status"] in {"completed", "failed"}:
                        break
                    time.sleep(0.05)
                state = orchestrator.get_mission(workflow_id)
                self.assertEqual(state["status"], "completed")
                self.assertIn("effect_evaluation", state["blackboard"]["results"])
                tracking = state["blackboard"]["results"]["tracking"]["result"]
                self.assertGreaterEqual(tracking["frames_processed"], 5)
                report = orchestrator.get_mission_report(workflow_id)
                self.assertEqual(report["workflow_id"], workflow_id)
                self.assertIn("capability_cards", report)
                threat_card = next(card for card in report["capability_cards"] if card["capability"] == "threat_assessment")
                self.assertTrue(threat_card["bullets"])
                self.assertTrue(any("保护目标" in bullet for bullet in threat_card["bullets"]))
                planning_card = next(card for card in report["capability_cards"] if card["capability"] == "decision_planning")
                planning_section = next(
                    section for section in planning_card["detail_sections"] if section.get("title") == "候选方案卡片"
                )
                self.assertEqual(len(planning_section["cards"]), 3)
            finally:
                orchestrator.shutdown()

    def test_adjustment_is_recorded(self):
        with TemporaryDirectory() as temp_dir:
            orchestrator = IntegratedDemoOrchestrator(state_dir=temp_dir, max_workflows=1)
            try:
                mission = orchestrator.submit_mission(
                    {
                        "objective": "Delay hostile group",
                        "contacts": [],
                        "friendly_platforms": [],
                        "success_threshold": 0.7,
                        "max_replans": 0,
                    }
                )
                workflow_id = mission["workflow_id"]
                orchestrator.adjust_mission(
                    workflow_id,
                    {
                        "note": "Favor containment over strike.",
                        "planning_focus": "containment",
                        "success_threshold": 0.5,
                    },
                )
                deadline = time.time() + 20
                while time.time() < deadline:
                    state = orchestrator.get_mission(workflow_id)
                    if state["status"] in {"completed", "failed"}:
                        break
                    time.sleep(0.05)
                state = orchestrator.get_mission(workflow_id)
                adjustments = state["blackboard"]["operator"]["adjustments"]
                self.assertTrue(adjustments)
            finally:
                orchestrator.shutdown()

    def test_demo_mission_library_contains_multiple_templates(self):
        library = list_demo_missions()
        self.assertGreaterEqual(len(library), 5)
        self.assertTrue(all(item["frame_count"] >= 5 for item in library))


if __name__ == "__main__":
    unittest.main()
