from __future__ import annotations

from copy import deepcopy
import os
from typing import Any, Dict, Iterable, Mapping, Sequence
from urllib.parse import urlparse


CORE_ATTACHMENT_FIELDS = {
    "id",
    "attachment_id",
    "kind",
    "uri",
    "mime_type",
    "checksum",
    "sha256",
    "size_bytes",
    "name",
    "meta",
    "width",
    "height",
    "duration_ms",
    "duration_s",
    "fps",
    "frame_range",
    "page_count",
    "page_range",
}

INLINE_ATTACHMENT_FIELDS = {"data", "base64", "bytes", "content", "buffer", "raw", "payload"}
SUPPORTED_ATTACHMENT_SCHEMES = {"s3", "gs", "oss", "minio", "cos", "azblob", "http", "https"}


def _ensure_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{label} must be a mapping")
    return value


def _ensure_checksum(checksum: Any, sha256: Any = None) -> Dict[str, str]:
    if checksum is None and sha256 is not None:
        checksum = {"algorithm": "sha256", "value": sha256}

    if checksum is None:
        raise ValueError("attachment requires checksum information")

    if isinstance(checksum, str):
        checksum = {"algorithm": "sha256", "value": checksum}

    checksum_mapping = _ensure_mapping(checksum, "checksum")
    algorithm = str(checksum_mapping.get("algorithm", "sha256")).strip()
    value = str(checksum_mapping.get("value", "")).strip()

    if not algorithm:
        raise ValueError("attachment checksum algorithm must not be empty")
    if not value:
        raise ValueError("attachment checksum value must not be empty")

    return {"algorithm": algorithm, "value": value}


def _allowed_schemes() -> set[str]:
    schemes = set(SUPPORTED_ATTACHMENT_SCHEMES)
    if os.environ.get("TIA_ALLOW_LOCAL_FILE", "0") == "1":
        schemes.add("local")
    return schemes


def _ensure_object_storage_uri(uri: Any) -> str:
    if not isinstance(uri, str) or not uri.strip():
        raise ValueError("attachment requires a non-empty uri")

    normalized_uri = uri.strip()
    parsed = urlparse(normalized_uri)
    if not parsed.scheme:
        raise ValueError("attachment uri must be an object storage URI or signed URL")
    if parsed.scheme == "file":
        raise ValueError("attachment uri must not use the file:// scheme")
    if parsed.scheme not in _allowed_schemes():
        raise ValueError(
            f"unsupported attachment uri scheme: {parsed.scheme}. "
            f"Expected one of: {', '.join(sorted(_allowed_schemes()))}"
        )
    if parsed.scheme in {"http", "https"} and not parsed.netloc:
        raise ValueError("http(s) attachment uri must include a network location")
    return normalized_uri


def _merge_meta(attachment: Mapping[str, Any]) -> Dict[str, Any]:
    meta = dict(attachment.get("meta", {}) or {})
    for key, value in attachment.items():
        if key in CORE_ATTACHMENT_FIELDS or key in INLINE_ATTACHMENT_FIELDS:
            continue
        meta[key] = value
    return meta


def build_attachment_ref(
    uri: str,
    *,
    checksum: Any = None,
    sha256: Any = None,
    kind: str = "other",
    mime_type: str | None = None,
    size_bytes: int | None = None,
    name: str | None = None,
    attachment_id: str | None = None,
    meta: Mapping[str, Any] | None = None,
    **extra_meta: Any,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "uri": _ensure_object_storage_uri(uri),
        "kind": kind,
        "checksum": _ensure_checksum(checksum, sha256),
    }

    if attachment_id is not None:
        payload["id"] = str(attachment_id)
    if mime_type is not None:
        payload["mime_type"] = mime_type
    if size_bytes is not None:
        payload["size_bytes"] = int(size_bytes)
    if name is not None:
        payload["name"] = name

    merged_meta = dict(meta or {})
    merged_meta.update(extra_meta)
    if merged_meta:
        payload["meta"] = merged_meta

    return normalize_attachment_ref(payload)


def normalize_attachment_ref(attachment: Any) -> Dict[str, Any]:
    attachment_mapping = dict(_ensure_mapping(attachment, "attachment"))

    inline_fields = [field for field in INLINE_ATTACHMENT_FIELDS if field in attachment_mapping and attachment_mapping[field] not in (None, "", [], {}, b"")]
    if inline_fields:
        raise ValueError(
            "attachments must reference object storage only; inline payload fields are not allowed: "
            + ", ".join(sorted(inline_fields))
        )

    uri = _ensure_object_storage_uri(attachment_mapping.get("uri"))
    checksum = _ensure_checksum(attachment_mapping.get("checksum"), attachment_mapping.get("sha256"))

    normalized: Dict[str, Any] = {
        "uri": uri,
        "kind": str(attachment_mapping.get("kind", "other")),
        "checksum": checksum,
    }

    attachment_id = attachment_mapping.get("id", attachment_mapping.get("attachment_id"))
    if attachment_id is not None:
        normalized["id"] = str(attachment_id)

    mime_type = attachment_mapping.get("mime_type")
    if mime_type is not None:
        normalized["mime_type"] = str(mime_type)

    size_bytes = attachment_mapping.get("size_bytes")
    if size_bytes is not None:
        normalized["size_bytes"] = int(size_bytes)

    name = attachment_mapping.get("name")
    if name is not None:
        normalized["name"] = str(name)

    meta = _merge_meta(attachment_mapping)
    if meta:
        normalized["meta"] = meta

    return normalized


def normalize_attachments(attachments: Iterable[Any] | None) -> list[Dict[str, Any]]:
    if not attachments:
        return []
    return [normalize_attachment_ref(item) for item in attachments]


def merge_attachments(existing: Sequence[Any] | None, incoming: Sequence[Any] | None) -> list[Dict[str, Any]]:
    merged: list[Dict[str, Any]] = []
    seen_keys = set()

    for item in list(existing or []) + list(incoming or []):
        normalized = normalize_attachment_ref(item)
        dedupe_key = normalized.get("id") or normalized["uri"]
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)
        merged.append(normalized)

    return merged


def attachment_snapshot(attachments: Iterable[Any] | None) -> list[Dict[str, Any]]:
    return [deepcopy(item) for item in normalize_attachments(attachments)]