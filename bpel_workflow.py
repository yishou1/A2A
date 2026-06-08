from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree


PARTNER_ROLE_MAP = {
    "ReconAgent": "recon",
    "ArtilleryAgent": "artillery",
    "EvaluatorAgent": "evaluator",
    "AssaultAgent": "assault",
    "LLMCommanderAgent": "commander",
}

OPERATION_COMMAND_MAP = {
    "scanBeachDefenses": "scan_beach_defenses",
    "suppressBeachSector": "suppress_beach_sector_A",
    "evaluateStrike": "evaluate_strike",
    "captureBeachhead": "capture_beachhead",
    "analyzeAndReplanning": "analyze_and_replanning",
}


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return normalized or "activity"


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
    dispatch_mode: str = "single"
    input_variable: str | None = None
    output_variable: str | None = None
    condition: str | None = None
    fault_name: str | None = None
    assign_from: str | None = None
    assign_to: str | None = None
    retry_count: int = 0
    timeout_seconds: float | None = None
    failure_policy: str = "pause"
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
            "dispatch_mode": self.dispatch_mode,
            "retry_count": self.retry_count,
            "timeout_seconds": self.timeout_seconds,
            "failure_policy": self.failure_policy,
            "status": "pending",
            "error": None,
        }


class BPELWorkflowDefinition:
    def __init__(self, source_path: Path, process_name: str, root_activatity: BPELActivatity):
        self.source_path = source_path
        self.process_name = process_name
        self.root_activatity = root_activatity
        self._activatities = list(self._walk(root_activatity))
        self.activatities_by_id = {
            activatity.activatity_id: activatity for activatity in self._activatities
        }

    @classmethod
    def load(cls, source_path: str | Path) -> "BPELWorkflowDefinition":
        path = Path(source_path).expanduser().resolve()
        process = ElementTree.parse(path).getroot()
        process_name = process.attrib.get("name", path.stem)

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
                command=OPERATION_COMMAND_MAP.get(operation, operation),
                dispatch_mode=element.attrib.get("dispatchMode", "single"),
                input_variable=element.attrib.get("inputVariable"),
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
            )
            activatity.children = [
                parse_element(child, activatity_id)
                for child in element
                if _local_name(child.tag) not in {"copy", "from", "to"}
            ]
            return activatity

        return cls(path, process_name, parse_element(body))

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
