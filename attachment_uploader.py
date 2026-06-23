from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional

import requests

from workflow_payloads import build_attachment_ref


AttachmentUploader = Callable[..., None]


def sha256_file(source_path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    path = Path(source_path)
    digest = hashlib.sha256()

    with path.open("rb") as file_handle:
        while True:
            chunk = file_handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)

    return digest.hexdigest()


def guess_mime_type(source_path: str | Path, default: str = "application/octet-stream") -> str:
    mime_type, _ = mimetypes.guess_type(str(source_path))
    return mime_type or default


def infer_attachment_kind(mime_type: str) -> str:
    if mime_type.startswith("image/"):
        return "image"
    if mime_type.startswith("video/"):
        return "video"
    if mime_type.startswith("audio/"):
        return "audio"
    if mime_type in {"application/pdf", "text/plain"}:
        return "document"
    return "file"


def _default_upload_target(object_uri: str, upload_url: str | None) -> str | None:
    if upload_url:
        return upload_url
    if object_uri.startswith(("http://", "https://")):
        return object_uri
    return None


def upload_attachment_file(
    source_path: str | Path,
    object_uri: str,
    *,
    upload_url: str | None = None,
    uploader: AttachmentUploader | None = None,
    upload_headers: Optional[Mapping[str, str]] = None,
    timeout: float = 30.0,
    checksum_algorithm: str = "sha256",
    kind: str | None = None,
    mime_type: str | None = None,
    name: str | None = None,
    attachment_id: str | None = None,
    meta: Mapping[str, Any] | None = None,
    **extra_meta: Any,
) -> Dict[str, Any]:
    path = Path(source_path)
    if not path.exists():
        raise FileNotFoundError(f"attachment source not found: {path}")
    if not path.is_file():
        raise ValueError(f"attachment source must be a regular file: {path}")

    if checksum_algorithm.lower() != "sha256":
        raise ValueError("only sha256 checksum is supported by the attachment upload helper")

    resolved_mime_type = mime_type or guess_mime_type(path)
    resolved_kind = kind or infer_attachment_kind(resolved_mime_type)
    resolved_name = name or path.name
    size_bytes = path.stat().st_size
    checksum_value = sha256_file(path)

    if uploader is not None:
        uploader(
            source_path=path,
            object_uri=object_uri,
            upload_url=upload_url,
            upload_headers=dict(upload_headers or {}),
            timeout=timeout,
            mime_type=resolved_mime_type,
            size_bytes=size_bytes,
            checksum={"algorithm": "sha256", "value": checksum_value},
        )
    else:
        target_url = _default_upload_target(object_uri, upload_url)
        if not target_url:
            raise ValueError(
                "upload_attachment_file requires upload_url or uploader for non-http(s) object URIs"
            )

        headers = dict(upload_headers or {})
        headers.setdefault("Content-Type", resolved_mime_type)
        headers.setdefault("Content-Length", str(size_bytes))

        with path.open("rb") as file_handle:
            response = requests.put(
                target_url,
                data=file_handle,
                headers=headers,
                timeout=timeout,
            )
        response.raise_for_status()

    return build_attachment_ref(
        object_uri,
        checksum={"algorithm": "sha256", "value": checksum_value},
        kind=resolved_kind,
        mime_type=resolved_mime_type,
        size_bytes=size_bytes,
        name=resolved_name,
        attachment_id=attachment_id,
        meta=meta,
        **extra_meta,
    )


def upload_attachment_files(
    items: list[dict[str, Any]],
    *,
    uploader: AttachmentUploader | None = None,
    upload_headers: Optional[Mapping[str, str]] = None,
    timeout: float = 30.0,
) -> list[Dict[str, Any]]:
    attachments: list[Dict[str, Any]] = []
    for item in items:
        attachments.append(
            upload_attachment_file(
                item["source_path"],
                item["object_uri"],
                upload_url=item.get("upload_url"),
                uploader=uploader,
                upload_headers=upload_headers,
                timeout=timeout,
                checksum_algorithm=item.get("checksum_algorithm", "sha256"),
                kind=item.get("kind"),
                mime_type=item.get("mime_type"),
                name=item.get("name"),
                attachment_id=item.get("attachment_id"),
                meta=item.get("meta"),
                **item.get("extra_meta", {}),
            )
        )
    return attachments