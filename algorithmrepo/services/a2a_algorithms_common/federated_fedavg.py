"""Shape-safe implementation of federated weighted model averaging (FedAvg)."""
from __future__ import annotations

from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
from typing import Any


METADATA_RELATIVE_PATH = Path("models/federated_fedavg_reference.metadata.json")


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normalized_text_sha256(path: Path) -> str:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@lru_cache(maxsize=1)
def load_reference_metadata() -> dict[str, Any]:
    return json.loads((_root() / METADATA_RELATIVE_PATH).read_text(encoding="utf-8"))


def implementation_loaded() -> bool:
    metadata_path = _root() / METADATA_RELATIVE_PATH
    source_path = Path(__file__).resolve()
    if not metadata_path.is_file():
        return False
    try:
        metadata = load_reference_metadata()
        dataset_path = _root() / metadata["dataset"]["path"]
        weights_path = _root() / metadata["final_global_weights"]["path"]
        return (
            metadata.get("implementation_sha256") == _normalized_text_sha256(source_path)
            and dataset_path.is_file()
            and metadata["dataset"]["sha256"] == _sha256(dataset_path)
            and weights_path.is_file()
            and metadata["final_global_weights"]["sha256"] == _sha256(weights_path)
        )
    except (KeyError, OSError, ValueError, json.JSONDecodeError):
        return False


Tensor = float | list["Tensor"]


def _tensor(value: Any, path: str) -> Tensor:
    if isinstance(value, bool):
        raise ValueError(f"{path} must contain finite numbers")
    if isinstance(value, (int, float)):
        numeric = float(value)
        if not math.isfinite(numeric):
            raise ValueError(f"{path} must contain finite numbers")
        return numeric
    if not isinstance(value, list) or not value:
        raise ValueError(f"{path} must be a number or non-empty numeric array")
    children = [_tensor(item, f"{path}[{index}]") for index, item in enumerate(value)]
    child_shapes = {_shape(item) for item in children}
    if len(child_shapes) != 1:
        raise ValueError(f"{path} must not be a ragged array")
    return children


def _shape(tensor: Tensor) -> tuple[int, ...]:
    if isinstance(tensor, float):
        return ()
    return (len(tensor), *_shape(tensor[0]))


def _zeros_like(tensor: Tensor) -> Tensor:
    if isinstance(tensor, float):
        return 0.0
    return [_zeros_like(item) for item in tensor]


def _add_weighted(accumulator: Tensor, tensor: Tensor, weight: float) -> Tensor:
    if isinstance(accumulator, float) and isinstance(tensor, float):
        return accumulator + tensor * weight
    if isinstance(accumulator, list) and isinstance(tensor, list):
        return [
            _add_weighted(left, right, weight)
            for left, right in zip(accumulator, tensor)
        ]
    raise ValueError("all client tensors must have identical shapes")


def _round_tensor(tensor: Tensor) -> float | list:
    if isinstance(tensor, float):
        rounded = round(tensor, 12)
        return 0.0 if rounded == -0.0 else rounded
    return [_round_tensor(item) for item in tensor]


def _tensor_squared_norm(tensor: Tensor) -> float:
    if isinstance(tensor, float):
        return tensor * tensor
    return sum(_tensor_squared_norm(item) for item in tensor)


def federated_average(inputs: dict, params: dict) -> dict:
    updates = inputs.get("client_updates")
    if not isinstance(updates, list) or not updates:
        raise ValueError("client_updates must be a non-empty array")
    if len(updates) > 1000:
        raise ValueError("client_updates cannot contain more than 1000 clients")

    minimum_clients = params.get("minimum_clients", 2)
    if isinstance(minimum_clients, bool) or not isinstance(minimum_clients, int):
        raise ValueError("minimum_clients must be an integer")
    if minimum_clients < 1 or minimum_clients > 1000:
        raise ValueError("minimum_clients must be between 1 and 1000")
    if len(updates) < minimum_clients:
        raise ValueError(
            f"at least {minimum_clients} client updates are required for this round"
        )

    parsed: list[tuple[str, int, dict[str, Tensor]]] = []
    client_ids: set[str] = set()
    expected_keys: list[str] | None = None
    expected_shapes: dict[str, tuple[int, ...]] = {}
    for update_index, update in enumerate(updates):
        if not isinstance(update, dict):
            raise ValueError(f"client_updates[{update_index}] must be an object")
        client_id = str(update.get("client_id") or "").strip()
        if not client_id:
            raise ValueError(f"client_updates[{update_index}].client_id is required")
        if client_id in client_ids:
            raise ValueError(f"duplicate client_id: {client_id}")
        client_ids.add(client_id)

        sample_count = update.get("sample_count")
        if isinstance(sample_count, bool) or not isinstance(sample_count, int) or sample_count < 1:
            raise ValueError(f"client_updates[{update_index}].sample_count must be a positive integer")
        weights = update.get("weights")
        if not isinstance(weights, dict) or not weights:
            raise ValueError(f"client_updates[{update_index}].weights must be a non-empty object")
        keys = sorted(str(key) for key in weights)
        if expected_keys is None:
            expected_keys = keys
        elif keys != expected_keys:
            raise ValueError("all clients must submit the same weight tensor names")

        parsed_weights: dict[str, Tensor] = {}
        for name in keys:
            parsed_weights[name] = _tensor(weights[name], f"{client_id}.weights.{name}")
            shape = _shape(parsed_weights[name])
            if name not in expected_shapes:
                expected_shapes[name] = shape
            elif expected_shapes[name] != shape:
                raise ValueError(f"all clients must use shape {expected_shapes[name]} for {name}")
        parsed.append((client_id, sample_count, parsed_weights))

    total_samples = sum(item[1] for item in parsed)
    aggregated = {
        name: _zeros_like(parsed[0][2][name])
        for name in expected_keys or []
    }
    participants: list[dict] = []
    for client_id, sample_count, weights in parsed:
        aggregation_weight = sample_count / total_samples
        for name in aggregated:
            aggregated[name] = _add_weighted(
                aggregated[name], weights[name], aggregation_weight
            )
        update_norm = math.sqrt(sum(_tensor_squared_norm(value) for value in weights.values()))
        participants.append(
            {
                "client_id": client_id,
                "sample_count": sample_count,
                "aggregation_weight": round(aggregation_weight, 12),
                "update_l2_norm": round(update_norm, 12),
            }
        )

    rounded_weights = {name: _round_tensor(value) for name, value in aggregated.items()}
    fingerprint = hashlib.sha256(
        json.dumps(rounded_weights, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "round_id": str(inputs.get("round_id") or ""),
        "aggregated_weights": rounded_weights,
        "tensor_shapes": {name: list(shape) for name, shape in expected_shapes.items()},
        "participants": participants,
        "participant_count": len(parsed),
        "total_samples": total_samples,
        "aggregation_method": "fedavg_weighted_by_sample_count",
        "global_weights_sha256": fingerprint,
        "model_runtime": {
            "backend": "native_python_fedavg",
            "used": True,
            "implementation_version": "1.0.0",
        },
    }
