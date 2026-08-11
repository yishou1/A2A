"""远程算法库后端：默认经集中网关 POST /run 调用。"""

from __future__ import annotations

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

    def run(self, inputs: dict[str, Any]) -> Any:
        params = {}
        payload = dict(inputs)
        if isinstance(payload.get("params"), dict):
            params = dict(payload.pop("params"))
        outputs = self._client.predict(
            self.algorithm_id,
            payload,
            params=params,
            version=self._version,
            backend_type="python_http_service",
        )
        if self._output_key:
            return outputs.get(self._output_key, outputs)
        return outputs
