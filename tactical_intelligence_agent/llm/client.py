"""OpenAI 兼容小模型客户端。"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class LLMClientError(RuntimeError):
    pass


@dataclass(frozen=True)
class ToolLLMSettings:
    enable: bool = False
    url: str = ""
    name: str = ""
    api_key: str = "EMPTY"
    provider: str = "openai_compatible"
    azure_api_version: str = "2024-12-01-preview"
    timeout_seconds: float = 30.0
    max_tokens: int | None = None
    temperature: float = 0.0
    json_mode: bool = False
    strip_thinking: bool = True
    json_retry_count: int = 0
    reasoning_effort: str = ""
    allowed_models: tuple[str, ...] = ()

    @classmethod
    def from_config(cls, cfg: dict[str, Any] | None = None) -> ToolLLMSettings:
        tool = dict((cfg or {}).get("tool_llm") or {})
        env_enable = os.environ.get("ENABLE_LLM", "").strip().lower()
        enable = env_enable == "true" if env_enable else bool(tool.get("enable", False))
        allowed_raw = os.environ.get("TOOL_LLM_ALLOWED_MODELS", "")
        if allowed_raw.strip():
            allowed = tuple(x.strip() for x in allowed_raw.split(",") if x.strip())
        else:
            allowed = tuple(tool.get("allowed_models") or [])

        max_tokens_env = os.environ.get("LLM_MAX_TOKENS", "").strip()
        max_tokens = (
            int(max_tokens_env)
            if max_tokens_env
            else (int(tool["max_tokens"]) if tool.get("max_tokens") is not None else None)
        )

        return cls(
            enable=enable,
            url=os.environ.get("TOOL_LLM_URL") or str(tool.get("url") or ""),
            name=os.environ.get("TOOL_LLM_NAME") or str(tool.get("name") or ""),
            api_key=os.environ.get("API_KEY") or str(tool.get("api_key") or "EMPTY"),
            provider=(
                os.environ.get("LLM_PROVIDER") or str(tool.get("provider") or "openai_compatible")
            ).lower(),
            azure_api_version=os.environ.get("AZURE_OPENAI_API_VERSION")
            or str(tool.get("azure_api_version") or "2024-12-01-preview"),
            timeout_seconds=float(
                os.environ.get("LLM_TIMEOUT_SECONDS") or tool.get("timeout_seconds") or 30
            ),
            max_tokens=max_tokens,
            temperature=float(os.environ.get("LLM_TEMPERATURE") or tool.get("temperature") or 0),
            json_mode=(
                os.environ.get("LLM_JSON_MODE", "").lower() == "true"
                if os.environ.get("LLM_JSON_MODE")
                else bool(tool.get("json_mode", False))
            ),
            strip_thinking=(
                os.environ.get("LLM_STRIP_THINKING", "true").lower() == "true"
                if os.environ.get("LLM_STRIP_THINKING")
                else bool(tool.get("strip_thinking", True))
            ),
            json_retry_count=max(
                0,
                int(os.environ.get("LLM_JSON_RETRY_COUNT") or tool.get("json_retry_count") or 0),
            ),
            reasoning_effort=os.environ.get("LLM_REASONING_EFFORT")
            or str(tool.get("reasoning_effort") or ""),
            allowed_models=allowed,
        )


class OpenAICompatibleClient:
    def __init__(self, settings: ToolLLMSettings, *, model: str | None = None) -> None:
        if not settings.url:
            raise LLMClientError("TOOL_LLM_URL is required when ENABLE_LLM=true.")
        resolved = model or settings.name
        if not resolved:
            raise LLMClientError("TOOL_LLM_NAME is required when ENABLE_LLM=true.")
        if settings.allowed_models and resolved not in settings.allowed_models:
            raise LLMClientError(f"LLM model is not allowed: {resolved}")
        self.base_url = settings.url.rstrip("/")
        self.model = resolved
        self.api_key = settings.api_key or "EMPTY"
        self.timeout = settings.timeout_seconds
        self.provider = settings.provider
        self.azure_api_version = settings.azure_api_version
        self.max_tokens = settings.max_tokens
        self.temperature = settings.temperature
        self.json_mode = settings.json_mode
        self.strip_thinking = settings.strip_thinking
        self.json_retry_count = settings.json_retry_count
        self.reasoning_effort = settings.reasoning_effort

    def chat(self, *, system_prompt: str, user_prompt: str) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.temperature,
        }
        if self.max_tokens is not None:
            payload["max_tokens"] = self.max_tokens
        if self.json_mode:
            payload["response_format"] = {"type": "json_object"}
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort
        url, headers = self._request_target()
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(url, data=body, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise LLMClientError(f"LLM request failed: HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise LLMClientError(f"LLM request failed: {exc}") from exc
        except ValueError as exc:
            raise LLMClientError("LLM returned invalid JSON response.") from exc
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMClientError("LLM response does not contain message content.") from exc

    def chat_json(self, *, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        prompt = user_prompt
        failure = "LLM content is not valid JSON."
        for attempt in range(self.json_retry_count + 1):
            content = self.chat(system_prompt=system_prompt, user_prompt=prompt)
            text = _strip_model_wrappers(content, strip_thinking=self.strip_thinking)
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                failure = "LLM content is not valid JSON."
            else:
                if isinstance(parsed, dict):
                    return parsed
                failure = "LLM JSON content must be an object."
            if attempt < self.json_retry_count:
                prompt = _json_retry_prompt(user_prompt, content)
        raise LLMClientError(failure)

    def _request_target(self) -> tuple[str, dict[str, str]]:
        headers = {"Content-Type": "application/json"}
        if self._is_azure_provider():
            headers["api-key"] = self.api_key
            if "/chat/completions" in self.base_url:
                return self.base_url, headers
            return (
                f"{self.base_url}/openai/deployments/{self.model}/chat/completions"
                f"?api-version={self.azure_api_version}",
                headers,
            )
        headers["Authorization"] = f"Bearer {self.api_key}"
        return f"{self.base_url}/chat/completions", headers

    def _is_azure_provider(self) -> bool:
        if self.provider in {"azure", "azure_openai"}:
            return True
        return ".openai.azure.com" in self.base_url


_THINK_BLOCK_RE = re.compile(r"<think\b[^>]*>.*?</think>", re.IGNORECASE | re.DOTALL)


def _strip_model_wrappers(content: str, *, strip_thinking: bool) -> str:
    text = content.strip()
    if strip_thinking:
        text = _THINK_BLOCK_RE.sub("", text).strip()
        if text.lower().startswith("<think") and "</think>" not in text.lower():
            object_start = text.find("{")
            if object_start >= 0:
                text = text[object_start:].strip()
    return _strip_json_fence(text)


def _json_retry_prompt(original_prompt: str, invalid_content: str) -> str:
    preview = invalid_content.strip()[:1000]
    return (
        f"{original_prompt}\n\n"
        "The previous response was not one valid JSON object. "
        "Return only the JSON object required by the system prompt. "
        "Do not include Markdown fences, thinking text, or commentary.\n\n"
        f"Previous response:\n{preview}"
    )


def _strip_json_fence(content: str) -> str:
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text
