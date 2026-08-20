"""Read-only run archive and acceptance-report routes."""

from __future__ import annotations

import json
import re
from typing import Any

from flask import Response, request

from amos_platform.api.dependencies import get_runtime
from amos_platform.api.responses import err, ok
from amos_platform.runtime.run_manifest import render_html_report, render_markdown_report


def _download_response(body: str, *, run_id: str, mimetype: str, extension: str) -> Response:
    safe_run_id = re.sub(r"[^A-Za-z0-9_.-]", "_", run_id)[:80] or "run"
    response = Response(body, status=200, mimetype=mimetype)
    response.headers["Content-Disposition"] = (
        f'attachment; filename="amos-{safe_run_id}-report.{extension}"'
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    if extension == "html":
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'"
        )
    return response


def register_run_routes(bp: Any) -> None:
    """Register safe list, detail, and report endpoints."""

    @bp.route("/api/v1/runs", methods=["GET"])
    def run_list():
        try:
            limit = int(request.args.get("limit", 50))
            offset = int(request.args.get("offset", 0))
        except (TypeError, ValueError):
            return err(400, "limit and offset must be integers"), 400
        if limit < 1 or limit > 100 or offset < 0:
            return err(400, "limit must be 1 to 100 and offset must be non-negative"), 400
        runs = get_runtime().get_run_manifest_store().list(limit=limit, offset=offset)
        return ok({"runs": runs, "count": len(runs), "limit": limit, "offset": offset})

    @bp.route("/api/v1/runs/<run_id>", methods=["GET"])
    def run_detail(run_id: str):
        manifest = get_runtime().get_run_manifest_store().get(run_id)
        if manifest is None:
            return err(404, f"run not found: {run_id}"), 404
        return ok(manifest)

    @bp.route("/api/v1/runs/<run_id>/report", methods=["GET"])
    def run_report(run_id: str):
        manifest = get_runtime().get_run_manifest_store().get(run_id)
        if manifest is None:
            return err(404, f"run not found: {run_id}"), 404
        report_format = str(request.args.get("format", "json")).lower()
        if report_format == "json":
            body = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
            return _download_response(
                body, run_id=run_id, mimetype="application/json", extension="json"
            )
        if report_format == "markdown":
            return _download_response(
                render_markdown_report(manifest),
                run_id=run_id,
                mimetype="text/markdown",
                extension="md",
            )
        if report_format == "html":
            return _download_response(
                render_html_report(manifest),
                run_id=run_id,
                mimetype="text/html",
                extension="html",
            )
        return err(400, "format must be json, markdown, or html"), 400


__all__ = ["register_run_routes"]
