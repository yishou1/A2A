"""Standalone AlgoLib adapters for the bundled SynapseRAG HTTP service."""

import os
import time
import uuid

import requests
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


class Invocation(BaseModel):
    request_id: str = ""
    trace_id: str = ""
    algorithm_id: str
    version: str
    inputs: dict = Field(default_factory=dict)
    params: dict = Field(default_factory=dict)


def create_app(algorithm_id, version, endpoint):
    app = FastAPI(title=algorithm_id, version=version)

    @app.get("/metadata")
    def metadata():
        return {"algorithm_id": algorithm_id, "version": version,
                "backend_type": "python_http_service", "task_family": "retrieval"}

    @app.get("/health")
    def health():
        try:
            response = requests.get(base_url() + "/api/health", timeout=3)
            response.raise_for_status()
            ready = response.json().get("indexed") is True
        except (requests.RequestException, ValueError):
            ready = False
        return {**metadata(), "ok": ready, "model_loaded": ready,
                "status": "ready" if ready else "not_ready"}

    @app.post("/predict")
    def predict(invocation: Invocation):
        started = time.perf_counter()
        result = {"ok": False, "request_id": invocation.request_id,
                  "trace_id": invocation.trace_id, "algorithm_id": algorithm_id,
                  "version": version, "outputs": {}, "error": None}
        code, message = "", ""
        if invocation.algorithm_id != algorithm_id or invocation.version != version:
            code, message = "ALGORITHM_INPUT_ERROR", "algorithm identity mismatch"
        elif invocation.params:
            code, message = "ALGORITHM_INPUT_ERROR", "params must be empty; use inputs"
        else:
            body = dict(invocation.inputs)
            if endpoint == "/api/retrieve":
                body.setdefault("request_id", invocation.request_id or uuid.uuid4().hex)
                body.setdefault("purpose", "general")
                body.setdefault("explain", {"enabled": True, "level": "summary"})
            try:
                response = requests.post(
                    base_url() + endpoint, json=body,
                    headers={"Authorization": "Bearer " + os.getenv("SYNAPSERAG_API_TOKEN", "")},
                    timeout=float(os.getenv("SYNAPSERAG_TIMEOUT_SECONDS", "60")),
                    allow_redirects=False,
                )
                data = response.json()
                if response.status_code != 200:
                    detail = data.get("detail", {})
                    code = detail.get("error_code", "ALGORITHM_INPUT_ERROR" if response.status_code == 422 else "RAG_SERVICE_ERROR") if isinstance(detail, dict) else "ALGORITHM_INPUT_ERROR"
                    message = detail.get("message", "request rejected") if isinstance(detail, dict) else "input validation failed"
                elif data.get("status") != "success":
                    code, message = "INVALID_UPSTREAM_RESPONSE", "missing success status"
                elif endpoint == "/api/retrieve" and not isinstance(data.get("results"), list):
                    code, message = "INVALID_UPSTREAM_RESPONSE", "missing retrieval results"
                elif endpoint == "/api/retrieve" and (
                    [item.get("query_id") for item in data["results"]] !=
                    [item.get("query_id") for item in body.get("queries", [])]
                    or not all(isinstance(item.get("evidence"), list) for item in data["results"])
                    or not isinstance(data.get("retrieval_profile"), dict)
                ):
                    code, message = "INVALID_UPSTREAM_RESPONSE", "incomplete retrieval results"
                elif endpoint == "/api/graph/explore" and not all(isinstance(data.get(k), list) for k in ("nodes", "edges", "paths")):
                    code, message = "INVALID_UPSTREAM_RESPONSE", "missing graph arrays"
                else:
                    result.update(ok=True, outputs=data)
            except requests.Timeout:
                code, message = "RAG_TIMEOUT", "SynapseRAG request timed out"
            except requests.RequestException:
                code, message = "RAG_SERVICE_UNAVAILABLE", "SynapseRAG connection failed"
            except (ValueError, TypeError, AttributeError):
                code, message = "INVALID_UPSTREAM_RESPONSE", "invalid SynapseRAG response or configuration"
        if code:
            result["error"] = {"code": code, "message": message}
        result["usage"] = {"latency_ms": round((time.perf_counter()-started)*1000, 3)}
        return JSONResponse(result)

    return app


def base_url():
    return os.getenv("SYNAPSERAG_BASE_URL", "http://127.0.0.1:8000").rstrip("/")


retriever = create_app("synapse_rag_retriever", "2.0.0", "/api/retrieve")
graph_explorer = create_app("synapse_graph_explorer", "1.0.0", "/api/graph/explore")
