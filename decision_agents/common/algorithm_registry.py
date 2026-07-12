"""Algorithm registry primitives."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from decision_agents.common.schemas import AgentRequest


ParameterSize = Literal["small", "medium", "large"]
AlgorithmRunner = Callable[[AgentRequest], dict[str, Any]]


@dataclass(frozen=True)
class AlgorithmSpec:
    algorithm_id: str
    category: str
    parameter_size: ParameterSize
    required_fields: tuple[str, ...]
    run_fn: AlgorithmRunner


class UnknownAlgorithmError(ValueError):
    def __init__(self, algorithm_id: str, available_algorithms: list[str]) -> None:
        self.algorithm_id = algorithm_id
        self.available_algorithms = available_algorithms
        super().__init__(
            f"Unknown algorithm_id '{algorithm_id}'. Available algorithms: "
            f"{', '.join(available_algorithms)}"
        )


def missing_required_fields(request: AgentRequest, fields: tuple[str, ...]) -> list[str]:
    missing = []
    for field in fields:
        if field not in request.model_fields_set:
            missing.append(field)
            continue
        value = getattr(request, field)
        if value is None or value == [] or value == {}:
            missing.append(field)
    return missing


def select_algorithm(
    request: AgentRequest,
    algorithms: list[AlgorithmSpec],
) -> AlgorithmSpec:
    if request.algorithm_id:
        for algorithm in algorithms:
            if algorithm.algorithm_id == request.algorithm_id:
                return algorithm
        raise UnknownAlgorithmError(
            request.algorithm_id,
            [algorithm.algorithm_id for algorithm in algorithms],
        )
    preferred_size = request.agent_profile.compute_budget
    for algorithm in algorithms:
        if algorithm.parameter_size == preferred_size:
            return algorithm
    return algorithms[0]
