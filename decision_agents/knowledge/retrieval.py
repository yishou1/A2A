"""Shared routing for local, SynapseRAG, and disabled evidence retrieval."""

from __future__ import annotations

from time import perf_counter
from typing import Iterable

from decision_agents.rag.documents import (
    DEFAULT_KNOWLEDGE_FILES,
    RagChunk as KnowledgeChunk,
    load_rag_chunks as load_knowledge_chunks,
    parse_markdown_chunks as _parse_markdown_chunks,
    tokenize as _tokens,
)
from decision_agents.rag.pipeline import RagResult, run_rag
from decision_agents.common.config import get_settings
from decision_agents.common.schemas import RuleEvidence
from decision_agents.knowledge.synapserag_client import (
    EvidenceQuery,
    SynapseRagClient,
    failed_rag_result,
)


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
    return retrieve_rule_rag_result(
        [EvidenceQuery(query_id="RAG-GENERAL", text=query)],
        purpose=purpose,
        top_k=top_k,
        files=files,
        document_scope=document_scope,
        require_citations=require_citations,
    )


def retrieve_rule_rag_result(
    queries: list[EvidenceQuery],
    *,
    purpose: str,
    top_k: int | None = None,
    request_id: str = "rag-request",
    files: tuple[str, ...] = DEFAULT_KNOWLEDGE_FILES,
    document_scope: str | Iterable[str] | None = None,
    require_citations: bool = True,
) -> RagResult:
    """Retrieve evidence for one or more structured rule IDs."""
    settings = get_settings()
    backend = settings.rag_backend
    if not queries:
        return failed_rag_result(
            backend=backend,
            warning="rag_no_queries",
            rewritten_query="",
        )
    if backend == "synapserag":
        final_top_k = top_k or (
            settings.synapserag_top_k_planning
            if purpose == "planning"
            else settings.synapserag_top_k_compliance
        )
        return SynapseRagClient(settings).retrieve(
            queries,
            request_id=request_id,
            purpose=purpose,
            top_k=final_top_k,
        )
    if backend == "disabled":
        return failed_rag_result(
            backend="disabled",
            warning="rag_disabled",
            rewritten_query="\n".join(item.text for item in queries),
        )
    if backend != "local":
        return failed_rag_result(
            backend=backend,
            warning=f"RAG_RETRIEVAL_ERROR:unsupported_backend:{backend}",
            rewritten_query="\n".join(item.text for item in queries),
        )

    started = perf_counter()
    evidence: list[RuleEvidence] = []
    warnings: list[str] = []
    answers: list[str] = []
    profile: dict = {"enabled": True, "backend": "local", "status": "ok"}
    keywords: list[str] = []
    final_top_k = top_k or settings.rag_top_k_final
    for item in queries:
        result = run_rag(
            item.text,
            purpose=purpose,
            top_k=final_top_k,
            files=files,
            document_scope=document_scope,
            require_citations=require_citations,
        )
        evidence.extend(
            evidence_item.model_copy(update={"rule_id": item.query_id})
            for evidence_item in result.evidence
        )
        warnings.extend(result.warnings)
        if result.answer:
            answers.append(result.answer)
        profile.update(result.model_profile)
        keywords.extend(result.keywords)
    profile.update({"backend": "local", "status": "ok"})
    return RagResult(
        evidence=_deduplicate_evidence(evidence),
        answer=" ".join(dict.fromkeys(answers)),
        model_profile=profile,
        warnings=list(dict.fromkeys(warnings)),
        rewritten_query="\n".join(item.text for item in queries),
        keywords=list(dict.fromkeys(keywords)),
        duration_ms=round((perf_counter() - started) * 1000.0, 3),
    )


def _deduplicate_evidence(evidence: list[RuleEvidence]) -> list[RuleEvidence]:
    unique: list[RuleEvidence] = []
    seen: set[tuple[str, str, str]] = set()
    for item in evidence:
        key = (item.rule_id, item.chunk_id or "", item.content_hash or item.text)
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique
