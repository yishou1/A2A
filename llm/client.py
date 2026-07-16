"""Chat client shared by A2A agents."""

from __future__ import annotations

import json
import re

from typing import Any

import httpx

from decision_agents.common.config import Settings


class LLMClientError(RuntimeError):
    pass


class OpenAICompatibleClient:
    def __init__(self, settings: Settings, *, model: str | None = None) -> None:
        if not settings.tool_llm_url:
            raise LLMClientError("TOOL_LLM_URL is required when ENABLE_LLM=true.")
        resolved_model = model or settings.tool_llm_name
        if not resolved_model:
            raise LLMClientError("TOOL_LLM_NAME is required when ENABLE_LLM=true.")
        self.base_url = settings.tool_llm_url.rstrip("/")
        self.model = resolved_model
        self.api_key = settings.api_key or "EMPTY"
        self.timeout = settings.llm_timeout_seconds
        self.provider = settings.llm_provider
        self.azure_api_version = settings.azure_openai_api_version
        self.max_tokens = settings.llm_max_tokens
        self.temperature = settings.llm_temperature
        self.json_mode = settings.llm_json_mode
        self.strip_thinking = settings.llm_strip_thinking
        self.json_retry_count = settings.llm_json_retry_count
        self.reasoning_effort = settings.llm_reasoning_effort

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
        try:
            response = httpx.post(
                url,
                headers=headers,
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            raise LLMClientError(f"LLM request failed: {exc}") from exc
        except ValueError as exc:
            raise LLMClientError("LLM returned invalid JSON response.") from exc

        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMClientError("LLM response does not contain message content.") from exc

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

    def chat_json(self, *, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        prompt = user_prompt
        failure = "LLM content is not valid JSON."
        for attempt in range(self.json_retry_count + 1):
            content = self.chat(system_prompt=system_prompt, user_prompt=prompt)
            text = _strip_model_wrappers(
                content,
                strip_thinking=self.strip_thinking,
            )
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
