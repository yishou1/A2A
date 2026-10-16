"""Browser-facing endpoint for detecting objects in released AMOS imagery."""

from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

from flask import request

from amos_platform.algorithms import RTDETRClient, RTDETRServiceError
from amos_platform.api.dependencies import get_engine
from amos_platform.api.responses import err, ok


SUPPORTED_IMAGE_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/webp"}


def get_rtdetr_client() -> RTDETRClient:
    """Construct from environment so endpoint changes do not require app rebuilds."""
    return RTDETRClient()


def register_detection_routes(bp: Any) -> None:
    """Register released-media object detection routes."""

    @bp.route("/api/v1/a2a/media/<media_id>/detect", methods=["POST"])
    def detect_released_media(media_id: str):
        state = get_engine().get_operator_state()
        media_items = ((state.get("scenario_story") or {}).get("media_cues") or [])
        media = next(
            (item for item in media_items if str(item.get("media_id") or "") == media_id),
            None,
        )
        if media is None:
            return err(404, "观测资料不存在或尚未发布"), 404

        mime_type = str(media.get("mime_type") or media.get("media_type") or "").lower()
        if mime_type not in SUPPORTED_IMAGE_TYPES:
            return err(415, "该观测资料不是 RT-DETR 支持的栅格图片", {"mime_type": mime_type}), 415
        uri = str(media.get("uri") or media.get("media_uri") or "")
        if not uri.startswith("/"):
            return err(422, "观测资料缺少 AMOS 本地资源地址"), 422

        image_url = urljoin(request.host_url, uri.lstrip("/"))
        try:
            result = get_rtdetr_client().detect(media, image_url)
        except RTDETRServiceError as exc:
            return err(exc.status_code, str(exc), exc.details), exc.status_code
        result.update({
            "media_id": media_id,
            "source_uri": uri,
            "checksum": media.get("checksum"),
        })
        return ok(result)


__all__ = ["register_detection_routes"]
