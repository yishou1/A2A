from copy import deepcopy
import os
from typing import List, Optional

import numpy as np
import torch
from tqdm import tqdm
from transformers import AutoModel
from openai import OpenAI
from openai import AzureOpenAI

from ..utils.config_utils import BaseConfig
from ..utils.logging_utils import get_logger
from .base import BaseEmbeddingModel, EmbeddingConfig, make_cache_embed

logger = get_logger(__name__)


def _is_local_base_url(base_url: Optional[str]) -> bool:
    if not base_url:
        return False
    normalized = base_url.lower()
    return (
        "localhost" in normalized
        or "127.0.0.1" in normalized
        or "[::1]" in normalized
    )


def _resolve_embedding_api_key(global_config: Optional[BaseConfig] = None) -> Optional[str]:
    """Resolve credentials for OpenAI-compatible embedding providers."""
    configured_key = getattr(global_config, "embedding_api_key_env", None)
    for key in dict.fromkeys(filter(None, (
        configured_key,
        "SYNAPSERAG_EMBEDDING_API_KEY",
        "SILICONFLOW_API_KEY",
        "BAILIAN_API_KEY",
        "DASHSCOPE_API_KEY",
        "SYNAPSERAG_OPENAI_API_KEY",
        "OPENAI_API_KEY",
    ))):
        candidate = os.getenv(key)
        if candidate:
            return candidate
    if global_config is not None and _is_local_base_url(global_config.embedding_base_url):
        return "sk-local"
    return None


class OpenAIEmbeddingModel(BaseEmbeddingModel):

    def __init__(self, global_config: Optional[BaseConfig] = None, embedding_model_name: Optional[str] = None) -> None:
        super().__init__(global_config=global_config)

        if embedding_model_name is not None:
            self.embedding_model_name = embedding_model_name
            logger.debug(
                f"Overriding {self.__class__.__name__}'s embedding_model_name with: {self.embedding_model_name}")

        self._init_embedding_config()

        # Initializing the embedding model
        logger.debug(
            f"Initializing {self.__class__.__name__}'s embedding model with params: {self.embedding_config.model_init_params}")

        if self.global_config.azure_embedding_endpoint is None:
            self.client = OpenAI(
                base_url=self.global_config.embedding_base_url,
                api_key=_resolve_embedding_api_key(self.global_config),
                timeout=self.global_config.embedding_timeout_seconds,
            )
        else:
            self.client = AzureOpenAI(api_version=self.global_config.azure_embedding_endpoint.split('api-version=')[1],
                                      azure_endpoint=self.global_config.azure_embedding_endpoint)


    def _init_embedding_config(self) -> None:
        """
        Extract embedding model-specific parameters to init the EmbeddingConfig.

        Returns:
            None
        """

        config_dict = {
            "embedding_model_name": self.embedding_model_name,
            "norm": self.global_config.embedding_return_as_normalized,
            # "max_seq_length": self.global_config.embedding_max_seq_len,
            "model_init_params": {
                # "model_name_or_path": self.embedding_model_name2mode_name_or_path[self.embedding_model_name],
                "pretrained_model_name_or_path": self.embedding_model_name,
                "trust_remote_code": True,
                # "torch_dtype": "auto",
                'device_map': "auto",  # added this line to use multiple GPUs
                # **kwargs
            },
            "encode_params": {
                "max_length": self.global_config.embedding_max_seq_len,  # 32768 from official example,
                "instruction": "",
                "batch_size": self.global_config.embedding_batch_size,
                "num_workers": 32
            },
        }

        self.embedding_config = EmbeddingConfig.from_dict(config_dict=config_dict)
        logger.debug(f"Init {self.__class__.__name__}'s embedding_config: {self.embedding_config}")

    def encode(self, texts: List[str], instruction: str = ""):
        texts = [t.replace("\n", " ") for t in texts]
        texts = [t if t != '' else ' ' for t in texts]
        mode = self.global_config.embedding_query_instruction_mode
        request_kwargs = {}
        if instruction and mode == "prefix":
            texts = [f"Instruct: {instruction}\nQuery: {text}" for text in texts]
        elif instruction and mode == "provider":
            request_kwargs["extra_body"] = {"instruction": instruction}

        response = self.client.embeddings.create(
            input=texts,
            model=self.embedding_model_name,
            **request_kwargs,
        )
        results = np.array([v.embedding for v in response.data])

        if results.ndim != 2 or results.shape[0] != len(texts):
            raise ValueError(
                f"Embedding endpoint returned shape {results.shape} for {len(texts)} inputs")

        return results

    def batch_encode(self, texts: List[str], **kwargs) -> None:
        if isinstance(texts, str): texts = [texts]

        params = deepcopy(self.embedding_config.encode_params)
        if kwargs: params.update(kwargs)
        instruction = kwargs.get(
            "instruction",
            self.global_config.embedding_document_instruction,
        )
        params.pop("instruction", None)

        logger.debug(f"Calling {self.__class__.__name__} with:\n{params}")

        batch_size = params.pop("batch_size", 16)

        if len(texts) <= batch_size:
            results = self.encode(texts, instruction=instruction)
        else:
            pbar = tqdm(total=len(texts), desc="Batch Encoding")
            results = []
            for i in range(0, len(texts), batch_size):
                batch = texts[i:i + batch_size]
                try:
                    batch_results = self.encode(batch, instruction=instruction)
                    if results and batch_results.shape[1] != results[0].shape[1]:
                        raise ValueError(
                            f"Embedding dimension changed at batch offset {i}: "
                            f"{results[0].shape[1]} -> {batch_results.shape[1]}"
                        )
                    results.append(batch_results)
                except Exception as error:
                    raise RuntimeError(
                        f"Embedding batch failed at offset {i}, size {len(batch)}, "
                        f"endpoint={self.global_config.embedding_base_url}"
                    ) from error
                pbar.update(len(batch))
            pbar.close()
            results = np.concatenate(results)

        if isinstance(results, torch.Tensor):
            results = results.cpu()
            results = results.numpy()
        if self.embedding_config.norm:
            norms = np.linalg.norm(results, axis=1, keepdims=True)
            if np.any(norms == 0):
                raise ValueError("Embedding endpoint returned a zero vector")
            results = results / norms
        if not np.isfinite(results).all():
            raise ValueError("Embedding endpoint returned NaN or infinite values")

        return results
