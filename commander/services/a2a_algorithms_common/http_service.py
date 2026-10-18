"""FastAPI factory for python_http_service algorithm endpoints."""
from __future__ import annotations

import time
import traceback
import re
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
    inference_mode: str = "unspecified"


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


def _non_real_result(value: Any, key: str = "") -> bool:
    if isinstance(value, dict):
        flags = {"mock", "is_mock", "use_mock", "fallback", "fallback_used", "use_fallback", "using_fallback", "random_initialized"}
        return any((name in flags and item is True) or _non_real_result(item, name)
                   for name, item in value.items())
    if isinstance(value, list):
        return any(_non_real_result(item, key) for item in value)
    return (isinstance(value, str) and key in {"mode", "inference_mode", "backend", "session_backend", "model"}
            and bool(re.search(r"(?:^|[_\W])(mock|stub|fallback|placeholder|random_initialized)(?:$|[_\W])", value, re.I)))


def create_algorithm_app(
    algorithm_id: str,
    version: str,
    task_family: str,
    predict_fn: PredictFn,
    *,
    model_loaded_callable: Optional[ModelLoadedCallable] = None,
    extra_metadata: Optional[Dict[str, Any]] = None,
    inference_mode_callable: Optional[Callable[[], str]] = None,
    input_schema_path: Optional[str] = None,
) -> FastAPI:
    """Create a FastAPI app exposing /health, /metadata, and /predict."""

    def _model_loaded() -> bool:
        if model_loaded_callable is None:
            return True
        return bool(model_loaded_callable())

    def _mode() -> str:
        if inference_mode_callable is not None:
            return inference_mode_callable()
        return str((extra_metadata or {}).get("inference_mode") or "unspecified")

    app = FastAPI(title=f"{algorithm_id}:{version}", version=version)
    input_validator = None
    if input_schema_path is not None:
        import json
        from pathlib import Path
        from jsonschema import Draft202012Validator

        schema = json.loads(Path(input_schema_path).read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        input_validator = Draft202012Validator(schema)

    @app.get("/health")
    def health() -> dict:
        loaded = _model_loaded()
        mode = _mode()
        return {
            "ok": loaded,
            "status": "mock" if loaded and mode == "mock" else "ready" if loaded else "not_ready",
            "algorithm_id": algorithm_id,
            "version": version,
            "model_loaded": False if mode == "mock" else loaded if model_loaded_callable is not None else None,
            "model_check_performed": model_loaded_callable is not None and mode != "mock",
            "inference_mode": mode,
            "ready_for_real": loaded and (mode == "deterministic" or (mode == "real" and model_loaded_callable is not None)),
            "input_schema_checked": input_validator is not None,
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
        payload["inference_mode"] = _mode()
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
        if input_validator is not None:
            error = next(input_validator.iter_errors(request_body.inputs), None)
            if error is not None:
                path = ".".join(str(part) for part in error.absolute_path)
                raise HTTPException(status_code=422, detail=f"input schema invalid at inputs.{path}: {error.message[:256]}")
        if not _model_loaded():
            raise HTTPException(status_code=503, detail="model not loaded")

        mode = _mode()
        if request_body.params.get("require_real") is True and mode not in {"real", "deterministic"}:
            raise HTTPException(status_code=503, detail=f"real inference required; service mode is {mode}")
        if request_body.params.get("require_real") is True and mode == "real" and model_loaded_callable is None:
            raise HTTPException(status_code=503, detail="real inference required; model readiness has not been checked")

        latency_ms = 0.0
        try:
            outputs = predict_fn(request_body.inputs, request_body.params)
            if request_body.params.get("require_real") is True and _non_real_result(outputs):
                raise RuntimeError("Real inference required; predictor returned a mock, stub or fallback result")
            latency_ms = round((time.perf_counter() - start) * 1000.0, 3)
            result = AlgorithmResult(
                ok=True,
                request_id=req_id,
                trace_id=trace_id,
                algorithm_id=algorithm_id,
                version=version,
                outputs=outputs if isinstance(outputs, dict) else {"result": outputs},
                usage=AlgorithmUsage(latency_ms=latency_ms, inference_mode=mode),
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
                usage=AlgorithmUsage(latency_ms=latency_ms, inference_mode=mode),
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
