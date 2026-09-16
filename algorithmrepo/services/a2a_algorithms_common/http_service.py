"""FastAPI factory for python_http_service algorithm endpoints."""
from __future__ import annotations

import time
import traceback
from typing import Any, Callable, Dict, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


class AlgorithmRequest(BaseModel):
    request_id: str = ""
    trace_id: str = ""
    algorithm_id: str = ""
    version: str = ""
    inputs: Dict[str, Any] = Field(default_factory=dict)
    params: Dict[str, Any] = Field(default_factory=dict)


class AlgorithmError(BaseModel):
    code: str
    message: str


class AlgorithmUsage(BaseModel):
    latency_ms: float


class AlgorithmResult(BaseModel):
    ok: bool
    request_id: str = ""
    trace_id: str = ""
    algorithm_id: str = ""
    version: str = ""
    outputs: Dict[str, Any] = Field(default_factory=dict)
    usage: AlgorithmUsage = Field(default_factory=lambda: AlgorithmUsage(latency_ms=0.0))
    error: Optional[AlgorithmError] = None


PredictFn = Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]]
ModelLoadedCallable = Callable[[], bool]


def create_algorithm_app(
    algorithm_id: str,
    version: str,
    task_family: str,
    predict_fn: PredictFn,
    *,
    model_loaded_callable: Optional[ModelLoadedCallable] = None,
    extra_metadata: Optional[Dict[str, Any]] = None,
) -> FastAPI:
    """Create a FastAPI app exposing /health, /metadata, and /predict."""

    def _model_loaded() -> bool:
        if model_loaded_callable is None:
            return True
        return bool(model_loaded_callable())

    app = FastAPI(title=f"{algorithm_id}:{version}", version=version)

    @app.get("/health")
    def health() -> dict:
        loaded = _model_loaded()
        return {
            "ok": loaded,
            "status": "ready" if loaded else "not_ready",
            "algorithm_id": algorithm_id,
            "version": version,
            "model_loaded": loaded,
        }

    @app.get("/metadata")
    def metadata() -> dict:
        payload = {
            "algorithm_id": algorithm_id,
            "version": version,
            "backend_type": "python_http_service",
            "task_family": task_family,
        }
        payload.update(extra_metadata or {})
        return payload

    @app.post("/predict")
    def predict(request_body: AlgorithmRequest) -> dict:
        start = time.perf_counter()
        req_id = request_body.request_id or ""
        trace_id = request_body.trace_id or ""

        if request_body.algorithm_id and request_body.algorithm_id != algorithm_id:
            raise HTTPException(status_code=404, detail="algorithm_id mismatch")
        if request_body.version and request_body.version != version:
            raise HTTPException(status_code=404, detail="version mismatch")
        if not _model_loaded():
            raise HTTPException(status_code=503, detail="model not loaded")

        latency_ms = 0.0
        try:
            outputs = predict_fn(request_body.inputs, request_body.params)
            latency_ms = round((time.perf_counter() - start) * 1000.0, 3)
            result = AlgorithmResult(
                ok=True,
                request_id=req_id,
                trace_id=trace_id,
                algorithm_id=algorithm_id,
                version=version,
                outputs=outputs if isinstance(outputs, dict) else {"result": outputs},
                usage=AlgorithmUsage(latency_ms=latency_ms),
                error=None,
            )
            return result.model_dump()
        except Exception as exc:
            latency_ms = round((time.perf_counter() - start) * 1000.0, 3)
            result = AlgorithmResult(
                ok=False,
                request_id=req_id,
                trace_id=trace_id,
                algorithm_id=algorithm_id,
                version=version,
                outputs={},
                usage=AlgorithmUsage(latency_ms=latency_ms),
                error=AlgorithmError(code=type(exc).__name__, message=str(exc)),
            )
            return result.model_dump()

    @app.exception_handler(HTTPException)
    async def http_exception_handler(_request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "ok": False,
                "error": {"code": f"HTTP_{exc.status_code}", "message": str(exc.detail)},
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "error": {"code": type(exc).__name__, "message": str(exc)},
                "detail": traceback.format_exc(),
            },
        )

    return app
