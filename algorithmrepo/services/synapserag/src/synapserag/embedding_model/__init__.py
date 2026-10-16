from .base import EmbeddingConfig, BaseEmbeddingModel
from .OpenAI import OpenAIEmbeddingModel
from .Cohere import CohereEmbeddingModel

try:
    from .Contriever import ContrieverModel
    from .GritLM import GritLMEmbeddingModel
    from .NVEmbedV2 import NVEmbedV2EmbeddingModel
    from .Transformers import TransformersEmbeddingModel
    from .VLLM import VLLMEmbeddingModel
except Exception:
    ContrieverModel = None
    GritLMEmbeddingModel = None
    NVEmbedV2EmbeddingModel = None
    TransformersEmbeddingModel = None
    VLLMEmbeddingModel = None

from ..utils.logging_utils import get_logger

logger = get_logger(__name__)


def _get_embedding_model_class(embedding_model_name: str = "nvidia/NV-Embed-v2"):
    normalized_name = embedding_model_name.lower()
    if "gritlm" in normalized_name:
        return GritLMEmbeddingModel
    elif "nv-embed-v2" in normalized_name:
        return NVEmbedV2EmbeddingModel
    elif "contriever" in normalized_name:
        return ContrieverModel
    elif "cohere" in normalized_name:
        return CohereEmbeddingModel
    elif embedding_model_name.startswith("Transformers/"):
        return TransformersEmbeddingModel
    elif embedding_model_name.startswith("VLLM/"):
        return VLLMEmbeddingModel
    elif normalized_name == "baai/bge-m3":
        return OpenAIEmbeddingModel
    elif "embedding" in normalized_name:
        return OpenAIEmbeddingModel
    assert False, f"Unknown embedding model name: {embedding_model_name}"
