import os
import hashlib
from dataclasses import replace

from ..utils.logging_utils import get_logger
from ..utils.config_utils import BaseConfig, LLMEndpointConfig

from .openai_gpt import CacheOpenAI
from .base import BaseLLM, coerce_llm_response
from .bedrock_llm import BedrockLLM
from .requests_llm import RequestsLLM
try:
    from .transformers_llm import TransformersLLM
except Exception:
    TransformersLLM = None


logger = get_logger(__name__)


CUSTOM_OPENAI_ENV_KEY = "SYNAPSERAG_OPENAI_API_KEY"
FALLBACK_ENV_KEYS = (
    "SYNAPSERAG_LLM_API_KEY",
    "BAILIAN_API_KEY",
    "DASHSCOPE_API_KEY",
    "SILICONFLOW_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
)


ROLE_ENV_KEYS = {
    "openie": ("SYNAPSERAG_OPENIE_API_KEY",),
    "rerank": ("SYNAPSERAG_RERANK_API_KEY", "SYNAPSERAG_QA_API_KEY"),
    "qa": ("SYNAPSERAG_QA_API_KEY",),
}


def _is_local_base_url(base_url):
    if not base_url:
        return False
    normalized = base_url.lower()
    return (
        "localhost" in normalized
        or "127.0.0.1" in normalized
        or "[::1]" in normalized
    )


def _resolve_api_key(config: BaseConfig, endpoint: LLMEndpointConfig = None, role: str = "qa"):
    endpoint = endpoint or config.get_llm_endpoint(role)
    keys = []
    if endpoint.api_key_env:
        keys.append(endpoint.api_key_env)
    keys.extend(ROLE_ENV_KEYS.get(role, ()))
    keys.extend((CUSTOM_OPENAI_ENV_KEY, *FALLBACK_ENV_KEYS))

    for key in dict.fromkeys(keys):
        candidate = os.getenv(key)
        if candidate:
            return candidate

    if _is_local_base_url(endpoint.base_url):
        return "sk-local"

    return None


class _LLMResponseAdapter:
    """Normalize all current backends without breaking tuple-unpacking callers."""

    def __init__(self, client, endpoint: LLMEndpointConfig):
        self._client = client
        self._endpoint = endpoint

    def infer(self, *args, **kwargs):
        call_kwargs = dict(self._endpoint.extra_body)
        call_kwargs.update(kwargs)
        return coerce_llm_response(self._client.infer(*args, **call_kwargs))

    def __getattr__(self, name):
        return getattr(self._client, name)


def create_llm(endpoint: LLMEndpointConfig, config: BaseConfig, role: str = "qa"):
    role_config = replace(
        config,
        llm_name=endpoint.model_name,
        llm_base_url=endpoint.base_url,
        azure_endpoint=endpoint.azure_endpoint,
        azure_api_version=endpoint.api_version,
        max_retry_attempts=endpoint.max_retries,
        temperature=endpoint.temperature,
        max_new_tokens=endpoint.max_tokens,
        response_format=endpoint.response_format,
    )
    api_key = _resolve_api_key(config, endpoint=endpoint, role=role)
    endpoint_fingerprint = hashlib.sha256(
        (
            f"{endpoint.provider}|{endpoint.base_url}|{endpoint.azure_endpoint}|"
            f"{endpoint.api_version}|{endpoint.model_name}"
        ).encode("utf-8")
    ).hexdigest()[:12]
    cache_namespace = f"{role}-{endpoint_fingerprint}"

    # RequestsLLM: 使用 requests 库的 OpenAI 兼容实现（适配 Claude API 等）
    if endpoint.model_name.startswith('requests/'):
        client = RequestsLLM.from_experiment_config(
            role_config,
            api_key=api_key,
            cache_namespace=cache_namespace,
            max_retries=endpoint.max_retries,
            timeout_seconds=endpoint.timeout_seconds,
        )
        return _LLMResponseAdapter(client, endpoint)

    if endpoint.model_name.startswith('bedrock'):
        return _LLMResponseAdapter(BedrockLLM(role_config), endpoint)

    if endpoint.model_name.startswith('Transformers/'):
        if TransformersLLM is None:
            raise RuntimeError("Transformers LLM dependencies are not available")
        return _LLMResponseAdapter(TransformersLLM(role_config), endpoint)

    client = CacheOpenAI.from_experiment_config(
        role_config,
        api_key=api_key,
        cache_namespace=cache_namespace,
        max_retries=endpoint.max_retries,
        timeout_seconds=endpoint.timeout_seconds,
    )
    return _LLMResponseAdapter(client, endpoint)


def _get_llm_class(config: BaseConfig):
    """Legacy factory: the historical llm_* fields now represent the QA role."""
    return create_llm(config.get_llm_endpoint("qa"), config, role="qa")
    
