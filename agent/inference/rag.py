"""SynapseRAG：PageIndex 检索 + GraphRAG 实体图谱 + 可选 LLM Agent。"""

from __future__ import annotations

import os
from typing import Any

import numpy as np

from agent.inference.registry import get_page_encoder


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-8))


def _build_graph_entities(classifications: list[dict[str, Any]]) -> list[dict[str, Any]]:
    entities: list[dict[str, Any]] = []
    for cls in classifications:
        label = cls.get("label", "unknown")
        entities.append(
            {
                "entity_id": f"ENT-{cls['target_id']}",
                "type": "MilitaryUnit",
                "label": label,
                "relations": [
                    {"predicate": "threat_of", "object": "mission_area"},
                    {"predicate": "classified_as", "object": label},
                ],
            }
        )
    return entities


def _llm_agent_summary(query: str, retrieved: list[dict[str, Any]], config: dict[str, Any]) -> str:
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key or not retrieved:
        return f"[SynapseRAG] query={query!r}; page_hits={len(retrieved)}"
    try:
        import requests

        base = os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1").rstrip("/")
        model = config.get("llm_model", os.environ.get("OPENAI_MODEL", "gpt-4o-mini"))
        snippets = "\n".join(f"- {d.get('page')}: {d.get('text', '')[:200]}" for d in retrieved[:3])
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": "你是战术情报 SynapseRAG Agent，基于检索页生成简短实体关联摘要。",
                },
                {"role": "user", "content": f"Query: {query}\nPages:\n{snippets}"},
            ],
            "max_tokens": 120,
        }
        res = requests.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
            timeout=15,
        )
        if res.status_code == 200:
            return res.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        pass
    return f"[SynapseRAG] query={query!r}; page_hits={len(retrieved)}"


def run_synapse_rag(
    classifications: list[dict[str, Any]],
    context_docs: list[dict[str, Any]],
    query: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    entities = _build_graph_entities(classifications)
    top_k = int(config.get("rag_top_k", config.get("page_index_top_k", 3)))

    if not context_docs:
        return {
            "entities": entities,
            "retrieved_pages": [],
            "rag_context": f"[SynapseRAG] query={query!r}; no knowledge_base",
            "agent_notes": "PageIndex 为空，仅基于分类构建图谱节点。",
        }

    encoder = get_page_encoder(config)
    doc_texts = [f"{d.get('page', 'doc')}: {d.get('text', '')}" for d in context_docs]
    q_emb = encoder.encode([query], normalize_embeddings=True)[0]
    d_emb = encoder.encode(doc_texts, normalize_embeddings=True)

    scores = [_cosine(q_emb, d_emb[i]) for i in range(len(doc_texts))]
    ranked = sorted(zip(context_docs, scores), key=lambda x: x[1], reverse=True)[:top_k]
    retrieved = [{**doc, "score": round(sc, 4)} for doc, sc in ranked]

    # GraphRAG：将检索页链接到实体边
    for ent in entities:
        ent["relations"].append(
            {
                "predicate": "supported_by",
                "object": retrieved[0].get("page", "page_index") if retrieved else "none",
            }
        )

    rag_context = _llm_agent_summary(query, retrieved, config)
    return {
        "entities": entities,
        "retrieved_pages": retrieved,
        "rag_context": rag_context,
        "agent_notes": "SynapseRAG PageIndex+GraphRAG 检索完成。",
    }
