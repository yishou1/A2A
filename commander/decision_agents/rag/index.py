"""In-memory retrieval and ranking utilities for local RAG."""

from __future__ import annotations

from math import sqrt

from decision_agents.rag.documents import RagChunk, tokenize


def rank_keyword(
    query: str,
    chunks: list[RagChunk],
    *,
    top_k: int,
) -> list[tuple[float, RagChunk]]:
    query_tokens = tokenize(query)
    if not query_tokens:
        return []

    scored = []
    for chunk in chunks:
        chunk_tokens = tokenize(chunk.searchable_text)
        overlap = query_tokens & chunk_tokens
        if not overlap:
            continue
        tag_overlap = query_tokens & set(chunk.tags)
        title_overlap = query_tokens & tokenize(chunk.title)
        score = len(overlap) + len(tag_overlap) * 1.5 + len(title_overlap) * 0.75
        scored.append((float(score), chunk))

    scored.sort(key=lambda item: (-item[0], item[1].rule_id))
    return scored[: max(1, top_k)]


def rank_vector(
    query_embedding: list[float],
    chunk_embeddings: list[list[float]],
    chunks: list[RagChunk],
    *,
    top_k: int,
) -> list[tuple[float, RagChunk]]:
    scored = [
        (cosine_similarity(query_embedding, embedding), chunk)
        for embedding, chunk in zip(chunk_embeddings, chunks)
    ]
    scored = [(score, chunk) for score, chunk in scored if score > 0.0]
    scored.sort(key=lambda item: (-item[0], item[1].rule_id))
    return scored[: max(1, top_k)]


def merge_rankings(
    *,
    keyword_ranked: list[tuple[float, RagChunk]],
    vector_ranked: list[tuple[float, RagChunk]],
    top_k: int,
) -> list[tuple[float, RagChunk]]:
    by_key: dict[tuple[str, str], tuple[float, RagChunk]] = {}
    max_keyword = max([score for score, _ in keyword_ranked] or [1.0])
    for score, chunk in keyword_ranked:
        key = (chunk.source, chunk.rule_id)
        normalized = score / max_keyword
        by_key[key] = (max(by_key.get(key, (0.0, chunk))[0], normalized), chunk)
    for score, chunk in vector_ranked:
        key = (chunk.source, chunk.rule_id)
        merged_score = by_key.get(key, (0.0, chunk))[0] + max(score, 0.0)
        by_key[key] = (merged_score, chunk)

    scored = list(by_key.values())
    scored.sort(key=lambda item: (-item[0], item[1].rule_id))
    return scored[: max(1, top_k)]


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = sqrt(sum(value * value for value in left))
    right_norm = sqrt(sum(value * value for value in right))
    if left_norm <= 0.0 or right_norm <= 0.0:
        return 0.0
    return dot / (left_norm * right_norm)
