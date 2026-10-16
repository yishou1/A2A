"""Resolve retrieved chunks back to safe, highlightable source previews."""

from __future__ import annotations

import hashlib
import html
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


class SourcePreviewError(ValueError):
    def __init__(self, code: str, message: str, status_code: int = 404) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def find_chunk(source_map: Dict[str, List[dict]], chunk_id: str) -> Tuple[str, dict]:
    for text, origins in source_map.items():
        for origin in origins:
            if str(origin.get("chunk_id")) == chunk_id:
                return text, dict(origin)
    raise SourcePreviewError("EVIDENCE_NOT_FOUND", f"evidence chunk not found: {chunk_id}")


def find_document(manifest: dict, document_id: str) -> dict:
    for item in manifest.get("documents", []):
        if str(item.get("document_id")) == document_id:
            document = dict(item)
            path = Path(str(document.get("upload_path") or "")).resolve()
            if not path.is_file():
                raise SourcePreviewError(
                    "SOURCE_FILE_NOT_FOUND",
                    f"source file is unavailable for document: {document_id}",
                )
            document["resolved_path"] = str(path)
            return document
    raise SourcePreviewError("DOCUMENT_NOT_FOUND", f"document not found: {document_id}")


def _source_quote(chunk_text: str) -> str:
    # Chunking adds a synthetic title line for retrieval context. It is not
    # necessarily present verbatim in the original document.
    return re.sub(r"^标题：[^\n]*\n", "", chunk_text, count=1).strip()


def _normalized_span(text: str, quote: str) -> Tuple[int, int]:
    direct = text.find(quote)
    if direct >= 0:
        return direct, direct + len(quote)

    def compact(value: str) -> Tuple[str, List[int]]:
        chars = []
        indexes = []
        for index, char in enumerate(value):
            if not char.isspace():
                chars.append(char)
                indexes.append(index)
        return "".join(chars), indexes

    compact_text, indexes = compact(text)
    compact_quote, _ = compact(quote)
    position = compact_text.find(compact_quote)
    if position >= 0 and compact_quote:
        return indexes[position], indexes[position + len(compact_quote) - 1] + 1

    anchor = next((part.strip() for part in re.split(r"[。！？!?\n]", quote) if len(part.strip()) >= 12), "")
    position = text.find(anchor) if anchor else -1
    if position >= 0:
        return position, position + len(anchor)
    return -1, -1


def _pdf_fragments(quote: str) -> Iterable[str]:
    candidates = [quote]
    candidates.extend(part.strip() for part in quote.splitlines())
    candidates.extend(part.strip() for part in re.split(r"(?<=[。！？.!?；;])", quote))
    seen = set()
    for candidate in candidates:
        candidate = re.sub(r"\s+", " ", candidate).strip()
        if len(candidate) < 8:
            continue
        if len(candidate) > 180:
            candidate = candidate[:180].rstrip()
        if candidate not in seen:
            seen.add(candidate)
            yield candidate


def _pdf_locations(path: Path, quote: str, page_start: Any, page_end: Any) -> List[dict]:
    try:
        import pymupdf
    except ImportError as error:
        raise SourcePreviewError(
            "PDF_PREVIEW_UNAVAILABLE", "PDF highlighting requires pymupdf", 503
        ) from error

    locations = []
    with pymupdf.open(path) as document:
        first = max(1, int(page_start or 1))
        last = min(len(document), int(page_end or first))
        for page_number in range(first, last + 1):
            page = document[page_number - 1]
            rects = []
            seen = set()
            for fragment in _pdf_fragments(quote):
                for rectangle in page.search_for(fragment):
                    key = tuple(round(float(value), 2) for value in rectangle)
                    if key in seen:
                        continue
                    seen.add(key)
                    rects.append({
                        "x0": key[0], "y0": key[1], "x1": key[2], "y1": key[3]
                    })
                if rects and fragment == quote:
                    break
                if len(rects) >= 50:
                    break
            if rects:
                locations.append({"page": page_number, "rects": rects[:50]})
    return locations


def locate_chunk(source_map: Dict[str, List[dict]], manifest: dict, chunk_id: str) -> dict:
    chunk_text, origin = find_chunk(source_map, chunk_id)
    document_id = str(origin.get("document_id") or "")
    document = find_document(manifest, document_id)
    path = Path(document["resolved_path"])
    quote = _source_quote(chunk_text)
    suffix = path.suffix.lower()
    response = {
        "schema_version": "1.0",
        "chunk_id": chunk_id,
        "document_id": document_id,
        "filename": str(document.get("filename") or path.name),
        "content_sha256": document.get("content_sha256"),
        "viewer_type": "pdf" if suffix == ".pdf" else "html",
        "page_start": origin.get("page_start"),
        "page_end": origin.get("page_end"),
        "heading_path": origin.get("heading_path") or [],
        "quote": quote,
        "locations": [],
        "text_span": None,
        "precision": "page",
        "warnings": [],
    }
    if suffix == ".pdf":
        response["locations"] = _pdf_locations(
            path, quote, origin.get("page_start"), origin.get("page_end")
        )
        if response["locations"]:
            response["precision"] = "rectangle"
        else:
            response["warnings"].append("exact_text_rectangle_not_found")
        return response

    parsed_path = Path(str(document.get("artifact_dir") or "")) / "parsed_document.json"
    parsed = json.loads(parsed_path.read_text(encoding="utf-8")) if parsed_path.is_file() else {}
    source_text = str(parsed.get("text") or path.read_text(encoding="utf-8", errors="replace"))
    segment = next(iter(origin.get("source_segments") or []), {})
    start = segment.get("source_start")
    end = segment.get("source_end")
    if not isinstance(start, int) or not isinstance(end, int) or not (0 <= start < end <= len(source_text)):
        start, end = _normalized_span(source_text, quote)
    response["text_span"] = {"start": start, "end": end} if start >= 0 else None
    response["precision"] = "character" if start >= 0 else "document"
    if start < 0:
        response["warnings"].append("exact_text_span_not_found")
    return response


def highlighted_preview(
    source_map: Dict[str, List[dict]], manifest: dict, chunk_id: str
) -> Tuple[bytes, str, str]:
    location = locate_chunk(source_map, manifest, chunk_id)
    document = find_document(manifest, location["document_id"])
    path = Path(document["resolved_path"])
    if location["viewer_type"] == "pdf":
        import pymupdf

        with pymupdf.open(path) as pdf:
            for item in location["locations"]:
                page = pdf[int(item["page"]) - 1]
                for rectangle in item["rects"]:
                    annotation = page.add_highlight_annot(pymupdf.Rect(
                        rectangle["x0"], rectangle["y0"], rectangle["x1"], rectangle["y1"]
                    ))
                    annotation.set_colors(stroke=(1.0, 0.82, 0.2))
                    annotation.update()
            payload = pdf.tobytes(garbage=3, deflate=True)
        return payload, "application/pdf", f"highlighted-{location['filename']}"

    parsed_path = Path(str(document.get("artifact_dir") or "")) / "parsed_document.json"
    parsed = json.loads(parsed_path.read_text(encoding="utf-8")) if parsed_path.is_file() else {}
    source_text = str(parsed.get("text") or path.read_text(encoding="utf-8", errors="replace"))
    span = location.get("text_span")
    if span:
        start, end = int(span["start"]), int(span["end"])
        body = html.escape(source_text[:start]) + "<mark>" + html.escape(source_text[start:end]) + "</mark>" + html.escape(source_text[end:])
    else:
        body = html.escape(source_text)
    checksum = hashlib.sha256(source_text.encode("utf-8")).hexdigest()[:12]
    page = f"""<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">
<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>{html.escape(location['filename'])}</title>
<style>body{{margin:0;background:#f4f6f7;color:#20282d;font:15px/1.8 system-ui,sans-serif}}main{{max-width:900px;margin:auto;padding:32px 40px;white-space:pre-wrap;background:white;min-height:100vh;box-sizing:border-box}}mark{{background:#ffe07a;color:inherit;padding:1px 0;box-shadow:0 0 0 2px #ffe07a}}</style></head>
<body><main data-source-hash=\"{checksum}\">{body}</main></body></html>"""
    return page.encode("utf-8"), "text/html; charset=utf-8", f"highlighted-{location['filename']}.html"
