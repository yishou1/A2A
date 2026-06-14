"""End-to-end local RAG pipeline with model fallback."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from decision_agents.config import get_settings
from decision_agents.rag.documents import (
    DEFAULT_KNOWLEDGE_FILES,
    RagChunk,
    load_rag_chunks,
    tokenize,
)
from decision_agents.rag.index import merge_rankings, rank_keyword, rank_vector
from decision_agents.rag.models import LocalRagModels, RagModelUnavailable
from decision_agents.schemas import RuleEvidence


@dataclass(frozen=True)
class RagResult:
    evidence: list[RuleEvidence]
    answer: str
    model_profile: dict[str, Any]
    warnings: list[str]
    rewritten_query: str
    keywords: list[str]


class RagPipeline:
    def __init__(self, models: LocalRagModels | None = None) -> None:
        self.settings = get_settings()
        self.models = models or LocalRagModels(self.settings)

    def run(
        self,
        query: str,
        *,
        purpose: str = "general",
        top_k: int | None = None,
        files: tuple[str, ...] = DEFAULT_KNOWLEDGE_FILES,
    ) -> RagResult:
        warnings = []
        final_top_k = top_k or self.settings.rag_top_k_final
        rewritten_query, rewrite_warnings = self.models.rewrite_query(query)
        warnings.extend(rewrite_warnings)
        model_keywords, keyword_warnings = self.models.extract_keywords(rewritten_query)
        warnings.extend(keyword_warnings)
        keywords = _fallback_keywords(rewritten_query, model_keywords)

        chunks = load_rag_chunks(files=files)
        candidates = self._retrieve_candidates(rewritten_query, chunks, warnings)
        reranked = self._rerank(rewritten_query, candidates, warnings)
        selected = reranked[: max(1, final_top_k)]
        evidence = [_to_rule_evidence(score, chunk) for score, chunk in selected]

        answer, answer_warnings = self.models.generate_answer(
            purpose=purpose,
            query=rewritten_query,
            evidence_text=_evidence_text(evidence),
        )
        warnings.extend(answer_warnings)
        if not answer:
            answer = _deterministic_answer(purpose, evidence)

        return RagResult(
            evidence=evidence,
            answer=answer,
            model_profile=self.models.profile.model_dump(),
            warnings=warnings,
            rewritten_query=rewritten_query,
            keywords=keywords,
        )

    def _retrieve_candidates(
        self,
        query: str,
        chunks: list[RagChunk],
        warnings: list[str],
    ) -> list[tuple[float, RagChunk]]:
        recall_top_k = max(self.settings.rag_top_k_recall, self.settings.rag_top_k_final)
        keyword_ranked = rank_keyword(query, chunks, top_k=recall_top_k)
        vector_ranked: list[tuple[float, RagChunk]] = []
        try:
            embeddings = self.models.embed_texts(
                [query, *[chunk.searchable_text for chunk in chunks]]
            )
            vector_ranked = rank_vector(
                embeddings[0],
                embeddings[1:],
                chunks,
                top_k=recall_top_k,
            )
        except RagModelUnavailable as exc:
            warnings.append(str(exc))

        merged = merge_rankings(
            keyword_ranked=keyword_ranked,
            vector_ranked=vector_ranked,
            top_k=recall_top_k,
        )
        if not merged:
            warnings.append("rag_no_evidence_found")
        return merged

    def _rerank(
        self,
        query: str,
        candidates: list[tuple[float, RagChunk]],
        warnings: list[str],
    ) -> list[tuple[float, RagChunk]]:
        if not candidates:
            return []
        try:
            scores = self.models.rerank(query, [chunk.searchable_text for _, chunk in candidates])
        except RagModelUnavailable as exc:
            warnings.append(str(exc))
            return candidates

        reranked = [
            (float(rerank_score) + original_score * 0.1, chunk)
            for rerank_score, (original_score, chunk) in zip(scores, candidates)
        ]
        reranked.sort(key=lambda item: (-item[0], item[1].rule_id))
        return reranked


def run_rag(
    query: str,
    *,
    purpose: str = "general",
    top_k: int | None = None,
    files: tuple[str, ...] = DEFAULT_KNOWLEDGE_FILES,
) -> RagResult:
    return RagPipeline().run(query, purpose=purpose, top_k=top_k, files=files)


def _to_rule_evidence(score: float, chunk: RagChunk) -> RuleEvidence:
    return RuleEvidence(
        source=chunk.source,
        rule_id=chunk.rule_id,
        title=chunk.title,
        text=chunk.text,
        score=round(max(score, 0.0), 3),
        tags=list(chunk.tags),
    )


def _fallback_keywords(query: str, model_keywords: list[str]) -> list[str]:
    if model_keywords:
        return model_keywords
    return sorted(tokenize(query))[:8]


def _evidence_text(evidence: list[RuleEvidence]) -> str:
    return "\n".join(
        f"[{item.rule_id}] {item.title}: {item.text}"
        for item in evidence
    )


def _deterministic_answer(purpose: str, evidence: list[RuleEvidence]) -> str:
    if not evidence:
        return "未检索到可绑定的本地知识库证据。"
    titles = "；".join(f"{item.rule_id} {item.title}" for item in evidence[:3])
    if purpose == "planning":
        return f"方案依据参考本地知识库证据：{titles}。"
    if purpose == "compliance":
        return f"合规审查依据参考本地知识库证据：{titles}。"
    return f"检索依据参考本地知识库证据：{titles}。"
