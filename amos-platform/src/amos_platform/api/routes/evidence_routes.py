"""Read-only, causal evidence-product routes."""

from __future__ import annotations

from typing import Any

from flask import Response

from amos_platform.api.dependencies import get_engine
from amos_platform.api.responses import err, ok
from amos_platform.data.scenario_repository import get_scenario
from amos_platform.media.evidence_products import get_evidence_product_service


def register_evidence_routes(bp: Any) -> None:
    """Register current-state product manifest and frozen SVG retrieval."""

    @bp.route("/api/v1/evidence-products", methods=["GET"])
    def evidence_products_manifest():
        engine = get_engine()
        operator_state = engine.get_operator_state()
        clock = operator_state.get("clock") or {}
        scenario_id = clock.get("scenario_id")
        scenario = get_scenario(str(scenario_id)) if scenario_id else None
        if not scenario:
            return ok({
                "run_id": clock.get("run_id"),
                "snapshot_at_sec": clock.get("elapsed_sec"),
                "products": [],
            })
        manifest = get_evidence_product_service().freeze_manifest(
            operator_state,
            engine.get_agent_visible_state(),
            scenario,
        )
        return ok(manifest)

    @bp.route(
        "/api/v1/evidence-products/<run_id>/<media_id>/<snapshot_token>.svg",
        methods=["GET"],
    )
    def evidence_product_svg(run_id: str, media_id: str, snapshot_token: str):
        # Products are intentionally valid only for the active run.  The
        # lookup itself never renders, so unreleased/future products cannot be
        # obtained by guessing identifiers.
        active_run_id = str(get_engine().clock.get("run_id") or "")
        if not active_run_id or active_run_id != str(run_id):
            return err(404, "evidence product not found"), 404
        product = get_evidence_product_service().get(run_id, media_id, snapshot_token)
        if product is None:
            return err(404, "evidence product not found"), 404
        response = Response(product.content, mimetype="image/svg+xml")
        response.headers["Cache-Control"] = "private, max-age=31536000, immutable"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.set_etag(product.checksum)
        return response


__all__ = ["register_evidence_routes"]
