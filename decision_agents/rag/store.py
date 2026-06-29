"""SQLite storage for local ROE RAG document chunks."""

from __future__ import annotations

import json
import sqlite3

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from decision_agents.config import get_settings
from decision_agents.rag.documents import RagChunk


SCHEMA_VERSION = 1


def default_index_path() -> Path:
    return Path(get_settings().rag_index_path)


def ensure_schema(index_path: str | Path | None = None) -> Path:
    path = Path(index_path) if index_path else default_index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS documents (
                doc_id TEXT PRIMARY KEY,
                source TEXT NOT NULL UNIQUE,
                doc_type TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS chunks (
                chunk_id TEXT PRIMARY KEY,
                doc_id TEXT NOT NULL,
                source TEXT NOT NULL,
                doc_type TEXT NOT NULL,
                page_start INTEGER,
                page_end INTEGER,
                section TEXT,
                rule_id TEXT NOT NULL,
                title TEXT NOT NULL,
                text TEXT NOT NULL,
                tags_json TEXT NOT NULL DEFAULT '[]',
                citation TEXT,
                content_hash TEXT NOT NULL,
                FOREIGN KEY(doc_id) REFERENCES documents(doc_id)
            );

            CREATE TABLE IF NOT EXISTS ingest_runs (
                run_id TEXT PRIMARY KEY,
                source_dir TEXT NOT NULL,
                rebuild INTEGER NOT NULL,
                documents_seen INTEGER NOT NULL,
                documents_ingested INTEGER NOT NULL,
                documents_skipped INTEGER NOT NULL,
                chunks_written INTEGER NOT NULL,
                warnings_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_chunks_doc_type ON chunks(doc_type);
            CREATE INDEX IF NOT EXISTS idx_chunks_rule_id ON chunks(rule_id);
            CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source);
            """
        )
    return path


def reset_index(index_path: str | Path | None = None) -> Path:
    path = Path(index_path) if index_path else default_index_path()
    if path.exists():
        path.unlink()
    return ensure_schema(path)


def get_document_by_source(
    source: str,
    *,
    index_path: str | Path | None = None,
) -> dict[str, Any] | None:
    path = ensure_schema(index_path)
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT * FROM documents WHERE source = ?",
            (source,),
        ).fetchone()
    return dict(row) if row else None


def upsert_document(
    *,
    doc_id: str,
    source: str,
    doc_type: str,
    content_hash: str,
    metadata: dict[str, Any] | None = None,
    index_path: str | Path | None = None,
) -> None:
    path = ensure_schema(index_path)
    now = _utc_now()
    metadata_json = json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True)
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            INSERT INTO documents (
                doc_id, source, doc_type, content_hash, created_at, updated_at, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(doc_id) DO UPDATE SET
                source = excluded.source,
                doc_type = excluded.doc_type,
                content_hash = excluded.content_hash,
                updated_at = excluded.updated_at,
                metadata_json = excluded.metadata_json
            """,
            (doc_id, source, doc_type, content_hash, now, now, metadata_json),
        )


def replace_document_chunks(
    doc_id: str,
    chunks: list[RagChunk],
    *,
    index_path: str | Path | None = None,
) -> int:
    path = ensure_schema(index_path)
    with sqlite3.connect(path) as connection:
        connection.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
        connection.executemany(
            """
            INSERT INTO chunks (
                chunk_id, doc_id, source, doc_type, page_start, page_end, section,
                rule_id, title, text, tags_json, citation, content_hash
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    chunk.chunk_id or f"{doc_id}:{index}",
                    chunk.doc_id or doc_id,
                    chunk.source,
                    chunk.doc_type,
                    chunk.page_start,
                    chunk.page_end,
                    chunk.section,
                    chunk.rule_id,
                    chunk.title,
                    chunk.text,
                    json.dumps(list(chunk.tags), ensure_ascii=False),
                    chunk.citation,
                    chunk.content_hash or "",
                )
                for index, chunk in enumerate(chunks, start=1)
            ],
        )
    return len(chunks)


def record_ingest_run(
    *,
    source_dir: str,
    rebuild: bool,
    documents_seen: int,
    documents_ingested: int,
    documents_skipped: int,
    chunks_written: int,
    warnings: list[str],
    index_path: str | Path | None = None,
) -> None:
    path = ensure_schema(index_path)
    run_id = f"run-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            INSERT INTO ingest_runs (
                run_id, source_dir, rebuild, documents_seen, documents_ingested,
                documents_skipped, chunks_written, warnings_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                source_dir,
                int(rebuild),
                documents_seen,
                documents_ingested,
                documents_skipped,
                chunks_written,
                json.dumps(warnings, ensure_ascii=False),
                _utc_now(),
            ),
        )


def load_index_chunks(
    *,
    index_path: str | Path | None = None,
    document_scope: str | list[str] | tuple[str, ...] | set[str] | None = None,
) -> list[RagChunk]:
    path = Path(index_path) if index_path else default_index_path()
    if not path.exists():
        return []
    ensure_schema(path)
    query = "SELECT * FROM chunks"
    params: list[Any] = []
    scopes = _normalize_scope(document_scope)
    if scopes:
        placeholders = ",".join("?" for _ in scopes)
        query += f" WHERE lower(doc_type) IN ({placeholders})"
        params.extend(sorted(scopes))
    query += " ORDER BY source, page_start, chunk_id"
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(query, params).fetchall()

    chunks = []
    for row in rows:
        tags = tuple(json.loads(row["tags_json"] or "[]"))
        chunks.append(
            RagChunk(
                source=row["source"],
                rule_id=row["rule_id"],
                title=row["title"],
                text=row["text"],
                tags=tags,
                doc_id=row["doc_id"],
                doc_type=row["doc_type"],
                page_start=row["page_start"],
                page_end=row["page_end"],
                section=row["section"],
                chunk_id=row["chunk_id"],
                citation=row["citation"],
                content_hash=row["content_hash"],
            )
        )
    return chunks


def _normalize_scope(
    document_scope: str | list[str] | tuple[str, ...] | set[str] | None,
) -> set[str]:
    if not document_scope:
        return set()
    if isinstance(document_scope, str):
        return {document_scope.lower()}
    return {str(item).lower() for item in document_scope}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
