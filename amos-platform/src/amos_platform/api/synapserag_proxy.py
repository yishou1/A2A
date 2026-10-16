"""Expose selected SynapseRAG evidence APIs through the AMOS origin."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Collection
from typing import Any

from flask import Flask, Response, request


DEFAULT_SYNAPSERAG_URL = "http://127.0.0.1:8000"
SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9._:-]{1,256}$")
FORWARDED_RESPONSE_HEADERS = (
    "Cache-Control",
    "Content-Disposition",
    "Content-Length",
    "Content-Type",
    "ETag",
    "Last-Modified",
    "X-Request-ID",
)


def _configured_url() -> str:
    return os.getenv("SYNAPSERAG_BASE_URL", "").strip().rstrip("/") or DEFAULT_SYNAPSERAG_URL


def _configured_timeout() -> float:
    try:
        return max(1.0, min(float(os.getenv("SYNAPSERAG_TIMEOUT_SECONDS", "30")), 120.0))
    except ValueError:
        return 30.0


def _safe_identifier(value: str) -> str:
    if not SAFE_IDENTIFIER.fullmatch(value):
        raise ValueError("invalid SynapseRAG identifier")
    return value


def _query_string(allowed: Collection[str]) -> str:
    values = []
    for key in allowed:
        for value in request.args.getlist(key):
            values.append((key, value[:256]))
    return urllib.parse.urlencode(values)


def _upstream_error(message: str) -> Response:
    return Response(
        json.dumps({
            "status": "unavailable",
            "error_code": "SYNAPSERAG_UNAVAILABLE",
            "message": message,
        }, ensure_ascii=False),
        status=503,
        content_type="application/json; charset=utf-8",
    )


def register_synapserag_proxy_routes(app: Flask) -> None:
    """Register a fixed-target, read-only proxy for the evidence viewer."""

    app.config["SYNAPSERAG_API_URL"] = _configured_url()
    app.config["SYNAPSERAG_PROXY_TIMEOUT"] = _configured_timeout()
    app.config["SYNAPSERAG_API_TOKEN"] = os.getenv("SYNAPSERAG_API_TOKEN", "").strip()

    def proxy(path: str, *, query_keys: Collection[str] | None = None) -> Response:
        query = _query_string(query_keys or set())
        url = f"{app.config['SYNAPSERAG_API_URL']}{path}"
        if query:
            url = f"{url}?{query}"
        headers = {"Accept": request.headers.get("Accept", "application/json")}
        token = app.config["SYNAPSERAG_API_TOKEN"]
        if token:
            headers["Authorization"] = f"Bearer {token}"
        upstream_request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(
                upstream_request,
                timeout=float(app.config["SYNAPSERAG_PROXY_TIMEOUT"]),
            ) as upstream:
                response_headers: dict[str, Any] = {
                    name: value
                    for name in FORWARDED_RESPONSE_HEADERS
                    if (value := upstream.headers.get(name)) is not None
                }
                return Response(upstream.read(), status=upstream.status, headers=response_headers)
        except urllib.error.HTTPError as exc:
            headers = {
                name: value
                for name in FORWARDED_RESPONSE_HEADERS
                if (value := exc.headers.get(name)) is not None
            }
            return Response(exc.read(), status=exc.code, headers=headers)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return _upstream_error(f"SynapseRAG service is unavailable: {exc}")

    @app.get("/synapserag-api/health")
    def synapserag_health():
        return proxy("/api/health")

    @app.get("/synapserag-api/retrieval-traces")
    def synapserag_trace_list():
        return proxy(
            "/api/retrieval-traces",
            query_keys=("task_id", "workflow_id", "request_id", "limit"),
        )

    @app.get("/synapserag-api/retrieval-traces/<trace_id>")
    def synapserag_trace_detail(trace_id: str):
        try:
            trace_id = _safe_identifier(trace_id)
        except ValueError as exc:
            return Response(str(exc), status=400)
        return proxy(f"/api/retrieval-traces/{trace_id}")

    @app.get("/synapserag-api/retrieval-traces/<trace_id>/graph")
    def synapserag_trace_graph(trace_id: str):
        try:
            trace_id = _safe_identifier(trace_id)
        except ValueError as exc:
            return Response(str(exc), status=400)
        return proxy(
            f"/api/retrieval-traces/{trace_id}/graph",
            query_keys=("query_trace_id", "view", "evidence_node_key", "max_paths"),
        )

    @app.get("/synapserag-api/evidence/<chunk_id>/location")
    def synapserag_evidence_location(chunk_id: str):
        try:
            chunk_id = _safe_identifier(chunk_id)
        except ValueError as exc:
            return Response(str(exc), status=400)
        return proxy(f"/api/evidence/{chunk_id}/location")

    @app.get("/synapserag-api/evidence/<chunk_id>/preview")
    def synapserag_evidence_preview(chunk_id: str):
        try:
            chunk_id = _safe_identifier(chunk_id)
        except ValueError as exc:
            return Response(str(exc), status=400)
        return proxy(f"/api/evidence/{chunk_id}/preview")

    @app.get("/synapserag-api/documents/<document_id>/original")
    def synapserag_original_document(document_id: str):
        try:
            document_id = _safe_identifier(document_id)
        except ValueError as exc:
            return Response(str(exc), status=400)
        return proxy(f"/api/documents/{document_id}/original")


__all__ = ["register_synapserag_proxy_routes"]
