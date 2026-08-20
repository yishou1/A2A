"""远程算法库后端：默认经集中网关 POST /run 调用。"""

from __future__ import annotations

import time
from typing import Any

from agent.algorithm_library.client import AlgorithmLibraryClient
from agent.algorithm_library.endpoints import TIA_ALGORITHM_VERSIONS
from agent.skills.base import AlgorithmBackend


class RemoteAlgorithmBackend(AlgorithmBackend[Any]):
    """通过算法库 HTTP /run（或旧 /predict）调用子算法。"""

    def __init__(
        self,
        *,
        algorithm_id: str,
        name: str,
        config: dict[str, Any] | None = None,
        output_key: str | None = None,
    ):
        super().__init__(use_mock=False, config=config or {})
        self.algorithm_id = algorithm_id
        self.name = name
        self._output_key = output_key
        library_cfg = (self.config.get("algorithm_library") or {})
        self._client = AlgorithmLibraryClient(library_cfg)
        self._version = str(
            (library_cfg.get("versions") or {}).get(algorithm_id)
            or TIA_ALGORITHM_VERSIONS.get(algorithm_id, "1.0.0")
        )
        self.last_invocation: dict[str, Any] | None = None

    def run(self, inputs: dict[str, Any]) -> Any:
        params = {}
        reason = ""
        payload = dict(inputs)
        if isinstance(payload.get("params"), dict):
            params = dict(payload.pop("params"))
        if payload.get("_algorithm_reason") is not None:
            reason = str(payload.pop("_algorithm_reason") or "")
        started = time.perf_counter()
        try:
            envelope = self._client.predict_with_metadata(
                self.algorithm_id,
                payload,
                params=params,
                version=self._version,
                backend_type="python_http_service",
            )
            outputs = envelope["outputs"]
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000.0, 3)
        usage = envelope.get("usage") if isinstance(envelope.get("usage"), dict) else {}
        remote_latency = usage.get("latency_ms") or usage.get("duration_ms")
        duration_source = "algorithm_usage" if remote_latency is not None else "client_measured"
        self.last_invocation = {
            "algorithm_id": self.algorithm_id,
            "algorithm_name": self.name,
            "version": envelope.get("version") or self._version,
            "backend_type": envelope.get("backend_type") or "python_http_service",
            "execution_mode": "algorithm_library",
            "status": "completed",
            "request_id": envelope.get("request_id"),
            "trace_id": envelope.get("trace_id"),
            "params": params,
            "reason": reason,
            "input": payload,
            "output": outputs,
            "usage": usage,
            "duration_ms": round(float(remote_latency), 3) if remote_latency is not None else duration_ms,
            "latency_ms": round(float(remote_latency), 3) if remote_latency is not None else duration_ms,
            "duration_source": duration_source,
        }
        if self._output_key:
            return outputs.get(self._output_key, outputs)
        return outputs
