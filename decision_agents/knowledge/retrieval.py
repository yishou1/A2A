"""Compatibility helpers for local RAG evidence retrieval."""

from __future__ import annotations

from typing import Iterable

from decision_agents.rag.documents import (
    DEFAULT_KNOWLEDGE_FILES,
    RagChunk as KnowledgeChunk,
    load_rag_chunks as load_knowledge_chunks,
    parse_markdown_chunks as _parse_markdown_chunks,
    tokenize as _tokens,
)
from decision_agents.rag.pipeline import RagResult, run_rag
from decision_agents.schemas import RuleEvidence


def retrieve_evidence(
    query: str,
    *,
    top_k: int = 5,
    files: tuple[str, ...] = DEFAULT_KNOWLEDGE_FILES,
    document_scope: str | Iterable[str] | None = None,
    require_citations: bool = True,
) -> list[RuleEvidence]:
    """Return the highest-scoring local RAG evidence chunks for a query."""
    return retrieve_rag_result(
        query,
        top_k=top_k,
        files=files,
        document_scope=document_scope,
        require_citations=require_citations,
    ).evidence


def retrieve_rag_result(
    query: str,
    *,
    purpose: str = "general",
    top_k: int = 5,
    files: tuple[str, ...] = DEFAULT_KNOWLEDGE_FILES,
    document_scope: str | Iterable[str] | None = None,
    require_citations: bool = True,
) -> RagResult:
    """Return evidence plus RAG answer/profile metadata."""
    return run_rag(
        query,
        purpose=purpose,
        top_k=top_k,
        files=files,
        document_scope=document_scope,
        require_citations=require_citations,
    )
