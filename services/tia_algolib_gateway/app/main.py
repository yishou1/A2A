"""TIA 算法库集中网关。

内部将 /run 转发到各算法包的 POST /predict（9020–9030）。
"""

from __future__ import annotations

import json
import os
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import FastAPI, Query
from pydantic import BaseModel, Field

# 保证可从仓库根导入 agent.*
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.algorithm_library.catalog import (  # noqa: E402
    ALGORITHM_STAGE,
    TIA_DEFAULT_PIPELINE,
    build_algorithm_catalog,
)
from agent.algorithm_library.endpoints import (  # noqa: E402
    TIA_ALGORITHM_PORTS,
    TIA_ALGORITHM_VERSIONS,
    resolve_endpoint,
)

HOST = os.environ.get("TIA_ALGOLIB_PREDICT_HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", os.environ.get("TIA_ALGOLIB_GATEWAY_PORT", "8088")))
FORWARD_TIMEOUT_S = float(os.environ.get("TIA_ALGOLIB_FORWARD_TIMEOUT_S", "60"))


class RunRequest(BaseModel):
    request_id: str = ""
    trace_id: str = ""
    algorithm_id: str
    version: str = "1.0.0"
    backend_type: str = "python_http_service"
    inputs: dict[str, Any] = Field(default_factory=dict)
    params: dict[str, Any] = Field(default_factory=dict)


def _active_registry() -> list[dict[str, Any]]:
    """活跃算法目录。"""
    cards = build_algorithm_catalog(host=HOST)
    out: list[dict[str, Any]] = []
    for card in cards:
        aid = card["algorithm_id"]
        out.append(
            {
                "algorithm_id": aid,
                "version": card.get("version", "1.0.0"),
                "backend_type": "python_http_service",
                "status": "active",
                "task_family": card.get("task_family", ""),
                "capabilities": card.get("capabilities", []),
                "input_schema_summary": {"required": card.get("required_fields", [])},
                "agent_card": card.get("agent_card") or {"summary": card.get("summary", "")},
                "summary": card.get("summary", ""),
                "optional": card.get("optional", True),
                "stage": card.get("stage") or ALGORITHM_STAGE.get(aid, ""),
                "predict_endpoint": card.get("predict_endpoint"),
            }
        )
    return out


def _forward_predict(req: RunRequest) -> dict[str, Any]:
    endpoint = resolve_endpoint(
        req.algorithm_id,
        {"host": HOST},
    )
    payload = {
        "request_id": req.request_id,
        "trace_id": req.trace_id,
        "algorithm_id": req.algorithm_id,
        "version": req.version or TIA_ALGORITHM_VERSIONS.get(req.algorithm_id, "1.0.0"),
        "inputs": req.inputs,
        "params": req.params,
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        endpoint,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=FORWARD_TIMEOUT_S) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return {
            "ok": False,
            "request_id": req.request_id,
            "trace_id": req.trace_id,
            "algorithm_id": req.algorithm_id,
            "version": req.version,
            "outputs": {},
            "usage": {"latency_ms": 0.0},
            "error": {"code": f"HTTP_{exc.code}", "message": detail},
        }
    except URLError as exc:
        return {
            "ok": False,
            "request_id": req.request_id,
            "trace_id": req.trace_id,
            "algorithm_id": req.algorithm_id,
            "version": req.version,
            "outputs": {},
            "usage": {"latency_ms": 0.0},
            "error": {
                "code": "UPSTREAM_UNAVAILABLE",
                "message": f"cannot reach {endpoint}: {exc}",
            },
        }


app = FastAPI(title="TIA Algorithm Library Gateway", version="1.0.0")


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "status": "ready",
        "service": "tia_algolib_gateway",
        "predict_host": HOST,
        "algorithms": len(TIA_ALGORITHM_PORTS),
        "pipeline": TIA_DEFAULT_PIPELINE,
    }


@app.get("/algorithms")
def list_algorithms(active_only: bool = Query(default=True)) -> dict[str, Any]:
    algorithms = _active_registry()
    if not active_only:
        # 当前网关仅托管 active TIA 卡；保留参数以兼容 lzh 客户端
        pass
    return {"algorithms": algorithms}


@app.get("/algorithms/{algorithm_id}/{version}/{backend_type}")
def get_algorithm(algorithm_id: str, version: str, backend_type: str) -> dict[str, Any]:
    for item in _active_registry():
        if (
            item["algorithm_id"] == algorithm_id
            and item["version"] == version
            and item["backend_type"] == backend_type
        ):
            return item
    return {
        "ok": False,
        "error": {
            "code": "NOT_FOUND",
            "message": f"{algorithm_id}/{version}/{backend_type} not found",
        },
    }


@app.post("/run")
def run_algorithm(body: RunRequest) -> dict[str, Any]:
    start = time.perf_counter()
    active = {
        item["algorithm_id"]: item
        for item in _active_registry()
        if item.get("status") == "active"
    }
    if body.algorithm_id not in active:
        return {
            "ok": False,
            "request_id": body.request_id,
            "trace_id": body.trace_id,
            "algorithm_id": body.algorithm_id,
            "version": body.version,
            "outputs": {},
            "usage": {"latency_ms": 0.0},
            "error": {
                "code": "ALGORITHM_NOT_ACTIVE",
                "message": f"algorithm is not active: {body.algorithm_id}",
            },
        }

    meta = active[body.algorithm_id]
    if body.version and body.version != meta["version"]:
        return {
            "ok": False,
            "request_id": body.request_id,
            "trace_id": body.trace_id,
            "algorithm_id": body.algorithm_id,
            "version": body.version,
            "outputs": {},
            "usage": {"latency_ms": 0.0},
            "error": {
                "code": "VERSION_MISMATCH",
                "message": f"expected version {meta['version']}, got {body.version}",
            },
        }
    if body.backend_type and body.backend_type != meta["backend_type"]:
        return {
            "ok": False,
            "request_id": body.request_id,
            "trace_id": body.trace_id,
            "algorithm_id": body.algorithm_id,
            "version": body.version,
            "outputs": {},
            "usage": {"latency_ms": 0.0},
            "error": {
                "code": "BACKEND_MISMATCH",
                "message": f"expected backend {meta['backend_type']}, got {body.backend_type}",
            },
        }

    result = _forward_predict(body)
    if "usage" not in result or not isinstance(result.get("usage"), dict):
        result["usage"] = {"latency_ms": round((time.perf_counter() - start) * 1000.0, 3)}
    elif not result["usage"].get("latency_ms"):
        result["usage"]["latency_ms"] = round((time.perf_counter() - start) * 1000.0, 3)
    result.setdefault("backend_type", body.backend_type or "python_http_service")
    return result


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=PORT)
