from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional
from xml.sax.saxutils import escape

from workflow_state_store import new_workflow_id


@dataclass
class PlannedActivity:
    name: str
    partner_link: str
    operation: str
    required_skill: str
    input_variable: str
    output_variable: str
    dispatch_mode: str = "single"
    depends_on: list[str] = field(default_factory=list)

    def snapshot(self) -> dict:
        return {
            "name": self.name,
            "partner_link": self.partner_link,
            "operation": self.operation,
            "required_skill": self.required_skill,
            "input_variable": self.input_variable,
            "output_variable": self.output_variable,
            "dispatch_mode": self.dispatch_mode,
            "depends_on": list(self.depends_on),
        }


@dataclass
class TaskPlan:
    workflow_name: str
    objective: str
    variables: list[str]
    activities: list[PlannedActivity]
    evaluation_threshold: int = 60
    include_evaluation: bool = True
    include_replanning: bool = True
    strike_coordinates: str = "120.5E, 35.1N"
    bpel: str = ""

    def snapshot(self) -> dict:
        return {
            "workflow_name": self.workflow_name,
            "objective": self.objective,
            "variables": list(self.variables),
            "activities": [activity.snapshot() for activity in self.activities],
            "evaluation_threshold": self.evaluation_threshold,
            "include_evaluation": self.include_evaluation,
            "include_replanning": self.include_replanning,
            "strike_coordinates": self.strike_coordinates,
            "bpel": self.bpel,
        }


class TaskDecomposer:
    """Rule-based task decomposer that emits executable BPEL.

    This provides a transparent planning contract for high-level task goals.
    The rules can later be replaced by an LLM planner without changing the
    TaskPlan/BPEL interface consumed by Commander.
    """

    SKILL_TEMPLATES = {
        "scan_beach_defenses": {
            "name": "AutoRecon",
            "partner_link": "ReconAgent",
            "operation": "scanBeachDefenses",
            "input_variable": "Sector_A",
            "output_variable": "ReconReport",
        },
        "detect": {
            "name": "AutoDetect",
            "partner_link": "ReconAgent",
            "operation": "scanBeachDefenses",
            "input_variable": "Sector_A",
            "output_variable": "ReconReport",
        },
        "suppress_beach_sector_A": {
            "name": "AutoStrike",
            "partner_link": "ArtilleryAgent",
            "operation": "suppressBeachSector",
            "input_variable": "StrikeCoordinates",
            "output_variable": "StrikeResult",
            "dispatch_mode": "parallel",
        },
        "target_assignment": {
            "name": "AutoTargetAssignment",
            "partner_link": "ArtilleryAgent",
            "operation": "suppressBeachSector",
            "input_variable": "StrikeCoordinates",
            "output_variable": "StrikeResult",
            "dispatch_mode": "parallel",
        },
        "evaluate_strike": {
            "name": "AutoEvaluate",
            "partner_link": "EvaluatorAgent",
            "operation": "evaluateStrike",
            "input_variable": "StrikeCoordinates",
            "output_variable": "EvalScore",
        },
        "strike_effect_evaluation": {
            "name": "AutoEffectEvaluation",
            "partner_link": "EvaluatorAgent",
            "operation": "evaluateStrike",
            "input_variable": "StrikeCoordinates",
            "output_variable": "EvalScore",
        },
        "capture_beachhead": {
            "name": "AutoAssault",
            "partner_link": "AssaultAgent",
            "operation": "captureBeachhead",
            "input_variable": "StrikeCoordinates",
            "output_variable": "AssaultResult",
        },
        "route_planning": {
            "name": "AutoRouteAssault",
            "partner_link": "AssaultAgent",
            "operation": "captureBeachhead",
            "input_variable": "StrikeCoordinates",
            "output_variable": "AssaultResult",
        },
        "build_situation_summary": {
            "name": "AutoCognition",
            "partner_link": "TacticalIntelligenceAgent",
            "operation": "buildSituationSummary",
            "required_skill": "semantic_intelligence",
            "input_variable": "MissionInput",
            "output_variable": "CognitionResult",
        },
        "semantic_intelligence": {
            "name": "AutoCognition",
            "partner_link": "TacticalIntelligenceAgent",
            "operation": "buildSituationSummary",
            "required_skill": "semantic_intelligence",
            "input_variable": "MissionInput",
            "output_variable": "CognitionResult",
        },
        "update_tracks": {
            "name": "AutoTracking",
            "partner_link": "TrackThreatAgent",
            "operation": "updateTracks",
            "required_skill": "trajectory_tracking",
            "input_variable": "CognitionResult",
            "output_variable": "TrackingResult",
        },
        "trajectory_tracking": {
            "name": "AutoTracking",
            "partner_link": "TrackThreatAgent",
            "operation": "updateTracks",
            "required_skill": "trajectory_tracking",
            "input_variable": "CognitionResult",
            "output_variable": "TrackingResult",
        },
        "rank_threats": {
            "name": "AutoThreatAssessment",
            "partner_link": "TrackThreatAgent",
            "operation": "rankThreats",
            "required_skill": "threat_ranking",
            "input_variable": "TrackingResult",
            "output_variable": "ThreatAssessmentResult",
        },
        "threat_ranking": {
            "name": "AutoThreatAssessment",
            "partner_link": "TrackThreatAgent",
            "operation": "rankThreats",
            "required_skill": "threat_ranking",
            "input_variable": "TrackingResult",
            "output_variable": "ThreatAssessmentResult",
        },
        "generate_decision_plan": {
            "name": "AutoDecisionPlanning",
            "partner_link": "DecisionPlanningAgent",
            "operation": "generateDecisionPlan",
            "required_skill": "decision_planning",
            "input_variable": "ThreatAssessmentResult",
            "output_variable": "DecisionPlanningResult",
        },
        "decision_planning": {
            "name": "AutoDecisionPlanning",
            "partner_link": "DecisionPlanningAgent",
            "operation": "generateDecisionPlan",
            "required_skill": "decision_planning",
            "input_variable": "ThreatAssessmentResult",
            "output_variable": "DecisionPlanningResult",
        },
        "check_compliance_authorization": {
            "name": "AutoComplianceAuthorization",
            "partner_link": "ComplianceAuthorizationAgent",
            "operation": "checkComplianceAuthorization",
            "required_skill": "compliance_authorization",
            "input_variable": "DecisionPlanningResult",
            "output_variable": "ComplianceAuthorizationResult",
        },
        "compliance_authorization": {
            "name": "AutoComplianceAuthorization",
            "partner_link": "ComplianceAuthorizationAgent",
            "operation": "checkComplianceAuthorization",
            "required_skill": "compliance_authorization",
            "input_variable": "DecisionPlanningResult",
            "output_variable": "ComplianceAuthorizationResult",
        },
        "simulate_execution_control": {
            "name": "AutoExecutionSimulation",
            "partner_link": "SimulationExecutionAgent",
            "operation": "simulateExecutionControl",
            "required_skill": "execution_control",
            "input_variable": "ComplianceAuthorizationResult",
            "output_variable": "ExecutionSimulationResult",
        },
        "execution_control": {
            "name": "AutoExecutionSimulation",
            "partner_link": "SimulationExecutionAgent",
            "operation": "simulateExecutionControl",
            "required_skill": "execution_control",
            "input_variable": "ComplianceAuthorizationResult",
            "output_variable": "ExecutionSimulationResult",
        },
        "evaluate_mission_effect": {
            "name": "AutoEffectEvaluation",
            "partner_link": "ClosedLoopAgent",
            "operation": "evaluateMissionEffect",
            "required_skill": "closed_loop_optimization",
            "input_variable": "ExecutionSimulationResult",
            "output_variable": "EffectEvaluationResult",
        },
        "closed_loop_optimization": {
            "name": "AutoEffectEvaluation",
            "partner_link": "ClosedLoopAgent",
            "operation": "evaluateMissionEffect",
            "required_skill": "closed_loop_optimization",
            "input_variable": "ExecutionSimulationResult",
            "output_variable": "EffectEvaluationResult",
        },
    }

    DEFAULT_SKILLS = [
        "scan_beach_defenses",
        "suppress_beach_sector_A",
        "evaluate_strike",
        "capture_beachhead",
    ]

    INTEGRATED_SKILLS = [
        "semantic_intelligence",
        "trajectory_tracking",
        "threat_ranking",
        "decision_planning",
        "compliance_authorization",
        "execution_control",
        "closed_loop_optimization",
    ]

    QUICK_TOKENS = ("quick", "\u5feb\u901f", "\u76f4\u63a5")
    RECON_TOKENS = ("recon", "detect", "\u4fa6\u5bdf", "\u63a2\u6d4b")
    ACTION_TOKENS = ("strike", "assault", "\u6253\u51fb", "\u7a81\u51fb")
    REINFORCED_TOKENS = ("reinforced", "\u5f3a\u5316", "\u9ad8\u53ef\u9760")
    PARALLEL_TOKENS = ("parallel", "\u5e76\u53d1", "\u534f\u540c", "\u5f3a\u5316")
    INTEGRATED_TOKENS = (
        "integrated",
        "intelligence",
        "cognition",
        "tracking",
        "threat",
        "decision",
        "planning",
        "compliance",
        "authorization",
        "closed_loop",
        "\u96c6\u6210",
        "\u667a\u80fd\u5316",
        "\u60c5\u62a5",
        "\u8ba4\u77e5",
        "\u8ddf\u8e2a",
        "\u822a\u8ff9",
        "\u5a01\u80c1",
        "\u51b3\u7b56",
        "\u89c4\u5212",
        "\u5408\u89c4",
        "\u6388\u6743",
        "\u95ed\u73af",
    )

    def decompose(
        self,
        objective: str,
        *,
        required_skills: Optional[Iterable[str]] = None,
        workflow_name: Optional[str] = None,
        evaluation_threshold: Optional[int] = None,
        strike_coordinates: str = "120.5E, 35.1N",
    ) -> TaskPlan:
        objective = (objective or "").strip()
        if not objective and not required_skills:
            raise ValueError("task decomposition requires objective or required_skills")

        skills = self._normalize_skills(required_skills) or self._infer_skills(objective)
        include_evaluation = any(
            skill in {"evaluate_strike", "strike_effect_evaluation"}
            for skill in skills
        )
        activities = self._activities_from_skills(skills, objective)
        threshold = (
            evaluation_threshold
            if evaluation_threshold is not None
            else self._infer_threshold(objective)
        )
        plan = TaskPlan(
            workflow_name=workflow_name or self._workflow_name(objective),
            objective=objective,
            variables=self._variables_for_activities(activities),
            activities=activities,
            evaluation_threshold=threshold,
            include_evaluation=include_evaluation,
            include_replanning=include_evaluation,
            strike_coordinates=strike_coordinates,
        )
        plan.bpel = self.to_bpel(plan)
        return plan

    def write_bpel(
        self,
        objective: str,
        *,
        output_dir: str | Path,
        workflow_id: Optional[str] = None,
        required_skills: Optional[Iterable[str]] = None,
        workflow_name: Optional[str] = None,
        evaluation_threshold: Optional[int] = None,
        strike_coordinates: str = "120.5E, 35.1N",
    ) -> Path:
        plan = self.decompose(
            objective,
            required_skills=required_skills,
            workflow_name=workflow_name,
            evaluation_threshold=evaluation_threshold,
            strike_coordinates=strike_coordinates,
        )
        output = Path(output_dir).expanduser().resolve()
        output.mkdir(parents=True, exist_ok=True)
        path = output / f"{workflow_id or new_workflow_id('auto-workflow')}.bpel"
        path.write_text(plan.bpel, encoding="utf-8")
        return path

    def to_bpel(self, plan: TaskPlan) -> str:
        variables_xml = "\n".join(
            f'        <variable name="{escape(name)}" type="{self._variable_type(name)}"/>'
            for name in plan.variables
        )
        return (
            f'<process name="{escape(plan.workflow_name)}" '
            'targetNamespace="http://a2a.generated/workflow">\n'
            "    <variables>\n"
            f"{variables_xml}\n"
            "    </variables>\n\n"
            f"{self._body_xml(plan)}\n"
            "</process>\n"
        )

    def _body_xml(self, plan: TaskPlan) -> str:
        pre_eval = [
            activity
            for activity in plan.activities
            if activity.output_variable not in {"EvalScore", "AssaultResult"}
        ]
        evaluator = next(
            (activity for activity in plan.activities if activity.output_variable == "EvalScore"),
            None,
        )
        assault = next(
            (activity for activity in plan.activities if activity.output_variable == "AssaultResult"),
            None,
        )

        lines = ['    <sequence name="AutoRootSequence">']
        coordinates_assigned = False
        for activity in pre_eval:
            coordinates_assigned = self._maybe_assign_coordinates(
                lines,
                plan,
                activity,
                coordinates_assigned,
            )
            lines.append(self._invoke_xml(activity, indent=8))

        if evaluator is not None:
            coordinates_assigned = self._maybe_assign_coordinates(
                lines,
                plan,
                evaluator,
                coordinates_assigned,
            )
            lines.append(self._invoke_xml(evaluator, indent=8))

        if evaluator is not None and plan.include_replanning:
            lines.extend(self._switch_xml(plan, assault))
        elif assault is not None:
            coordinates_assigned = self._maybe_assign_coordinates(
                lines,
                plan,
                assault,
                coordinates_assigned,
            )
            lines.append(self._invoke_xml(assault, indent=8))

        lines.append("    </sequence>")
        return "\n".join(lines)

    def _switch_xml(self, plan: TaskPlan, assault: Optional[PlannedActivity]) -> list[str]:
        lines = [
            '        <switch name="AutoEvaluationDecision">',
            f"            <case condition=\"bpws:getVariableData('EvalScore') &lt; {int(plan.evaluation_threshold)}\">",
            '                <sequence name="AutoReplanSequence">',
            '                    <invoke name="AutoReplan" partnerLink="LLMCommanderAgent" operation="analyzeAndReplanning"',
            '                            requiredSkill="analyze_and_replanning"',
            '                            inputVariables="ReconReport,StrikeResult" outputVariable="CommanderDecision"/>',
            '                    <throw faultName="AutoReplanningRequired"/>',
            "                </sequence>",
            "            </case>",
            '            <otherwise name="AutoContinue">',
        ]
        if assault is not None:
            lines.append(self._invoke_xml(assault, indent=16))
        lines.extend(["            </otherwise>", "        </switch>"])
        return lines

    def _invoke_xml(self, activity: PlannedActivity, *, indent: int) -> str:
        prefix = " " * indent
        attrs = [
            f'name="{escape(activity.name)}"',
            f'partnerLink="{escape(activity.partner_link)}"',
            f'operation="{escape(activity.operation)}"',
            f'requiredSkill="{escape(activity.required_skill)}"',
            f'inputVariable="{escape(activity.input_variable)}"',
            f'outputVariable="{escape(activity.output_variable)}"',
        ]
        if activity.dispatch_mode != "single":
            attrs.insert(4, f'dispatchMode="{escape(activity.dispatch_mode)}"')
        if activity.depends_on:
            attrs.append(f'dependsOn="{escape(",".join(activity.depends_on))}"')
        return f"{prefix}<invoke {' '.join(attrs)}/>"

    def _maybe_assign_coordinates(
        self,
        lines: list[str],
        plan: TaskPlan,
        activity: PlannedActivity,
        already_assigned: bool,
    ) -> bool:
        if (
            not already_assigned
            and activity.input_variable == "StrikeCoordinates"
            and "StrikeCoordinates" in plan.variables
        ):
            lines.extend(self._assign_coordinates_xml(plan.strike_coordinates))
            return True
        return already_assigned

    @staticmethod
    def _assign_coordinates_xml(coordinates: str) -> list[str]:
        return [
            '        <assign name="AutoAssignStrikeCoordinates">',
            "            <copy>",
            f"                <from>{escape(coordinates)}</from>",
            '                <to variable="StrikeCoordinates"/>',
            "            </copy>",
            "        </assign>",
        ]

    def _activities_from_skills(self, skills: list[str], objective: str) -> list[PlannedActivity]:
        activities = []
        added_outputs = set()
        for skill in skills:
            template = self.SKILL_TEMPLATES.get(skill)
            if not template:
                continue
            output = template["output_variable"]
            if output in added_outputs:
                continue
            added_outputs.add(output)
            dispatch_mode = template.get("dispatch_mode", "single")
            if self._wants_parallel(objective, skill):
                dispatch_mode = "parallel"
            activities.append(
                PlannedActivity(
                    name=template["name"],
                    partner_link=template["partner_link"],
                    operation=template["operation"],
                    required_skill=template.get("required_skill", skill),
                    input_variable=template["input_variable"],
                    output_variable=output,
                    dispatch_mode=dispatch_mode,
                )
            )
        return activities

    def _infer_skills(self, objective: str) -> list[str]:
        text = objective.lower()
        if any(token in text for token in self.INTEGRATED_TOKENS):
            return list(self.INTEGRATED_SKILLS)
        if any(token in text for token in self.QUICK_TOKENS):
            return [
                "scan_beach_defenses",
                "suppress_beach_sector_A",
                "capture_beachhead",
            ]
        if any(token in text for token in self.RECON_TOKENS) and not any(
            token in text for token in self.ACTION_TOKENS
        ):
            return ["scan_beach_defenses", "evaluate_strike"]
        return list(self.DEFAULT_SKILLS)

    def _infer_threshold(self, objective: str) -> int:
        text = objective.lower()
        if any(token in text for token in self.REINFORCED_TOKENS):
            return 80
        return 60

    def _wants_parallel(self, objective: str, skill: str) -> bool:
        text = objective.lower()
        if skill in {"suppress_beach_sector_A", "target_assignment"}:
            return True
        return any(token in text for token in self.PARALLEL_TOKENS)

    def _normalize_skills(self, required_skills: Optional[Iterable[str]]) -> list[str]:
        if not required_skills:
            return []
        if isinstance(required_skills, str):
            raw = re.split(r"[,;\s]+", required_skills)
        else:
            raw = list(required_skills)
        result = []
        for skill in raw:
            normalized = str(skill or "").strip()
            if normalized and normalized not in result:
                result.append(normalized)
        return result

    @staticmethod
    def _workflow_name(objective: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9]+", "_", objective.strip()).strip("_")
        if slug:
            return f"AutoWorkflow_{slug[:40]}"
        return "AutoWorkflow"

    @staticmethod
    def _variables_for_activities(activities: list[PlannedActivity]) -> list[str]:
        preferred_order = [
            "MissionInput",
            "CognitionResult",
            "TrackingResult",
            "ThreatAssessmentResult",
            "DecisionPlanningResult",
            "ComplianceAuthorizationResult",
            "ExecutionSimulationResult",
            "EffectEvaluationResult",
            "ReconReport",
            "StrikeCoordinates",
            "StrikeResult",
            "EvalScore",
            "CommanderDecision",
            "AssaultResult",
        ]
        used = {
            activity.input_variable
            for activity in activities
            if activity.input_variable != "Sector_A"
        } | {activity.output_variable for activity in activities}
        if "EvalScore" in used:
            used.update({"CommanderDecision", "ReconReport", "StrikeResult"})
        ordered = [variable for variable in preferred_order if variable in used]
        for variable in sorted(used):
            if variable not in ordered:
                ordered.append(variable)
        return ordered or ["ReconReport"]

    @staticmethod
    def _variable_type(name: str) -> str:
        return "Integer" if name == "EvalScore" else "String"
