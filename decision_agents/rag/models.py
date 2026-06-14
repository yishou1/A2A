"""Lazy local model adapters for the RAG pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from decision_agents.config import Settings, get_settings


class RagModelUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class RagModelProfile:
    enabled: bool
    query_model: str
    embedding_model: str
    rerank_model: str
    generation_model: str

    def model_dump(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "query_model": self.query_model,
            "embedding_model": self.embedding_model,
            "rerank_model": self.rerank_model,
            "generation_model": self.generation_model,
        }


class LocalRagModels:
    """Adapter around optional Hugging Face local models.

    The adapter imports heavy ML dependencies only when local RAG models are
    enabled. This keeps the default test path deterministic and lightweight.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.profile = RagModelProfile(
            enabled=self.settings.enable_local_rag_models,
            query_model=self.settings.rag_query_model,
            embedding_model=self.settings.rag_embedding_model,
            rerank_model=self.settings.rag_rerank_model,
            generation_model=self.settings.rag_generation_model,
        )
        self._embedding_model = None
        self._rerank_model = None
        self._generation_pipeline = None
        self._query_pipeline = None

    def rewrite_query(self, query: str) -> tuple[str, list[str]]:
        if not self.profile.enabled:
            return query, ["local_rag_models_disabled:query_rewrite"]
        try:
            pipeline = self._load_query_pipeline()
            prompt = (
                "请将下面内容改写为用于知识库检索的简洁中文查询，"
                "只输出查询文本：\n"
                f"{query}"
            )
            generated = pipeline(prompt, max_new_tokens=96, do_sample=False)
            text = _extract_generated_text(generated).replace(prompt, "").strip()
            return text or query, []
        except Exception as exc:
            return query, [f"query_rewrite_unavailable:{exc}"]

    def extract_keywords(self, query: str) -> tuple[list[str], list[str]]:
        if not self.profile.enabled:
            return [], ["local_rag_models_disabled:keyword_extract"]
        try:
            pipeline = self._load_query_pipeline()
            prompt = (
                "请从下面检索请求中抽取最多8个关键词，使用逗号分隔，只输出关键词：\n"
                f"{query}"
            )
            generated = pipeline(prompt, max_new_tokens=64, do_sample=False)
            text = _extract_generated_text(generated).replace(prompt, "")
            keywords = [item.strip() for item in text.replace("，", ",").split(",")]
            return [item for item in keywords if item][:8], []
        except Exception as exc:
            return [], [f"keyword_extract_unavailable:{exc}"]

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not self.profile.enabled:
            raise RagModelUnavailable("local_rag_models_disabled:embedding")
        try:
            model = self._load_embedding_model()
            embeddings = model.encode(texts, normalize_embeddings=True)
            return [
                embedding.tolist() if hasattr(embedding, "tolist") else list(embedding)
                for embedding in embeddings
            ]
        except Exception as exc:
            raise RagModelUnavailable(f"embedding_unavailable:{exc}") from exc

    def rerank(self, query: str, texts: list[str]) -> list[float]:
        if not self.profile.enabled:
            raise RagModelUnavailable("local_rag_models_disabled:rerank")
        try:
            model = self._load_rerank_model()
            pairs = [(query, text) for text in texts]
            scores = model.predict(pairs)
            return [float(score) for score in scores]
        except Exception as exc:
            raise RagModelUnavailable(f"rerank_unavailable:{exc}") from exc

    def generate_answer(
        self,
        *,
        purpose: str,
        query: str,
        evidence_text: str,
    ) -> tuple[str, list[str]]:
        if not self.profile.enabled:
            return "", ["local_rag_models_disabled:generation"]
        try:
            pipeline = self._load_generation_pipeline()
            prompt = (
                "你是一个本地RAG证据整理助手。请只基于给定证据，"
                "生成简洁、可追溯的中文说明；不得补充证据外内容。\n"
                f"用途：{purpose}\n"
                f"查询：{query}\n"
                f"证据：\n{evidence_text}\n"
            )
            generated = pipeline(prompt, max_new_tokens=192, do_sample=False)
            text = _extract_generated_text(generated).replace(prompt, "").strip()
            return text, []
        except Exception as exc:
            return "", [f"generation_unavailable:{exc}"]

    def _load_embedding_model(self):
        if self._embedding_model is None:
            from sentence_transformers import SentenceTransformer

            self._embedding_model = SentenceTransformer(self.profile.embedding_model)
        return self._embedding_model

    def _load_rerank_model(self):
        if self._rerank_model is None:
            from sentence_transformers import CrossEncoder

            self._rerank_model = CrossEncoder(self.profile.rerank_model)
        return self._rerank_model

    def _load_generation_pipeline(self):
        if self._generation_pipeline is None:
            from transformers import pipeline

            self._generation_pipeline = pipeline(
                "text-generation",
                model=self.profile.generation_model,
                trust_remote_code=True,
            )
        return self._generation_pipeline

    def _load_query_pipeline(self):
        if self._query_pipeline is None:
            from transformers import pipeline

            self._query_pipeline = pipeline(
                "text-generation",
                model=self.profile.query_model,
                trust_remote_code=True,
            )
        return self._query_pipeline


def _extract_generated_text(generated: Any) -> str:
    if isinstance(generated, list) and generated:
        first = generated[0]
        if isinstance(first, dict):
            return str(first.get("generated_text") or first.get("text") or "")
    if isinstance(generated, dict):
        return str(generated.get("generated_text") or generated.get("text") or "")
    return str(generated or "")
