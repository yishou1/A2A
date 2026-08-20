from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Iterable, Optional, Union


# Deployment status values for a deployed algorithm model.
MODEL_STATUS_READY = "ready"
MODEL_STATUS_LOADING = "loading"
MODEL_STATUS_UNAVAILABLE = "unavailable"


@dataclass
class AlgorithmModel:
    """Describes an algorithm/model deployed inside an Agent runtime."""

    id: str
    name: str = ""
    version: str = "1.0.0"
    model_type: str = "generic"
    status: str = MODEL_STATUS_READY
    description: str = ""
    tags: list = field(default_factory=list)

    def snapshot(self) -> dict:
        return {
            "id": self.id,
            "name": self.name or self.id,
            "version": self.version,
            "model_type": self.model_type,
            "status": self.status,
            "description": self.description,
            "tags": list(self.tags or []),
        }


def build_model(
    model_id: str,
    *,
    name: str = "",
    version: str = "1.0.0",
    model_type: str = "generic",
    status: str = MODEL_STATUS_READY,
    description: str = "",
    tags: Optional[Iterable[str]] = None,
) -> AlgorithmModel:
    return AlgorithmModel(
        id=model_id,
        name=name,
        version=version,
        model_type=model_type,
        status=status,
        description=description,
        tags=list(tags or []),
    )


class ModelRegistry:
    """Tracks the algorithm models an Agent has deployed and can invoke."""

    def __init__(self, models: Optional[Iterable[Union[AlgorithmModel, dict]]] = None):
        self._models: dict[str, AlgorithmModel] = {}
        for model in models or []:
            self.register(model)

    def register(self, model: Union[AlgorithmModel, dict]) -> AlgorithmModel:
        if isinstance(model, dict):
            model = AlgorithmModel(
                id=model["id"],
                name=model.get("name", ""),
                version=model.get("version", "1.0.0"),
                model_type=model.get("model_type", model.get("type", "generic")),
                status=model.get("status", MODEL_STATUS_READY),
                description=model.get("description", ""),
                tags=list(model.get("tags", []) or []),
            )
        if not model.id:
            raise ValueError("Algorithm model requires a non-empty id")
        self._models[model.id] = model
        return model

    def set_status(self, model_id: str, status: str) -> None:
        model = self._models.get(model_id)
        if model is not None:
            model.status = status

    def has_model(self, model_id: str) -> bool:
        return _normalize_token(model_id) in {
            _normalize_token(mid) for mid in self._models
        }

    def get(self, model_id: str) -> Optional[AlgorithmModel]:
        return self._models.get(model_id)

    def model_ids(self) -> list[str]:
        return list(self._models.keys())

    def ready_model_ids(self) -> list[str]:
        return [
            model.id
            for model in self._models.values()
            if model.status == MODEL_STATUS_READY
        ]

    def list_models(self) -> list[dict]:
        return [model.snapshot() for model in self._models.values()]

    def deployment_status(self) -> str:
        """Aggregate algorithm deployment state for heartbeat reporting."""
        if not self._models:
            return "none"
        statuses = {model.status for model in self._models.values()}
        if statuses == {MODEL_STATUS_READY}:
            return MODEL_STATUS_READY
        if MODEL_STATUS_READY in statuses:
            return "partial"
        if MODEL_STATUS_LOADING in statuses:
            return MODEL_STATUS_LOADING
        return MODEL_STATUS_UNAVAILABLE

    def metadata(self) -> dict:
        """Flat metadata suitable for Nacos registration/heartbeat."""
        return {
            "models": ",".join(self.model_ids()),
            "models_ready": ",".join(self.ready_model_ids()),
            "models_count": str(len(self._models)),
            "algorithm_deployment_status": self.deployment_status(),
        }

    def snapshot(self) -> dict:
        return {
            "models": self.list_models(),
            "deployment_status": self.deployment_status(),
            "count": len(self._models),
        }


def models_from_metadata(metadata: dict) -> list[str]:
    """Parse deployed model identifiers from an instance's Nacos metadata."""
    if not metadata:
        return []
    tokens: list[str] = []
    for key in ("models", "models_ready", "model"):
        value = metadata.get(key)
        if not value:
            continue
        if isinstance(value, (list, tuple, set)):
            tokens.extend(str(item) for item in value)
            continue
        text = str(value)
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, dict) and item.get("id"):
                    tokens.append(str(item["id"]))
                else:
                    tokens.append(str(item))
            continue
        tokens.extend(part for part in re.split(r"[,;\s]+", text) if part)
    seen = set()
    unique = []
    for token in tokens:
        key = _normalize_token(token)
        if key and key not in seen:
            seen.add(key)
            unique.append(token)
    return unique


def instance_has_model(metadata: dict, required_model: str) -> bool:
    required = _normalize_token(required_model)
    if not required:
        return False
    for token in models_from_metadata(metadata):
        if _normalize_token(token) == required:
            return True
    return False


def _normalize_token(value: str) -> str:
    return re.sub(r"[\s_\-]+", "", str(value or "").strip().lower())
