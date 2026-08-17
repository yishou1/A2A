"""算法库 HTTP 客户端（可回退分散 /predict）。"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from agent.algorithm_library.endpoints import TIA_ALGORITHM_VERSIONS, resolve_endpoint


class AlgorithmLibraryError(RuntimeError):
    """算法库 HTTP 调用失败。"""


@dataclass(frozen=True)
class AlgorithmRunCall:
    algorithm_id: str
    version: str
    backend_type: str
    inputs: dict[str, Any]
    params: dict[str, Any]
    reason: str = ""


def _resolve_base_url(library_cfg: dict[str, Any]) -> str:
    env = os.environ.get("ALGOLIB_BASE_URL", "").strip()
    if env:
        return env.rstrip("/")
    base = str(library_cfg.get("base_url") or "").strip()
    if base:
        return base.rstrip("/")
    return "http://127.0.0.1:8088"


def _resolve_call_mode(library_cfg: dict[str, Any]) -> str:
    """run = 集中网关（lzh）；predict = 直连各端口（旧路径）。"""
    env = os.environ.get("TIA_ALGOLIB_CALL_MODE", "").strip().lower()
    if env in {"run", "predict"}:
        return env
    mode = str(library_cfg.get("call_mode") or "run").strip().lower()
    return mode if mode in {"run", "predict"} else "run"


class AlgorithmLibraryClient:
    def __init__(self, library_cfg: dict[str, Any] | None = None):
        self._cfg = dict(library_cfg or {})
        self._timeout_s = float(
            os.environ.get("ALGOLIB_TIMEOUT_SECONDS")
            or (float(self._cfg.get("timeout_ms", 30000)) / 1000.0)
        )
        self.base_url = _resolve_base_url(self._cfg)
        self.call_mode = _resolve_call_mode(self._cfg)

    def list_algorithms(self, *, active_only: bool = True) -> list[dict[str, Any]]:
        """对齐 lzh：GET {base}/algorithms。"""
        q = "active_only=true" if active_only else "active_only=false"
        url = f"{self.base_url}/algorithms?{q}"
        try:
            payload = self._get_json(url)
        except AlgorithmLibraryError as exc:
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
        """对齐 lzh：POST {base}/run。"""
        payload = {
            "request_id": request_id,
            "trace_id": trace_id,
            "algorithm_id": call.algorithm_id,
            "version": call.version,
            "backend_type": call.backend_type,
            "inputs": call.inputs,
            "params": call.params,
        }
        url = f"{self.base_url}/run"
        try:
            return self._post_json(url, payload)
        except AlgorithmLibraryError as exc:
            raise AlgorithmLibraryError(f"Algorithm library run failed: {exc}") from exc

    def predict(
        self,
        algorithm_id: str,
        inputs: dict[str, Any],
        *,
        params: dict[str, Any] | None = None,
        request_id: str | None = None,
        trace_id: str | None = None,
        version: str | None = None,
        backend_type: str = "python_http_service",
    ) -> dict[str, Any]:
        """
        统一执行入口：
        - call_mode=run → POST /run（推荐，对齐 lzh）
        - call_mode=predict → 直连各算法 /predict
        """
        req_id = request_id or f"req-{uuid.uuid4().hex[:12]}"
        tr_id = trace_id or os.environ.get("TIA_TRACE_ID", "")
        ver = str(
            version
            or (self._cfg.get("versions") or {}).get(algorithm_id)
            or TIA_ALGORITHM_VERSIONS.get(algorithm_id, "1.0.0")
        )
        if self.call_mode == "run":
            result = self.run_algorithm(
                request_id=req_id,
                trace_id=tr_id,
                call=AlgorithmRunCall(
                    algorithm_id=algorithm_id,
                    version=ver,
                    backend_type=backend_type,
                    inputs=inputs,
                    params=params or {},
                ),
            )
            if not result.get("ok"):
                err = result.get("error") or {}
                code = err.get("code", "AlgorithmError")
                message = err.get("message", "algorithm library run failed")
                raise AlgorithmLibraryError(f"{algorithm_id}@{self.base_url}/run: [{code}] {message}")
            outputs = result.get("outputs")
            if not isinstance(outputs, dict):
                raise AlgorithmLibraryError(f"{algorithm_id}: invalid outputs payload")
            return outputs

        endpoint = resolve_endpoint(algorithm_id, self._cfg)
        payload = {
            "request_id": req_id,
            "trace_id": tr_id,
            "algorithm_id": algorithm_id,
            "version": ver,
            "inputs": inputs,
            "params": params or {},
        }
        body = self._post_json(endpoint, payload)
        if not body.get("ok"):
            err = body.get("error") or {}
            code = err.get("code", "AlgorithmError")
            message = err.get("message", "algorithm library predict failed")
            raise AlgorithmLibraryError(f"{algorithm_id}@{endpoint}: [{code}] {message}")
        outputs = body.get("outputs")
        if not isinstance(outputs, dict):
            raise AlgorithmLibraryError(f"{algorithm_id}: invalid outputs payload")
        return outputs

    def health(self, algorithm_id: str | None = None) -> dict[str, Any]:
        if self.call_mode == "run" or algorithm_id is None:
            return self._get_json(f"{self.base_url}/health")
        endpoint = resolve_endpoint(algorithm_id, self._cfg)
        health_url = endpoint.replace("/predict", "/health")
        return self._get_json(health_url)

    def _post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self._timeout_s) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise AlgorithmLibraryError(f"HTTP {exc.code} {url}: {detail}") from exc
        except URLError as exc:
            raise AlgorithmLibraryError(
                f"无法连接算法库服务 {url}；请先启动网关: "
                f"python -m services.tia_algolib_gateway.app.main "
                f"或 scripts/start_tia_algolib_gateway.ps1"
            ) from exc

    def _get_json(self, url: str) -> dict[str, Any]:
        try:
            with urlopen(url, timeout=self._timeout_s) as response:
                return json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError) as exc:
            raise AlgorithmLibraryError(f"GET failed {url}: {exc}") from exc
