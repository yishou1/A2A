"""Local model registry aligned with the shared repository interface."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable

try:
    from model_registry import (  # type: ignore
        AlgorithmModel as SharedAlgorithmModel,
        ModelRegistry as SharedModelRegistry,
    )
except ImportError:  # Standalone checkout without the shared repository root.
    SharedAlgorithmModel = None
    SharedModelRegistry = None


READY = "ready"
UNAVAILABLE = "unavailable"


@dataclass
class AgentModel:
    id: str
    name: str
    version: str = "1.0.0"
    model_type: str = "algorithm"
    status: str = READY
    description: str = ""
    tags: list[str] = field(default_factory=list)

    def snapshot(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "model_type": self.model_type,
            "status": self.status,
            "description": self.description,
            "tags": list(self.tags),
        }


class AgentModelRegistry:
    """Agent-owned registry with the same metadata contract as main.ModelRegistry."""

    def __init__(self, models: Iterable[AgentModel] = ()) -> None:
        self._models = {model.id: model for model in models}

    def register(self, model: AgentModel) -> AgentModel:
        self._models[model.id] = model
        return model

    def model_ids(self) -> list[str]:
        return list(self._models)

    def ready_model_ids(self) -> list[str]:
        return [
            model.id
            for model in self._models.values()
            if model.status == READY
        ]

    def deployment_status(self) -> str:
        statuses = {model.status for model in self._models.values()}
        if not statuses:
            return "none"
        if statuses == {READY}:
            return READY
        if READY in statuses:
            return "partial"
        return UNAVAILABLE

    def metadata(self) -> Dict[str, str]:
        return {
            "models": ",".join(self.model_ids()),
            "models_ready": ",".join(self.ready_model_ids()),
            "models_count": str(len(self._models)),
            "algorithm_deployment_status": self.deployment_status(),
        }

    def snapshot(self) -> Dict[str, Any]:
        return {
            "models": [model.snapshot() for model in self._models.values()],
            "deployment_status": self.deployment_status(),
            "count": len(self._models),
        }


def build_agent_model_registry(
    trained_st_gnn_runtime: Any | None,
) -> Any:
    model_class = SharedAlgorithmModel or AgentModel
    registry_class = SharedModelRegistry or AgentModelRegistry
    models = [
        model_class(
            "track_state_kalman_cv",
            "Covariance Kalman CV Tracker",
            model_type="state_estimator",
            tags=["tracking", "kalman", "local"],
        ),
        model_class(
            "trajectory_adaptive_multi_model_physics",
            "Adaptive CV/CA/CT Physics Predictor",
            model_type="physics_predictor",
            tags=["prediction", "physics", "fallback", "local"],
        ),
        model_class(
            "dbn_risk_state_calibration",
            "DBN Situation-Attention Calibration",
            version="dbn-risk-attention-v1",
            model_type="probabilistic_model",
            description="Versioned observable-factor DBN parameters with SHA256 traceability",
            tags=["risk", "dbn", "versioned", "local"],
        ),
        model_class(
            "physical_relation_complete_link_clustering",
            "Physical Relation Complete-Link Group Detector",
            version="2.0.0",
            model_type="graph_algorithm",
            description="Complete-link physical grouping with tentative/confirmed/coasting lifecycle",
            tags=["group", "relation", "lifecycle", "local"],
        ),
        model_class(
            "protected_asset_impact",
            "Protected Asset Impact Analyzer",
            model_type="risk_model",
            tags=["asset", "impact", "local"],
        ),
        model_class(
            "xai_evidence_chain",
            "XAI Evidence Chain",
            model_type="explanation_model",
            tags=["xai", "evidence", "local"],
        ),
    ]

    runtime_status = (
        trained_st_gnn_runtime.status()
        if trained_st_gnn_runtime is not None
        else {"models": {}}
    )
    for object_type in ("aircraft", "ship"):
        status = runtime_status.get("models", {}).get(object_type, {})
        model_id = str(
            status.get("model_version")
            or f"st_gnn_{object_type}_not_configured"
        )
        models.append(
            model_class(
                model_id,
                f"{object_type.title()} ST-GNN TorchScript",
                version=model_id,
                model_type="torchscript_st_gnn",
                status=READY if status.get("loaded") else UNAVAILABLE,
                description=(
                    f"release_status={status.get('release_status')}; "
                    f"release_gate_passed={status.get('release_gate_passed')}"
                ),
                tags=["prediction", "st-gnn", object_type, "local", "cpu"],
            )
        )
    return registry_class(models)


def configured_model_metadata() -> Dict[str, str]:
    """Static startup metadata used before runtime objects are constructed."""
    registry = build_agent_model_registry(None)
    return registry.metadata()
