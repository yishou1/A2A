"""Chat client shared by A2A agents."""

from __future__ import annotations

import json

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

    def chat(self, *, system_prompt: str, user_prompt: str) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0,
        }
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
        content = self.chat(system_prompt=system_prompt, user_prompt=user_prompt)
        try:
            parsed = json.loads(_strip_json_fence(content))
        except json.JSONDecodeError as exc:
            raise LLMClientError("LLM content is not valid JSON.") from exc
        if not isinstance(parsed, dict):
            raise LLMClientError("LLM JSON content must be an object.")
        return parsed


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
