"""技能与算法后端基类。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Generic, TypeVar

T = TypeVar("T")


class AlgorithmBackend(ABC, Generic[T]):
    name: str = "base"

    def __init__(self, *, use_mock: bool = True, config: dict[str, Any] | None = None):
        self.use_mock = use_mock
        self.config = config or {}

    @abstractmethod
    def run(self, inputs: dict[str, Any]) -> T:
        ...
