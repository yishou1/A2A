from __future__ import annotations

import tempfile
import unittest

from bpel_workflow import BPELWorkflowDefinition
from commander_agent.task_decomposer import TaskDecomposer


class TaskDecomposerTest(unittest.TestCase):
    def test_default_task_goal_generates_executable_bpel(self):
        plan = TaskDecomposer().decompose("coordinate recon strike evaluation and assault")

        self.assertEqual(plan.evaluation_threshold, 60)
        self.assertIn("ReconReport", plan.variables)
        self.assertIn("CommanderDecision", plan.variables)
        self.assertIn("AutoEvaluationDecision", plan.bpel)
        self.assertIn('requiredSkill="scan_beach_defenses"', plan.bpel)
        self.assertIn('dispatchMode="parallel"', plan.bpel)

        with tempfile.NamedTemporaryFile("w", suffix=".bpel", delete=False, encoding="utf-8") as fh:
            fh.write(plan.bpel)
            path = fh.name
        definition = BPELWorkflowDefinition.load(path)
        invokes = [activity for activity in definition.activatities if activity.type == "invoke"]
        self.assertGreaterEqual(len(invokes), 4)
        self.assertTrue(any(activity.required_skill == "analyze_and_replanning" for activity in invokes))

    def test_required_skills_control_generated_steps(self):
        plan = TaskDecomposer().decompose(
            "quick direct action",
            required_skills=["scan_beach_defenses", "capture_beachhead"],
        )

        skills = [activity.required_skill for activity in plan.activities]
        self.assertEqual(skills, ["scan_beach_defenses", "capture_beachhead"])
        self.assertNotIn("AutoEvaluationDecision", plan.bpel)
        self.assertIn('requiredSkill="capture_beachhead"', plan.bpel)

    def test_chinese_goal_keywords_are_supported(self):
        decomposer = TaskDecomposer()

        quick = decomposer.decompose("\u5feb\u901f\u7a81\u51fb")
        self.assertEqual(
            [activity.required_skill for activity in quick.activities],
            ["scan_beach_defenses", "suppress_beach_sector_A", "capture_beachhead"],
        )

        reinforced = decomposer.decompose("\u5f3a\u5316\u534f\u540c\u4efb\u52a1")
        self.assertEqual(reinforced.evaluation_threshold, 80)

        recon_only = decomposer.decompose("\u4fa6\u5bdf\u63a2\u6d4b")
        self.assertEqual(
            [activity.required_skill for activity in recon_only.activities],
            ["scan_beach_defenses", "evaluate_strike"],
        )

    def test_integrated_goal_generates_new_agent_workflow(self):
        plan = TaskDecomposer().decompose("集成智能化任务：情报认知、跟踪、威胁评估、决策规划、合规授权和闭环评估")

        self.assertEqual(
            [activity.partner_link for activity in plan.activities],
            [
                "TacticalIntelligenceAgent",
                "TrackThreatAgent",
                "TrackThreatAgent",
                "DecisionPlanningAgent",
                "ComplianceAuthorizationAgent",
                "SimulationExecutionAgent",
                "ClosedLoopAgent",
            ],
        )
        self.assertIn("MissionInput", plan.variables)
        self.assertIn("EffectEvaluationResult", plan.variables)
        self.assertNotIn("AutoEvaluationDecision", plan.bpel)

        with tempfile.NamedTemporaryFile("w", suffix=".bpel", delete=False, encoding="utf-8") as fh:
            fh.write(plan.bpel)
            path = fh.name
        definition = BPELWorkflowDefinition.load(path)
        invokes = [activity for activity in definition.activatities if activity.type == "invoke"]
        self.assertEqual(
            [activity.role for activity in invokes],
            [
                "tactical_intelligence",
                "track_threat",
                "track_threat",
                "decision_planning",
                "compliance_authorization",
                "simulation_execution",
                "closed_loop",
            ],
        )
        self.assertEqual(invokes[0].required_skill, "semantic_intelligence")
        self.assertEqual(invokes[-1].required_skill, "closed_loop_optimization")

    def test_required_new_agent_skills_can_generate_partial_workflow(self):
        plan = TaskDecomposer().decompose(
            "决策与合规",
            required_skills=["generate_decision_plan", "check_compliance_authorization"],
        )

        self.assertEqual(
            [activity.partner_link for activity in plan.activities],
            ["DecisionPlanningAgent", "ComplianceAuthorizationAgent"],
        )
        self.assertIn('requiredSkill="decision_planning"', plan.bpel)
        self.assertIn('requiredSkill="compliance_authorization"', plan.bpel)

    def test_write_bpel_persists_generated_workflow(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = TaskDecomposer().write_bpel(
                "reinforced coordinated workflow",
                output_dir=temp_dir,
                workflow_id="wf-generated",
            )
            self.assertTrue(path.exists())
            self.assertEqual(path.name, "wf-generated.bpel")
            self.assertIn("AutoRootSequence", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
