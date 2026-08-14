"""从对象存储 URI / 签名 URL 拉取附件字节（禁止内联 base64 直传）。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import unquote, urlparse

import requests

from workflow_payloads import SUPPORTED_ATTACHMENT_SCHEMES, _allowed_schemes

_DEFAULT_TIMEOUT = 30.0


def _fetch_headers(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    headers: dict[str, str] = {}
    auth = os.environ.get("TIA_ATTACHMENT_AUTH", "").strip()
    if auth:
        headers["Authorization"] = auth
    if extra:
        headers.update(dict(extra))
    return headers


def fetch_bytes_from_uri(
    uri: str,
    *,
    timeout: float = _DEFAULT_TIMEOUT,
    headers: Mapping[str, str] | None = None,
) -> bytes:
    """
    通过 HTTP(S) GET 或预签名 URL 下载附件。

    s3:// / minio:// 等 scheme 需由上游提供可 GET 的 signed URL，
    或通过 ``TIA_S3_ENDPOINT`` + boto3 扩展（当前版本仅支持 http/https GET）。
    """
    normalized = uri.strip()
    parsed = urlparse(normalized)
    if not parsed.scheme:
        raise ValueError(f"attachment uri missing scheme: {uri}")
    if parsed.scheme == "file":
        raise ValueError("file:// attachments are not allowed; use object storage URI")

    if parsed.scheme == "local" and os.environ.get("TIA_ALLOW_LOCAL_FILE", "0") == "1":
        local_path = Path(unquote(parsed.path))
        if os.name == "nt" and not local_path.is_file() and len(parsed.path) > 2 and parsed.path[2] == ":":
            local_path = Path(unquote(parsed.path.lstrip("/")))
        if not local_path.is_file():
            raise FileNotFoundError(f"local attachment not found: {local_path}")
        return local_path.read_bytes()

    if parsed.scheme not in _allowed_schemes():
        raise ValueError(f"unsupported attachment uri scheme: {parsed.scheme}")

    if parsed.scheme in {"http", "https"}:
        response = requests.get(
            normalized,
            headers=_fetch_headers(headers),
            timeout=timeout,
        )
        response.raise_for_status()
        return response.content

    raise ValueError(
        f"cannot fetch {parsed.scheme}:// URI directly; "
        "provide an http(s) signed URL or configure a custom fetcher"
    )


def resolve_image_uri_from_frame(frame: dict[str, Any]) -> str | None:
    """从 SensorFrame dict 提取图像 URI（兼容 attachment_ref / image_uri）。"""
    payload = frame.get("payload") or {}
    uri = payload.get("image_uri")
    if isinstance(uri, str) and uri.strip():
        return uri.strip()

    attachment = payload.get("attachment_ref") or {}
    uri = attachment.get("uri")
    if isinstance(uri, str) and uri.strip():
        return uri.strip()

    meta = frame.get("metadata") or {}
    uri = meta.get("attachment_uri")
    if isinstance(uri, str) and uri.strip():
        return uri.strip()
    return None
