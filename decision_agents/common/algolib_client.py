"""HTTP client for the algorithm library server."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from decision_agents.common.config import Settings


class AlgorithmLibraryError(RuntimeError):
    pass


@dataclass(frozen=True)
class AlgorithmRunCall:
    algorithm_id: str
    version: str
    backend_type: str
    inputs: dict[str, Any]
    params: dict[str, Any]
    reason: str = ""


class AlgorithmLibraryClient:
    def __init__(self, settings: Settings) -> None:
        self.base_url = settings.algolib_base_url.rstrip("/")
        self.timeout = settings.algolib_timeout_seconds

    def list_algorithms(self) -> list[dict[str, Any]]:
        try:
            response = httpx.get(f"{self.base_url}/algorithms", timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            raise AlgorithmLibraryError(f"Algorithm library list failed: {exc}") from exc
        except ValueError as exc:
            raise AlgorithmLibraryError("Algorithm library returned invalid JSON.") from exc
        algorithms = payload.get("algorithms")
        if not isinstance(algorithms, list):
            raise AlgorithmLibraryError("Algorithm library response missing algorithms list.")
        return [item for item in algorithms if isinstance(item, dict)]

    def run_algorithm(
        self,
        *,
        request_id: str,
        trace_id: str,
        call: AlgorithmRunCall,
    ) -> dict[str, Any]:
        payload = {
            "request_id": request_id,
            "trace_id": trace_id,
            "algorithm_id": call.algorithm_id,
            "version": call.version,
            "backend_type": call.backend_type,
            "inputs": call.inputs,
            "params": call.params,
        }
        try:
            response = httpx.post(f"{self.base_url}/run", json=payload, timeout=self.timeout)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as exc:
            raise AlgorithmLibraryError(f"Algorithm library run failed: {exc}") from exc
        except ValueError as exc:
            raise AlgorithmLibraryError("Algorithm library run returned invalid JSON.") from exc
