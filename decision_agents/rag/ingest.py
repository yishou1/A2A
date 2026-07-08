"""Command line ingestion for local ROE PDF documents."""

from __future__ import annotations

import argparse
import hashlib
import json
import re

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from decision_agents.common.config import get_settings
from decision_agents.rag.documents import RagChunk
from decision_agents.rag.store import (
    ensure_schema,
    get_document_by_source,
    record_ingest_run,
    replace_document_chunks,
    reset_index,
    upsert_document,
)


SECTION_PATTERN = re.compile(
    r"^\s*((?:ROE|RULE|LOW|AUTH|PLAN|SEC)[-_A-Z0-9]*|\d+(?:\.\d+){0,4})"
    r"[\s:：.-]+(.{2,120})$",
    re.IGNORECASE,
)
MAX_CHUNK_CHARS = 1200


@dataclass(frozen=True)
class PdfPage:
    page: int
    text: str


@dataclass(frozen=True)
class PdfExtractionResult:
    pages: list[PdfPage]
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class IngestSummary:
    documents_seen: int
    documents_ingested: int
    documents_skipped: int
    chunks_written: int
    warnings: list[str]
    index_path: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "documents_seen": self.documents_seen,
            "documents_ingested": self.documents_ingested,
            "documents_skipped": self.documents_skipped,
            "chunks_written": self.chunks_written,
            "warnings": self.warnings,
            "index_path": self.index_path,
        }


def ingest_documents(
    *,
    source: str | Path | None = None,
    index_path: str | Path | None = None,
    rebuild: bool = False,
) -> IngestSummary:
    settings = get_settings()
    source_path = Path(source or settings.rag_document_dir)
    index = reset_index(index_path) if rebuild else ensure_schema(index_path)
    warnings: list[str] = []
    documents_ingested = 0
    documents_skipped = 0
    chunks_written = 0

    pdf_files = sorted(source_path.glob("*.pdf")) if source_path.exists() else []
    if not source_path.exists():
        warnings.append(f"rag_document_dir_missing:{source_path}")

    for pdf_path in pdf_files:
        source_name = pdf_path.name
        content_hash = _file_hash(pdf_path)
        existing = get_document_by_source(source_name, index_path=index)
        if existing and existing.get("content_hash") == content_hash and not rebuild:
            documents_skipped += 1
            continue

        doc_id = _doc_id(pdf_path, content_hash)
        extraction = extract_pdf_pages(
            pdf_path,
            enable_ocr=settings.enable_rag_ocr,
            ocr_engine=settings.rag_ocr_engine,
        )
        warnings.extend(extraction.warnings)
        chunks = chunk_pdf_pages(
            doc_id=doc_id,
            source=source_name,
            content_hash=content_hash,
            pages=extraction.pages,
        )
        if not chunks:
            warnings.append(f"pdf_no_chunks:{source_name}")
            documents_skipped += 1
            continue
        upsert_document(
            doc_id=doc_id,
            source=source_name,
            doc_type="roe",
            content_hash=content_hash,
            metadata={"path": str(pdf_path), "schema_version": 1},
            index_path=index,
        )
        chunks_written += replace_document_chunks(doc_id, chunks, index_path=index)
        documents_ingested += 1

    record_ingest_run(
        source_dir=str(source_path),
        rebuild=rebuild,
        documents_seen=len(pdf_files),
        documents_ingested=documents_ingested,
        documents_skipped=documents_skipped,
        chunks_written=chunks_written,
        warnings=warnings,
        index_path=index,
    )
    return IngestSummary(
        documents_seen=len(pdf_files),
        documents_ingested=documents_ingested,
        documents_skipped=documents_skipped,
        chunks_written=chunks_written,
        warnings=warnings,
        index_path=str(index),
    )


def extract_pdf_pages(
    path: str | Path,
    *,
    enable_ocr: bool = False,
    ocr_engine: str = "paddleocr",
) -> PdfExtractionResult:
    pdf_path = Path(path)
    warnings: list[str] = []
    try:
        from pypdf import PdfReader
    except Exception as exc:
        return PdfExtractionResult(
            pages=[],
            warnings=[f"pdf_reader_unavailable:pypdf:{exc}"],
        )

    reader = PdfReader(str(pdf_path))
    pages: list[PdfPage] = []
    for page_index, page in enumerate(reader.pages, start=1):
        text = ""
        try:
            text = page.extract_text() or ""
        except Exception as exc:
            warnings.append(f"pdf_text_extract_failed:{pdf_path.name}:page:{page_index}:{exc}")
        if not text.strip():
            if enable_ocr:
                ocr_text, ocr_warnings = _extract_page_with_ocr(
                    pdf_path,
                    page_index,
                    ocr_engine=ocr_engine,
                )
                text = ocr_text
                warnings.extend(ocr_warnings)
            else:
                warnings.append(f"ocr_disabled:{pdf_path.name}:page:{page_index}")
        pages.append(PdfPage(page=page_index, text=text.strip()))
    return PdfExtractionResult(pages=pages, warnings=warnings)


def chunk_pdf_pages(
    *,
    doc_id: str,
    source: str,
    content_hash: str,
    pages: list[PdfPage],
) -> list[RagChunk]:
    chunks: list[RagChunk] = []
    for page in pages:
        if not page.text.strip():
            continue
        sections = _page_sections(page)
        for section_index, section in enumerate(sections, start=1):
            for part_index, text in enumerate(_split_text(section["text"]), start=1):
                rule_id = section["rule_id"] or f"ROE-P{page.page}-S{section_index}"
                chunk_id = f"{doc_id}:p{page.page}:s{section_index}:c{part_index}"
                title = section["title"] or rule_id
                citation = _citation(source, page.page, section["section"])
                chunks.append(
                    RagChunk(
                        source=source,
                        rule_id=rule_id,
                        title=title,
                        text=text,
                        tags=("roe", "pdf", "authorization", "rules"),
                        doc_id=doc_id,
                        doc_type="roe",
                        page_start=page.page,
                        page_end=page.page,
                        section=section["section"],
                        chunk_id=chunk_id,
                        citation=citation,
                        content_hash=_chunk_hash(content_hash, page.page, text),
                    )
                )
    return chunks


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest local ROE PDF documents for RAG.")
    parser.add_argument("--source", default=None, help="Directory containing ROE PDF files.")
    parser.add_argument("--index-path", default=None, help="SQLite index path.")
    parser.add_argument("--rebuild", action="store_true", help="Rebuild the SQLite index.")
    args = parser.parse_args()

    summary = ingest_documents(
        source=args.source,
        index_path=args.index_path,
        rebuild=args.rebuild,
    )
    print(json.dumps(summary.as_dict(), ensure_ascii=False, indent=2))


def _extract_page_with_ocr(
    pdf_path: Path,
    page_index: int,
    *,
    ocr_engine: str,
) -> tuple[str, list[str]]:
    if ocr_engine.lower() != "paddleocr":
        return "", [f"ocr_engine_unsupported:{ocr_engine}:{pdf_path.name}:page:{page_index}"]
    try:
        import paddleocr  # noqa: F401
    except Exception as exc:
        return "", [f"ocr_unavailable:paddleocr:{pdf_path.name}:page:{page_index}:{exc}"]
    return "", [f"ocr_pdf_rasterization_not_configured:{pdf_path.name}:page:{page_index}"]


def _page_sections(page: PdfPage) -> list[dict[str, str]]:
    sections: list[dict[str, str]] = []
    current = {
        "rule_id": f"ROE-P{page.page}",
        "title": f"Page {page.page}",
        "section": f"Page {page.page}",
        "lines": [],
    }
    for raw_line in page.text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = SECTION_PATTERN.match(line)
        if match and current["lines"]:
            sections.append(_finalize_section(current))
            current = _section_from_heading(page.page, match)
            continue
        if match and not current["lines"]:
            current = _section_from_heading(page.page, match)
            continue
        current["lines"].append(line)
    if current["lines"]:
        sections.append(_finalize_section(current))
    return sections or [
        {
            "rule_id": f"ROE-P{page.page}",
            "title": f"Page {page.page}",
            "section": f"Page {page.page}",
            "text": page.text.strip(),
        }
    ]


def _section_from_heading(page: int, match: re.Match[str]) -> dict[str, Any]:
    rule_id = match.group(1).strip().upper()
    title = match.group(2).strip()
    section = f"{rule_id}: {title}"
    return {"rule_id": rule_id, "title": title, "section": section, "lines": []}


def _finalize_section(section: dict[str, Any]) -> dict[str, str]:
    return {
        "rule_id": str(section["rule_id"]),
        "title": str(section["title"]),
        "section": str(section["section"]),
        "text": " ".join(str(line) for line in section["lines"]).strip(),
    }


def _split_text(text: str) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n{2,}|(?<=。)|(?<=\.)", text)]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if not paragraph:
            continue
        if len(current) + len(paragraph) + 1 <= MAX_CHUNK_CHARS:
            current = f"{current} {paragraph}".strip()
            continue
        if current:
            chunks.append(current)
        current = paragraph
    if current:
        chunks.append(current)
    return chunks or [text[:MAX_CHUNK_CHARS]]


def _citation(source: str, page: int, section: str) -> str:
    return f"{source} p.{page} {section}".strip()


def _doc_id(path: Path, content_hash: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_-]+", "-", path.stem).strip("-").lower()
    return f"{normalized or 'roe-doc'}-{content_hash[:12]}"


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _chunk_hash(content_hash: str, page: int, text: str) -> str:
    return hashlib.sha256(f"{content_hash}:{page}:{text}".encode("utf-8")).hexdigest()


if __name__ == "__main__":
    main()
