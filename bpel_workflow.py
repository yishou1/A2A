from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree


PARTNER_ROLE_MAP = {
    "ReconAgent": "recon",
    "ExecutionControlAgent": "execution_control",
    "ArtilleryAgent": "artillery",
    "EvaluatorAgent": "evaluator",
    "AssaultAgent": "assault",
    "ClosedLoopAgent": "closed_loop",
    "LLMCommanderAgent": "commander",
}

OPERATION_COMMAND_MAP = {
    "scanBeachDefenses": "scan_beach_defenses",
    "planStrikeControl": "plan_strike_control",
    "planAssaultControl": "plan_assault_control",
    "suppressBeachSector": "suppress_beach_sector_A",
    "evaluateStrike": "evaluate_strike",
    "captureBeachhead": "capture_beachhead",
    "closedLoopOptimization": "closed_loop_optimization",
    "analyzeAndReplanning": "analyze_and_replanning",
}


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return normalized or "activity"


def _split_list_attribute(value: str | None) -> list[str]:
    if not value:
        return []
    return [
        item.strip()
        for item in re.split(r"[,;\s]+", value)
        if item.strip()
    ]


@dataclass
class BPELActivatity:
    activatity_id: str
    type: str
    name: str
    parent_activatity: str | None = None
    role: str | None = None
    partner_link: str | None = None
    operation: str | None = None
    command: str | None = None
    required_skill: str | None = None
    required_skills: list[str] = field(default_factory=list)
    dispatch_mode: str = "single"
    input_variable: str | None = None
    input_variables: list[str] = field(default_factory=list)
    output_variable: str | None = None
    condition: str | None = None
    fault_name: str | None = None
    assign_from: str | None = None
    assign_to: str | None = None
    retry_count: int = 0
    timeout_seconds: float | None = None
    failure_policy: str = "pause"
    depends_on: list[str] = field(default_factory=list)
    children: list["BPELActivatity"] = field(default_factory=list)

    @property
    def activity_id(self) -> str:
        return self.activatity_id

    @property
    def parent_activity(self) -> str | None:
        return self.parent_activatity

    def to_work_list_item(self, workflow_id: str, index: int) -> dict:
        return {
            "activatity_id": self.activatity_id,
            "activatity_index": index,
            "activity_id": self.activatity_id,
            "activity_index": index,
            "work_item": f"{workflow_id}:{self.activatity_id}",
            "type": self.type,
            "name": self.name,
            "parent_activatity": self.parent_activatity,
            "parent_activity": self.parent_activatity,
            "role": self.role,
            "partner_link": self.partner_link,
            "operation": self.operation,
            "command": self.command,
            "required_skill": self.required_skill,
            "required_skills": list(self.required_skills),
            "dispatch_mode": self.dispatch_mode,
            "input_variable": self.input_variable,
            "input_variables": list(self.input_variables),
            "output_variable": self.output_variable,
            "retry_count": self.retry_count,
            "timeout_seconds": self.timeout_seconds,
            "failure_policy": self.failure_policy,
            "depends_on": list(self.depends_on),
            "status": "pending",
            "error": None,
        }


class BPELWorkflowDefinition:
    def __init__(
        self,
        source_path: Path,
        process_name: str,
        root_activatity: BPELActivatity,
        variables: set[str] | None = None,
    ):
        self.source_path = source_path
        self.process_name = process_name
        self.root_activatity = root_activatity
        self.variables = set(variables or [])
        self._activatities = list(self._walk(root_activatity))
        self.activatities_by_id = {
            activatity.activatity_id: activatity for activatity in self._activatities
        }
        self.validate()

    @property
    def activatities(self) -> list[BPELActivatity]:
        return list(self._activatities)

    @classmethod
    def load(cls, source_path: str | Path) -> "BPELWorkflowDefinition":
        path = Path(source_path).expanduser().resolve()
        process = ElementTree.parse(path).getroot()
        process_name = process.attrib.get("name", path.stem)
        variables = {
            child.attrib.get("name")
            for container in process
            if _local_name(container.tag) == "variables"
            for child in container
            if _local_name(child.tag) == "variable" and child.attrib.get("name")
        }

        body = next(
            (
                child
                for child in process
                if _local_name(child.tag) not in {"variables", "partnerLinks"}
            ),
            None,
        )
        if body is None:
            raise ValueError(f"BPEL workflow has no executable body: {path}")

        counter = iter(range(1, 10000))

        def optional_int(*values, default=0):
            for value in values:
                if value not in (None, ""):
                    return int(value)
            return default

        def optional_float(*values):
            for value in values:
                if value not in (None, ""):
                    return float(value)
            return None

        def parse_element(element, parent_id=None):
            kind = _local_name(element.tag)
            index = next(counter)
            raw_name = (
                element.attrib.get("name")
                or element.attrib.get("operation")
                or element.attrib.get("faultName")
                or kind
            )
            activatity_id = f"activatity-{index:03d}-{_slug(raw_name)}"
            partner_link = element.attrib.get("partnerLink")
            operation = element.attrib.get("operation")
            role = PARTNER_ROLE_MAP.get(partner_link)
            command = OPERATION_COMMAND_MAP.get(operation, operation)
            required_skills = _split_list_attribute(
                element.attrib.get("requiredSkills")
                or element.attrib.get("skills")
            )
            required_skill = (
                element.attrib.get("requiredSkill")
                or element.attrib.get("skill")
                or (required_skills[0] if required_skills else None)
                or command
            )
            if required_skill and required_skill not in required_skills:
                required_skills.insert(0, required_skill)
            input_variable = element.attrib.get("inputVariable")
            input_variables = _split_list_attribute(element.attrib.get("inputVariables"))
            if input_variable:
                if any(separator in input_variable for separator in ("+", ",", ";")):
                    raise ValueError(
                        f"BPEL invoke '{raw_name}' must use inputVariables for multiple inputs"
                    )
                if input_variable not in input_variables:
                    input_variables.insert(0, input_variable)
            assign_from = None
            assign_to = None

            if kind == "assign":
                copy_element = next(
                    (child for child in element if _local_name(child.tag) == "copy"),
                    None,
                )
                if copy_element is not None:
                    from_element = next(
                        (child for child in copy_element if _local_name(child.tag) == "from"),
                        None,
                    )
                    to_element = next(
                        (child for child in copy_element if _local_name(child.tag) == "to"),
                        None,
                    )
                    if from_element is not None:
                        assign_from = (from_element.text or "").strip()
                    if to_element is not None:
                        assign_to = to_element.attrib.get("variable") or (to_element.text or "").strip()

            activatity = BPELActivatity(
                activatity_id=activatity_id,
                type=kind,
                name=raw_name,
                parent_activatity=parent_id,
                role=role,
                partner_link=partner_link,
                operation=operation,
                command=command,
                required_skill=required_skill,
                required_skills=required_skills,
                dispatch_mode=element.attrib.get("dispatchMode", "single"),
                input_variable=input_variable,
                input_variables=input_variables,
                output_variable=element.attrib.get("outputVariable"),
                condition=element.attrib.get("condition"),
                fault_name=element.attrib.get("faultName"),
                assign_from=assign_from,
                assign_to=assign_to,
                retry_count=optional_int(
                    element.attrib.get("retryCount"),
                    element.attrib.get("maxRetries"),
                    default=0,
                ),
                timeout_seconds=optional_float(
                    element.attrib.get("timeoutSeconds"),
                    element.attrib.get("timeout"),
                ),
                failure_policy=element.attrib.get("failurePolicy", "pause"),
                depends_on=_split_list_attribute(element.attrib.get("dependsOn")),
            )
            activatity.children = [
                parse_element(child, activatity_id)
                for child in element
                if _local_name(child.tag) not in {"copy", "from", "to"}
            ]
            return activatity

        return cls(path, process_name, parse_element(body), variables=variables)

    def validate(self) -> None:
        names = {activity.name for activity in self._activatities}
        ids = set(self.activatities_by_id)
        errors = []
        name_counts = {
            name: sum(1 for activity in self._activatities if activity.name == name)
            for name in names
        }
        for activity in self._activatities:
            if activity.type == "invoke":
                if not activity.operation:
                    errors.append(f"invoke '{activity.name}' is missing operation")
                if not activity.required_skills:
                    errors.append(f"invoke '{activity.name}' is missing requiredSkill(s)")
                if not activity.output_variable:
                    errors.append(f"invoke '{activity.name}' is missing outputVariable")
                elif self.variables and activity.output_variable not in self.variables:
                    errors.append(
                        f"invoke '{activity.name}' writes undeclared variable "
                        f"'{activity.output_variable}'"
                    )
                if activity.dispatch_mode not in {"single", "parallel"}:
                    errors.append(
                        f"invoke '{activity.name}' has invalid dispatchMode={activity.dispatch_mode}"
                    )
                for variable in activity.input_variables:
                    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", variable):
                        errors.append(
                            f"invoke '{activity.name}' has invalid input variable '{variable}'"
                        )
            if activity.failure_policy not in {"pause", "skip", "fail"}:
                errors.append(
                    f"activity '{activity.name}' has invalid failurePolicy={activity.failure_policy}"
                )
            for dependency in activity.depends_on:
                if dependency not in names and dependency not in ids:
                    errors.append(
                        f"activity '{activity.name}' depends on unknown activity '{dependency}'"
                    )
                if dependency in name_counts and name_counts[dependency] > 1:
                    errors.append(
                        f"activity '{activity.name}' has ambiguous dependency '{dependency}'"
                    )
        dependency_graph = {
            activity.name: set(activity.depends_on)
            for activity in self._activatities
            if activity.depends_on
        }
        visiting = set()
        visited = set()

        def visit(name):
            if name in visiting:
                errors.append(f"dependency cycle detected at '{name}'")
                return
            if name in visited:
                return
            visiting.add(name)
            for dependency in dependency_graph.get(name, set()):
                if dependency in names:
                    visit(dependency)
            visiting.remove(name)
            visited.add(name)

        for name in dependency_graph:
            visit(name)
        if errors:
            raise ValueError("Invalid BPEL workflow: " + "; ".join(errors))

    @staticmethod
    def _walk(activatity: BPELActivatity) -> Iterable[BPELActivatity]:
        yield activatity
        for child in activatity.children:
            yield from BPELWorkflowDefinition._walk(child)

    def initial_work_list(self, workflow_id: str) -> list[dict]:
        return [
            activatity.to_work_list_item(workflow_id, index)
            for index, activatity in enumerate(self._activatities, start=1)
        ]

    def work_list_snapshot(self, workflow_id: str) -> list[dict]:
        return deepcopy(self.initial_work_list(workflow_id))


class BPELWorkflowCatalog:
    def __init__(self, project_root: str | Path):
        root = Path(project_root).resolve()
        self.search_dirs = [root / "workflows", root]

    def discover(self) -> list[Path]:
        discovered = []
        seen = set()
        for search_dir in self.search_dirs:
            if not search_dir.exists():
                continue
            for path in sorted(search_dir.glob("*.bpel")):
                resolved = path.resolve()
                if resolved not in seen:
                    discovered.append(resolved)
                    seen.add(resolved)
        return discovered

    def load(self, workflow_ref: str | None = None) -> BPELWorkflowDefinition:
        if workflow_ref:
            requested_path = Path(workflow_ref).expanduser()
            if requested_path.exists():
                return BPELWorkflowDefinition.load(requested_path)

        definitions = [BPELWorkflowDefinition.load(path) for path in self.discover()]
        if not definitions:
            raise FileNotFoundError("No .bpel workflows were found")

        if not workflow_ref:
            return definitions[0]

        for definition in definitions:
            if workflow_ref in {
                definition.process_name,
                definition.source_path.name,
                definition.source_path.stem,
            }:
                return definition

        available = ", ".join(definition.source_path.name for definition in definitions)
        raise FileNotFoundError(f"BPEL workflow not found: {workflow_ref}. Available: {available}")
