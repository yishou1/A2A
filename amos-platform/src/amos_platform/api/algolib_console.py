"""Serve the AlgoLib console and proxy its fixed upstream API."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from flask import Flask, Response, abort, request, send_from_directory

from amos_platform.config import REPO_ROOT


DEFAULT_ALGOLIB_API_URL = "http://127.0.0.1:8088"
DEFAULT_ALGOLIB_WEB_DIST = REPO_ROOT.parent / "algorithmrepo" / "web" / "dist"
FORWARDED_REQUEST_HEADERS = (
    "Accept",
    "Content-Type",
    "X-Request-ID",
    "X-Trace-ID",
)
FORWARDED_RESPONSE_HEADERS = (
    "Cache-Control",
    "Content-Type",
    "X-Request-ID",
    "X-Trace-ID",
)


def _configured_dist() -> Path:
    configured = os.getenv("ALGOLIB_WEB_DIST", "").strip()
    if not configured:
        return DEFAULT_ALGOLIB_WEB_DIST
    path = Path(configured).expanduser()
    return path if path.is_absolute() else REPO_ROOT.parent / path


def _configured_api_url() -> str:
    return os.getenv("ALGOLIB_API_URL", "").strip().rstrip("/") or DEFAULT_ALGOLIB_API_URL


def _configured_timeout() -> float:
    try:
        return max(1.0, min(float(os.getenv("ALGOLIB_PROXY_TIMEOUT", "30")), 300.0))
    except ValueError:
        return 30.0


def _response_headers(headers: Any) -> dict[str, str]:
    return {
        name: value
        for name in FORWARDED_RESPONSE_HEADERS
        if (value := headers.get(name)) is not None
    }


def _proxy_error(message: str) -> Response:
    payload = {
        "ok": False,
        "error_code": "ALGOLIB_UNAVAILABLE",
        "message": message,
    }
    return Response(
        json.dumps(payload, ensure_ascii=False),
        status=503,
        content_type="application/json; charset=utf-8",
    )


def register_algolib_console_routes(app: Flask) -> None:
    """Register the same-origin console shell and fixed-target API proxy."""

    app.config["ALGOLIB_WEB_DIST"] = _configured_dist()
    app.config["ALGOLIB_API_URL"] = _configured_api_url()
    app.config["ALGOLIB_PROXY_TIMEOUT"] = _configured_timeout()

    @app.route("/algolib", defaults={"asset_path": ""})
    @app.route("/algolib/", defaults={"asset_path": ""})
    @app.route("/algolib/<path:asset_path>")
    def algolib_console(asset_path: str):
        dist = Path(app.config["ALGOLIB_WEB_DIST"])
        if not (dist / "index.html").is_file():
            abort(
                503,
                description="AlgoLib console is not built. Run npm run build in algorithmrepo/web.",
            )
        requested = dist / asset_path
        if asset_path and requested.is_file():
            return send_from_directory(dist, asset_path)
        return send_from_directory(dist, "index.html")

    @app.route(
        "/algolib-api",
        defaults={"upstream_path": ""},
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    )
    @app.route(
        "/algolib-api/<path:upstream_path>",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    )
    def algolib_api_proxy(upstream_path: str):
        url = f"{app.config['ALGOLIB_API_URL']}/{upstream_path.lstrip('/')}"
        if request.query_string:
            url = f"{url}?{request.query_string.decode('latin-1')}"
        headers = {
            name: value
            for name in FORWARDED_REQUEST_HEADERS
            if (value := request.headers.get(name)) is not None
        }
        upstream_request = urllib.request.Request(
            url,
            data=request.get_data() or None,
            headers=headers,
            method=request.method,
        )
        try:
            with urllib.request.urlopen(
                upstream_request,
                timeout=float(app.config["ALGOLIB_PROXY_TIMEOUT"]),
            ) as upstream:
                return Response(
                    upstream.read(),
                    status=upstream.status,
                    headers=_response_headers(upstream.headers),
                )
        except urllib.error.HTTPError as exc:
            return Response(
                exc.read(),
                status=exc.code,
                headers=_response_headers(exc.headers),
            )
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return _proxy_error(f"AlgoLib service is unavailable: {exc}")


__all__ = ["register_algolib_console_routes"]
