from __future__ import annotations

from copy import deepcopy
from typing import Any


PROTOCOL_VERSION = "1.0"
SUPPORTED_PROTOCOL_VERSIONS = {PROTOCOL_VERSION}
TASK_REQUIRED_FIELDS = {
    "schema_version",
    "workflow_id",
    "work_item",
    "command",
    "required_skill",
    "input",
    "output_hint",
}


class ContractValidationError(ValueError):
    def __init__(self, message: str, *, code: str = "SCHEMA_VALIDATION_ERROR"):
        super().__init__(message)
        self.code = code


def validate_protocol_version(payload: dict) -> str:
    version = str(payload.get("schema_version") or PROTOCOL_VERSION)
    if version not in SUPPORTED_PROTOCOL_VERSIONS:
        raise ContractValidationError(
            f"Unsupported schema_version={version}; supported={sorted(SUPPORTED_PROTOCOL_VERSIONS)}",
            code="UNSUPPORTED_SCHEMA_VERSION",
        )
    return version


def validate_value(value: Any, schema: dict | None, path: str = "value") -> None:
    if not schema:
        return
    expected = schema.get("type")
    expected_types = [expected] if isinstance(expected, str) else list(expected or [])
    if expected_types and not any(_matches_type(value, item) for item in expected_types):
        raise ContractValidationError(
            f"{path} must be {expected_types}, got {type(value).__name__}"
        )

    if value is None:
        return
    if isinstance(value, dict):
        required = schema.get("required", [])
        missing = [key for key in required if key not in value]
        if missing:
            raise ContractValidationError(f"{path} is missing required fields: {missing}")
        properties = schema.get("properties", {})
        for key, child_schema in properties.items():
            if key in value:
                validate_value(value[key], child_schema, f"{path}.{key}")
        if schema.get("additionalProperties") is False:
            unknown = sorted(set(value) - set(properties))
            if unknown:
                raise ContractValidationError(f"{path} has unknown fields: {unknown}")
    elif isinstance(value, list) and schema.get("items"):
        for index, item in enumerate(value):
            validate_value(item, schema["items"], f"{path}[{index}]")


def validate_task_payload(payload: dict, skill: dict | None = None) -> dict:
    version = validate_protocol_version(payload)
    missing = sorted(
        field
        for field in TASK_REQUIRED_FIELDS
        if payload.get(field) in (None, "")
    )
    if missing:
        raise ContractValidationError(f"task is missing required fields: {missing}")
    if not isinstance(payload.get("input", {}), dict):
        raise ContractValidationError("task.input must be an object")
    if skill:
        validate_value(payload.get("input", {}), skill.get("input_schema"), "task.input")
    normalized = deepcopy(payload)
    normalized["schema_version"] = version
    return normalized


def validate_task_response(
    task_payload: dict,
    response: dict,
    skill: dict | None = None,
) -> dict:
    validate_protocol_version(response)
    required_response_fields = {
        "schema_version", "workflow_id", "work_item", "agent", "role", "status",
        "output", "metrics",
    }
    missing = sorted(field for field in required_response_fields if field not in response)
    if missing:
        raise ContractValidationError(f"response is missing required fields: {missing}")
    if response.get("workflow_id") != task_payload.get("workflow_id"):
        raise ContractValidationError("response.workflow_id does not match task.workflow_id")
    if response.get("work_item") != task_payload.get("work_item"):
        raise ContractValidationError("response.work_item does not match task.work_item")
    if not isinstance(response.get("metrics"), dict):
        raise ContractValidationError("response.metrics must be an object")
    output = response.get("output")
    if not isinstance(output, dict):
        raise ContractValidationError("response.output must be an object")
    output_hint = task_payload.get("output_hint")
    status = str(response.get("status") or "").lower()
    if status not in {
        "completed", "succeeded", "success", "accepted",
        "failed", "error", "rejected", "timeout",
    }:
        raise ContractValidationError(f"response.status is invalid: {status}")
    if status in {"completed", "succeeded", "success"} and output_hint:
        if output_hint not in output:
            raise ContractValidationError(
                f"response.output must contain output_hint key '{output_hint}'",
                code="OUTPUT_CONTRACT_ERROR",
            )
        if skill:
            validate_value(
                output[output_hint],
                skill.get("output_schema"),
                f"response.output.{output_hint}",
            )
    return response


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return True
