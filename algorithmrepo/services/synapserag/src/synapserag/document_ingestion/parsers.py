"""Small, explicit parsers for the first document-ingestion MVP.

PDF and DOCX dependencies are optional so the existing text-only runtime keeps
working.  Installing ``pymupdf`` and ``python-docx`` enables those parsers.
"""

import hashlib
import mimetypes
import re
from pathlib import Path
from typing import List, Optional

from .models import DocumentBlock, ParsedDocument


class DocumentParserError(ValueError):
    def __init__(self, message: str, code: str = "document_parse_error"):
        super().__init__(message)
        self.code = code


SUPPORTED_EXTENSIONS = {".txt", ".text", ".md", ".markdown", ".pdf", ".docx"}


def _clean_text(text: str) -> str:
    text = text.replace("\x00", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _decode_text(data: bytes) -> str:
    # Prefer the encodings used by the supported Chinese-document workflow.
    # charset-normalizer can otherwise label short GB18030 input as a
    # single-byte encoding with mojibake.
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    try:
        from charset_normalizer import from_bytes

        match = from_bytes(data).best()
        if match is not None:
            return str(match)
    except Exception:
        pass
    raise DocumentParserError(
        "Unable to decode text file as UTF-8 or GB18030",
        code="text_decode_error",
    )


def _markdown_blocks(text: str) -> List[DocumentBlock]:
    heading_path: List[str] = []
    blocks: List[DocumentBlock] = []
    current: List[str] = []
    order = 0

    def flush() -> None:
        nonlocal order
        value = _clean_text("\n".join(current))
        if value:
            blocks.append(DocumentBlock(
                text=value,
                kind="markdown_block",
                heading_path=list(heading_path),
                order=order,
            ))
            order += 1
        current.clear()

    for line in text.splitlines():
        match = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if match:
            flush()
            level = len(match.group(1))
            heading = match.group(2).strip()
            heading_path[:] = heading_path[: level - 1]
            heading_path.append(heading)
            blocks.append(DocumentBlock(
                text=heading,
                kind="heading",
                heading_path=list(heading_path),
                order=order,
            ))
            order += 1
        elif line.strip():
            current.append(line)
        else:
            flush()
    flush()
    return blocks


def _plain_blocks(text: str) -> List[DocumentBlock]:
    return [
        DocumentBlock(text=part.strip(), order=index)
        for index, part in enumerate(re.split(r"\n\s*\n", text))
        if part.strip()
    ]


def _parse_pdf(path: Path) -> tuple[str, List[DocumentBlock], dict]:
    try:
        import pymupdf
    except ImportError as error:
        raise DocumentParserError(
            "PDF parsing requires pymupdf; install the document extras",
            code="missing_pdf_dependency",
        ) from error

    blocks: List[DocumentBlock] = []
    pages: List[str] = []
    with pymupdf.open(path) as document:
        for page_number, page in enumerate(document, start=1):
            page_text = _clean_text(page.get_text("text"))
            pages.append(page_text)
            if page_text:
                blocks.append(DocumentBlock(
                    text=page_text,
                    kind="page",
                    page_start=page_number,
                    page_end=page_number,
                    order=page_number - 1,
                ))
    text = "\n\n".join(page for page in pages if page)
    # A short text PDF is still a valid text PDF.  Only classify the document
    # as OCR-required when no extractable text exists at all.
    if not re.sub(r"\s+", "", text):
        raise DocumentParserError(
            "PDF contains too little extractable text; it may be scanned and needs OCR",
            code="scanned_pdf_requires_ocr",
        )
    return text, blocks, {"page_count": len(pages)}


def _parse_docx(path: Path) -> tuple[str, List[DocumentBlock], dict]:
    try:
        from docx import Document
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except ImportError as error:
        raise DocumentParserError(
            "DOCX parsing requires python-docx; install the document extras",
            code="missing_docx_dependency",
        ) from error

    document = Document(path)
    blocks: List[DocumentBlock] = []
    heading_path: List[str] = []
    order = 0
    body = document.element.body
    for child in body.iterchildren():
        if child.tag.endswith("}p"):
            paragraph = Paragraph(child, document)
            value = _clean_text(paragraph.text)
            if not value:
                continue
            style = paragraph.style.name if paragraph.style else ""
            if style.lower().startswith("heading"):
                try:
                    level = int(style.split()[-1])
                except (ValueError, IndexError):
                    level = 1
                heading_path[:] = heading_path[: max(0, level - 1)]
                heading_path.append(value)
                kind = "heading"
            else:
                kind = "paragraph"
            blocks.append(DocumentBlock(value, kind, heading_path=list(heading_path), order=order))
            order += 1
        elif child.tag.endswith("}tbl"):
            table = Table(child, document)
            rows = [" | ".join(cell.text.strip() for cell in row.cells) for row in table.rows]
            value = _clean_text("\n".join(row for row in rows if row.strip()))
            if value:
                blocks.append(DocumentBlock(value, "table", heading_path=list(heading_path), order=order))
                order += 1
    text = "\n\n".join(block.text for block in blocks)
    return text, blocks, {"paragraph_or_table_count": len(blocks)}


def parse_document(path: str | Path, document_id: Optional[str] = None) -> ParsedDocument:
    source = Path(path).resolve()
    if not source.is_file():
        raise DocumentParserError(f"Document does not exist: {source}", "file_not_found")
    suffix = source.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise DocumentParserError(
            f"Unsupported document extension {suffix!r}; supported: {sorted(SUPPORTED_EXTENSIONS)}",
            "unsupported_extension",
        )
    data = source.read_bytes()
    content_sha256 = hashlib.sha256(data).hexdigest()
    document_id = document_id or f"doc-{content_sha256[:16]}"
    mime_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"

    if suffix in {".txt", ".text", ".md", ".markdown"}:
        text = _clean_text(_decode_text(data))
        blocks = _markdown_blocks(text) if suffix in {".md", ".markdown"} else _plain_blocks(text)
        parser = "markdown" if suffix in {".md", ".markdown"} else "plain_text"
        metadata = {}
    elif suffix == ".pdf":
        text, blocks, metadata = _parse_pdf(source)
        parser = "pymupdf"
    else:
        text, blocks, metadata = _parse_docx(source)
        parser = "python-docx"

    if not text.strip():
        raise DocumentParserError("Document contains no extractable text", "empty_document")
    return ParsedDocument(
        document_id=document_id,
        filename=source.name,
        mime_type=mime_type,
        source_path=str(source),
        text=text,
        blocks=blocks,
        content_sha256=content_sha256,
        parser=parser,
        metadata=metadata,
    )
