from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


SUBMIT_SCHEMA_VERSION = "amos.commander.gateway.submit.v1"
PACKAGE_SCHEMA_VERSION = "amos.commander.package.v1"
PROJECTION_SCHEMA_VERSION = "amos.commander.projection.v1"


class WorkflowSubmitV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["amos.commander.gateway.submit.v1"]
    run_id: str = Field(min_length=1)
    chain_id: str = Field(min_length=1)
    workflow: Literal["bpel", "dynamic"] = "bpel"
    workflow_file: str | None = None
    max_steps: int = Field(default=10, ge=1)
    max_workers: int | None = Field(default=None, ge=1)
    max_activity_workers: int | None = Field(default=None, ge=1)
    max_agent_workers: int | None = Field(default=None, ge=1)
    max_retries: int = Field(default=1, ge=0)
    retry_backoff: float = Field(default=0.2, ge=0)
    request_timeout: float = Field(default=5.0, gt=0)
    mock_eval_score: int | None = None
    mock_decision: Literal["ASSAULT", "RE-PLAN"] | None = None

    def commander_parameters(self) -> dict[str, Any]:
        payload = self.model_dump(
            exclude={"schema_version", "run_id", "chain_id"},
            exclude_none=True,
        )
        return payload


class CommanderProjectionV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["amos.commander.projection.v1"] = PROJECTION_SCHEMA_VERSION
    workflow_id: str
    run_id: str
    chain_id: str
    package_id: str
    package_checksum: str
    event_cursor: int
    status: str
    work_list: list[Any] = Field(default_factory=list)
    trace: list[Any] = Field(default_factory=list)
    submitted_at: str
    updated_at: str
    last_error: Any | None = None


class SimulationProvenanceV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    mode: Literal["simulation"]
    generator: Literal["amos_simulation"]
    simulated: Literal[True]


class MediaRefV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    media_id: str = Field(min_length=1)
    uri: str = Field(min_length=1)
    mime_type: str = Field(min_length=1)
    checksum: str = Field(min_length=1)
    source_name: str = Field(min_length=1)
    provenance: SimulationProvenanceV1


class AmosEventV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["amos.simulation.event.v1"]
    event_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    sim_time_ms: int = Field(ge=0)
    occurred_at: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    phase: str
    data: dict[str, Any]
    media_refs: list[MediaRefV1]
    source: Literal["amos_simulation"]
    provenance: SimulationProvenanceV1


class AmosSnapshotV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["amos.simulation.snapshot.v1"]
    run_id: str = Field(min_length=1)
    scenario_id: str = Field(min_length=1)
    sequence: int = Field(ge=0)
    sim_time_ms: int = Field(ge=0)
    status: str = Field(min_length=1)
    assets: list[dict[str, Any]]
    observations: list[dict[str, Any]]
    tracks: list[dict[str, Any]]
    alerts: list[dict[str, Any]]
    network: dict[str, Any]
    simulation_chains: list[dict[str, Any]]
    recent_events: list[dict[str, Any]]
    provenance: SimulationProvenanceV1


class StoredWorkflowRecordV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    digest: str = Field(min_length=64, max_length=64)
    request_key: str = Field(min_length=64, max_length=64)
    projection: CommanderProjectionV1
    request: WorkflowSubmitV1
    commander_payload: dict[str, Any]


class IdempotencyRecordV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_id: str = Field(min_length=1)
    request_key: str = Field(min_length=64, max_length=64)
