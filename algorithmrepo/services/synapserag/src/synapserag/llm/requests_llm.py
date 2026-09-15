"""
RequestsLLM: 使用 requests 库的 OpenAI 兼容 LLM 实现
适配 Claude API 等与 OpenAI SDK 不兼容的端点
"""

import functools
import hashlib
import json
import os
import sqlite3
from copy import deepcopy
from typing import List, Tuple, Optional

import requests
from filelock import FileLock

from ..utils.config_utils import BaseConfig
from ..utils.llm_utils import TextChatMessage
from ..utils.logging_utils import get_logger
from .base import BaseLLM, LLMConfig

logger = get_logger(__name__)


def _is_local_base_url(base_url: Optional[str]) -> bool:
    """判断是否为本地 URL"""
    if base_url is None:
        return False
    normalized = base_url.lower()
    return (
        "localhost" in normalized
        or normalized.startswith("http://127.0.0.1")
        or normalized.startswith("https://127.0.0.1")
    )


def cache_response(func):
    """缓存装饰器：将 LLM 响应缓存到 SQLite 数据库"""
    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        # 从 args 或 kwargs 获取 messages
        if args:
            messages = args[0]
        else:
            messages = kwargs.get("messages")
        if messages is None:
            raise ValueError("Missing required 'messages' parameter for caching.")

        # Cache every generation/profile option, not only model/temperature.
        gen_params = getattr(self, "llm_config", {}).generate_params if hasattr(self, "llm_config") else {}
        request_params = deepcopy(gen_params)
        request_params.update(kwargs)
        request_params.pop("messages", None)

        # 构建缓存 key
        key_data = {
            "messages": messages,
            "request_params": request_params,
        }
        key_str = json.dumps(key_data, sort_keys=True, default=str)
        key_hash = hashlib.sha256(key_str.encode("utf-8")).hexdigest()

        lock_file = self.cache_file_name + ".lock"

        # 尝试从 SQLite 缓存读取
        with FileLock(lock_file):
            conn = sqlite3.connect(self.cache_file_name)
            c = conn.cursor()
            c.execute("""
                CREATE TABLE IF NOT EXISTS cache (
                    key TEXT PRIMARY KEY,
                    message TEXT,
                    metadata TEXT
                )
            """)
            conn.commit()
            c.execute("SELECT message, metadata FROM cache WHERE key = ?", (key_hash,))
            row = c.fetchone()
            conn.close()
            if row is not None:
                message, metadata_str = row
                metadata = json.loads(metadata_str)
                logger.debug(f"Cache hit for key {key_hash[:8]}...")
                return message, metadata, True

        # 缓存未命中，调用原函数
        result = func(self, *args, **kwargs)
        message, metadata = result

        # 将结果写入缓存
        with FileLock(lock_file):
            conn = sqlite3.connect(self.cache_file_name)
            c = conn.cursor()
            c.execute("""
                CREATE TABLE IF NOT EXISTS cache (
                    key TEXT PRIMARY KEY,
                    message TEXT,
                    metadata TEXT
                )
            """)
            metadata_str = json.dumps(metadata)
            c.execute("INSERT OR REPLACE INTO cache (key, message, metadata) VALUES (?, ?, ?)",
                      (key_hash, message, metadata_str))
            conn.commit()
            conn.close()

        return message, metadata, False

    return wrapper


def dynamic_retry_decorator(func):
    """动态重试装饰器"""
    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        max_retries = getattr(self, "max_retries", 5)
        last_exception = None

        for attempt in range(max_retries):
            try:
                return func(self, *args, **kwargs)
            except Exception as e:
                last_exception = e
                if attempt < max_retries - 1:
                    logger.warning(f"Attempt {attempt + 1}/{max_retries} failed: {e}. Retrying...")
                    import time
                    time.sleep(1)
                else:
                    logger.error(f"All {max_retries} attempts failed.")

        raise last_exception

    return wrapper


class RequestsLLM(BaseLLM):
    """使用 requests 库的 OpenAI 兼容 LLM 实现"""

    @classmethod
    def from_experiment_config(cls, global_config: BaseConfig, **kwargs) -> "RequestsLLM":
        """从配置创建 RequestsLLM 实例"""
        cache_namespace = kwargs.pop("cache_namespace", "default")
        cache_dir = os.path.join(global_config.save_dir, "llm_cache", cache_namespace)
        return cls(cache_dir=cache_dir, global_config=global_config, **kwargs)

    def __init__(self, cache_dir, global_config, cache_filename: str = None, **kwargs) -> None:
        super().__init__()
        self.cache_dir = cache_dir
        self.global_config = global_config

        self.llm_name = global_config.llm_name
        self.llm_base_url = global_config.llm_base_url
        self.requires_api_key = (
            global_config.azure_endpoint is None
            and not _is_local_base_url(self.llm_base_url)
        )

        os.makedirs(self.cache_dir, exist_ok=True)
        if cache_filename is None:
            cache_filename = f"{self.llm_name.replace('/', '_')}_cache.sqlite"
        self.cache_file_name = os.path.join(self.cache_dir, cache_filename)

        self._init_llm_config()

        # 初始化 requests session
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
        })

        self.max_retries = kwargs.pop("max_retries", 2)
        self.timeout_seconds = kwargs.pop("timeout_seconds", 300)
        self.api_key = kwargs.pop("api_key", None)

        if self.api_key:
            self.session.headers.update({
                "Authorization": f"Bearer {self.api_key}"
            })

        logger.info(f"Initialized {self.__class__.__name__} with model: {self.llm_name}")

    def _init_llm_config(self) -> None:
        """初始化 LLM 配置"""
        config_dict = dict(self.global_config.__dict__)

        config_dict['llm_name'] = self.global_config.llm_name
        config_dict['llm_base_url'] = self.global_config.llm_base_url
        config_dict['generate_params'] = {
            "model": self.global_config.llm_name.replace('requests/', ''),  # 移除 'requests/' 前缀
            "max_tokens": config_dict.get("max_new_tokens", 2048),
            "n": config_dict.get("num_gen_choices", 1),
            "seed": config_dict.get("seed", None),
            "temperature": config_dict.get("temperature", 0.0),
        }

        self.llm_config = LLMConfig.from_dict(config_dict=config_dict)
        logger.debug(f"Init {self.__class__.__name__}'s llm_config: {self.llm_config}")

    @cache_response
    @dynamic_retry_decorator
    def infer(
        self,
        messages: List[TextChatMessage],
        **kwargs
    ) -> Tuple[str, dict]:
        """
        执行同步推理

        Args:
            messages: 消息列表
            **kwargs: 额外参数（可覆盖 generate_params）

        Returns:
            (response_text, metadata): 响应文本和元数据
        """
        if self.requires_api_key and not self.api_key:
            raise RuntimeError(
                "No OpenAI-compatible API key detected. "
                "Export SYNAPSERAG_OPENAI_API_KEY, OPENAI_API_KEY, or OPENROUTER_API_KEY "
                "when using hosted endpoints, or point `llm_base_url` to a localhost deployment."
            )

        # 准备请求参数
        params = deepcopy(self.llm_config.generate_params)
        if kwargs:
            params.update(kwargs)
        params["messages"] = messages

        # 确保移除 model 参数中的 'requests/' 前缀
        if "model" in params and isinstance(params["model"], str):
            params["model"] = params["model"].replace("requests/", "")

        # 处理 response_format 参数
        response_format = params.pop("response_format", None)
        if response_format:
            # 某些 API 可能不支持 response_format，这里尝试添加
            try:
                params["response_format"] = response_format
            except Exception as e:
                logger.warning(f"API may not support response_format: {e}")

        logger.debug(f"Calling RequestsLLM API with:\n{json.dumps(params, indent=2, ensure_ascii=False)}")

        # 构建请求 URL
        base_url = self.llm_base_url.rstrip("/")
        if not base_url.endswith("/chat/completions"):
            url = f"{base_url}/chat/completions"
        else:
            url = base_url

        # 发送 HTTP 请求
        try:
            response = self.session.post(
                url,
                json=params,
                timeout=self.timeout_seconds
            )
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            logger.error(f"HTTP request failed: {e}")
            if hasattr(e.response, 'text'):
                logger.error(f"Response text: {e.response.text}")
            raise

        # 解析响应
        try:
            response_data = response.json()
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON response: {e}")
            logger.error(f"Response text: {response.text}")
            raise

        # 提取响应内容
        try:
            response_message = response_data["choices"][0]["message"]["content"]
            assert isinstance(response_message, str), "response_message should be a string"

            metadata = {
                "prompt_tokens": response_data.get("usage", {}).get("prompt_tokens", 0),
                "completion_tokens": response_data.get("usage", {}).get("completion_tokens", 0),
                "finish_reason": response_data["choices"][0].get("finish_reason", "stop"),
            }

            logger.debug(f"Received response: {response_message[:100]}...")
            return response_message, metadata

        except (KeyError, IndexError, TypeError) as e:
            logger.error(f"Failed to extract response content: {e}")
            logger.error(f"Response data: {json.dumps(response_data, indent=2)}")
            raise

    def __del__(self):
        """清理 requests session"""
        if hasattr(self, 'session'):
            self.session.close()
