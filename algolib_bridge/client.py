"""HTTP client for algorithm-library gateway and direct package endpoints."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import requests

from algolib_bridge.config import AlgolibSettings, resolve_direct_endpoint


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
    def __init__(self, settings: Optional[AlgolibSettings] = None) -> None:
        self.settings = settings or AlgolibSettings.load()

    def list_algorithms(self) -> list[dict[str, Any]]:
        if self.settings.transport == "direct":
            # Direct mode has no catalog API; return synthetic active entries.
            from algolib_bridge.config import DEFAULT_DIRECT_ENDPOINTS

            return [
                {
                    "algorithm_id": algorithm_id,
                    "version": self.settings.default_version,
                    "backend_type": self.settings.default_backend_type,
                    "endpoint": resolve_direct_endpoint(algorithm_id),
                    "status": "active",
                }
                for algorithm_id in DEFAULT_DIRECT_ENDPOINTS
            ]
        try:
            response = requests.get(
                f"{self.settings.base_url}/algorithms",
                timeout=self.settings.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise AlgorithmLibraryError(f"Algorithm library list failed: {exc}") from exc
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
            if self.settings.transport == "direct":
                url = resolve_direct_endpoint(call.algorithm_id)
                response = requests.post(url, json=payload, timeout=self.settings.timeout_seconds)
            else:
                response = requests.post(
                    f"{self.settings.base_url}/run",
                    json=payload,
                    timeout=self.settings.timeout_seconds,
                )
            response.raise_for_status()
            result = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise AlgorithmLibraryError(f"Algorithm library run failed: {exc}") from exc
        if not isinstance(result, dict):
            raise AlgorithmLibraryError("Algorithm library returned non-object JSON.")
        return result

    def run_outputs(
        self,
        *,
        algorithm_id: str,
        inputs: dict[str, Any],
        params: Optional[dict[str, Any]] = None,
        request_id: str = "zh-agent",
        trace_id: str = "zh-agent",
        version: Optional[str] = None,
    ) -> dict[str, Any]:
        call = AlgorithmRunCall(
            algorithm_id=algorithm_id,
            version=version or self.settings.default_version,
            backend_type=self.settings.default_backend_type,
            inputs=inputs,
            params=dict(params or {}),
        )
        result = self.run_algorithm(request_id=request_id, trace_id=trace_id, call=call)
        if not result.get("ok", False):
            error = result.get("error") if isinstance(result.get("error"), dict) else {}
            raise AlgorithmLibraryError(
                f"{algorithm_id} failed: {error.get('code', 'UNKNOWN')}: {error.get('message', '')}"
            )
        outputs = result.get("outputs")
        if not isinstance(outputs, dict):
            raise AlgorithmLibraryError(f"{algorithm_id} response missing outputs object.")
        return outputs
