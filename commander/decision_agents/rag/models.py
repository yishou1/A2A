"""Lazy ONNX model adapters for the RAG pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from decision_agents.common.config import Settings, get_settings


class RagModelUnavailable(RuntimeError):
    pass


_SESSION_CACHE: dict[tuple[str, tuple[str, ...]], Any] = {}


@dataclass(frozen=True)
class RagModelProfile:
    enabled: bool
    backend: str
    query_model: str
    embedding_model: str
    rerank_model: str
    generation_model: str
    providers: list[str]

    def model_dump(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "backend": self.backend,
            "query_model": self.query_model,
            "embedding_model": self.embedding_model,
            "rerank_model": self.rerank_model,
            "generation_model": self.generation_model,
            "providers": self.providers,
        }


class LocalRagModels:
    """ONNX-only adapter for optional local RAG models.

    The class name is kept for compatibility with the existing pipeline, but
    this implementation does not load Hugging Face models or trigger downloads.
    It only runs local ONNX files through onnxruntime. If a model requires a
    tokenizer or a non-text input contract that is not provided here, the caller
    receives a clear warning and the RAG pipeline falls back to deterministic
    keyword retrieval/answering.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        enabled = self.settings.enable_rag_onnx_models or self.settings.enable_local_rag_models
        self.providers = _providers(self.settings.rag_onnx_providers)
        self.profile = RagModelProfile(
            enabled=enabled,
            backend="onnxruntime",
            query_model=_model_path(self.settings.rag_query_model, self.settings),
            embedding_model=_model_path(self.settings.rag_embedding_model, self.settings),
            rerank_model=_model_path(self.settings.rag_rerank_model, self.settings),
            generation_model=_model_path(self.settings.rag_generation_model, self.settings),
            providers=list(self.providers),
        )

    def rewrite_query(self, query: str) -> tuple[str, list[str]]:
        if not self.profile.enabled:
            return query, ["rag_onnx_models_disabled:query_rewrite"]
        try:
            output = _run_text_generation_model(
                self.profile.query_model,
                [query],
                providers=self.providers,
            )
            return output[0].strip() or query, []
        except RagModelUnavailable as exc:
            return query, [f"query_rewrite_unavailable:{exc}"]

    def extract_keywords(self, query: str) -> tuple[list[str], list[str]]:
        if not self.profile.enabled:
            return [], ["rag_onnx_models_disabled:keyword_extract"]
        try:
            output = _run_text_generation_model(
                self.profile.query_model,
                [f"keywords:{query}"],
                providers=self.providers,
            )
            text = output[0].replace("，", ",")
            keywords = [item.strip() for item in text.split(",") if item.strip()]
            return keywords[:8], []
        except RagModelUnavailable as exc:
            return [], [f"keyword_extract_unavailable:{exc}"]

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not self.profile.enabled:
            raise RagModelUnavailable("rag_onnx_models_disabled:embedding")
        embeddings = _run_embedding_model(
            self.profile.embedding_model,
            texts,
            providers=self.providers,
        )
        return [_normalize_vector(vector) for vector in embeddings]

    def rerank(self, query: str, texts: list[str]) -> list[float]:
        if not self.profile.enabled:
            raise RagModelUnavailable("rag_onnx_models_disabled:rerank")
        return _run_rerank_model(
            self.profile.rerank_model,
            query,
            texts,
            providers=self.providers,
        )

    def generate_answer(
        self,
        *,
        purpose: str,
        query: str,
        evidence_text: str,
    ) -> tuple[str, list[str]]:
        if not self.profile.enabled:
            return "", ["rag_onnx_models_disabled:generation"]
        prompt = (
            "你是一个本地RAG证据整理助手。请只基于给定证据，"
            "生成简洁、可追溯的中文说明；不得补充证据外内容。\n"
            f"用途：{purpose}\n"
            f"查询：{query}\n"
            f"证据：\n{evidence_text}\n"
        )
        try:
            output = _run_text_generation_model(
                self.profile.generation_model,
                [prompt],
                providers=self.providers,
            )
            return output[0].strip(), []
        except RagModelUnavailable as exc:
            return "", [f"generation_unavailable:{exc}"]


def _run_embedding_model(
    model_path: str,
    texts: list[str],
    *,
    providers: tuple[str, ...],
) -> list[list[float]]:
    outputs = _run_string_input_model(model_path, texts, providers=providers)
    if not outputs:
        raise RagModelUnavailable("onnx_no_outputs:embedding")
    array = _as_numpy(outputs[0])
    if array.ndim == 1:
        array = array.reshape(1, -1)
    if array.ndim == 3:
        array = array.mean(axis=1)
    if array.ndim != 2:
        raise RagModelUnavailable(f"onnx_invalid_embedding_shape:{tuple(array.shape)}")
    return [[float(value) for value in row] for row in array.tolist()]


def _run_rerank_model(
    model_path: str,
    query: str,
    texts: list[str],
    *,
    providers: tuple[str, ...],
) -> list[float]:
    session = _load_session(model_path, providers)
    inputs = session.get_inputs()
    if len(inputs) >= 2 and all(_is_string_input(item) for item in inputs[:2]):
        feeds = {
            inputs[0].name: _string_array([query] * len(texts), inputs[0].shape),
            inputs[1].name: _string_array(texts, inputs[1].shape),
        }
        outputs = session.run(None, feeds)
    elif len(inputs) == 1 and _is_string_input(inputs[0]):
        pairs = [f"{query} [SEP] {text}" for text in texts]
        outputs = session.run(None, {inputs[0].name: _string_array(pairs, inputs[0].shape)})
    else:
        raise RagModelUnavailable(_tokenizer_required_reason("rerank", inputs))
    if not outputs:
        raise RagModelUnavailable("onnx_no_outputs:rerank")
    array = _as_numpy(outputs[0]).reshape(-1)
    return [float(value) for value in array[: len(texts)].tolist()]


def _run_text_generation_model(
    model_path: str,
    texts: list[str],
    *,
    providers: tuple[str, ...],
) -> list[str]:
    outputs = _run_string_input_model(model_path, texts, providers=providers)
    if not outputs:
        raise RagModelUnavailable("onnx_no_outputs:text_generation")
    output = outputs[0]
    if isinstance(output, list):
        return [str(item) for item in output]
    array = _as_numpy(output)
    if array.dtype.kind in {"U", "S", "O"}:
        return [str(item) for item in array.reshape(-1).tolist()]
    raise RagModelUnavailable(
        "onnx_text_decoder_required:numeric_generation_output"
    )


def _run_string_input_model(
    model_path: str,
    texts: list[str],
    *,
    providers: tuple[str, ...],
) -> list[Any]:
    session = _load_session(model_path, providers)
    inputs = session.get_inputs()
    if not inputs:
        raise RagModelUnavailable("onnx_no_inputs")
    if len(inputs) != 1 or not _is_string_input(inputs[0]):
        raise RagModelUnavailable(_tokenizer_required_reason("text", inputs))
    return session.run(None, {inputs[0].name: _string_array(texts, inputs[0].shape)})


def _load_session(model_path: str, providers: tuple[str, ...]):
    path = Path(model_path).expanduser()
    if not path.exists():
        raise RagModelUnavailable(f"onnx_model_not_found:{path}")
    try:
        import onnxruntime as ort
    except Exception as exc:
        raise RagModelUnavailable(f"onnxruntime_unavailable:{exc}") from exc
    cache_key = (str(path.resolve()), providers)
    session = _SESSION_CACHE.get(cache_key)
    if session is None:
        session = ort.InferenceSession(str(path), providers=list(providers))
        _SESSION_CACHE[cache_key] = session
    return session


def _string_array(texts: list[str], shape: Any):
    import numpy as np

    if isinstance(shape, list) and len(shape) >= 2:
        return np.array([[text] for text in texts], dtype=object)
    return np.array(texts, dtype=object)


def _as_numpy(value: Any):
    import numpy as np

    if hasattr(value, "detach"):
        value = value.detach().cpu().numpy()
    return np.asarray(value)


def _is_string_input(input_meta: Any) -> bool:
    input_type = str(getattr(input_meta, "type", "")).lower()
    return "string" in input_type or "str" in input_type


def _tokenizer_required_reason(stage: str, inputs: list[Any]) -> str:
    names = ",".join(str(getattr(item, "name", "")) for item in inputs)
    types = ",".join(str(getattr(item, "type", "")) for item in inputs)
    return f"onnx_tokenizer_required:{stage}:inputs={names}:types={types}"


def _normalize_vector(vector: list[float]) -> list[float]:
    norm = sum(value * value for value in vector) ** 0.5
    if norm <= 0.0:
        return vector
    return [value / norm for value in vector]


def _providers(value: str) -> tuple[str, ...]:
    providers = tuple(item.strip() for item in value.split(",") if item.strip())
    return providers or ("CPUExecutionProvider",)


def _model_path(value: str, settings: Settings) -> str:
    path = Path(value).expanduser()
    if path.is_absolute() or path.parts[:2] == ("models", "rag"):
        return str(path)
    return str(Path(settings.rag_onnx_model_dir) / path)
