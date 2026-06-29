"""Small OpenAI-compatible chat client used by the optional LLM layer."""

from __future__ import annotations

import json

from typing import Any

import httpx

from decision_agents.config import Settings


class LLMClientError(RuntimeError):
    pass


class OpenAICompatibleClient:
    def __init__(self, settings: Settings) -> None:
        if not settings.tool_llm_url:
            raise LLMClientError("TOOL_LLM_URL is required when ENABLE_LLM=true.")
        if not settings.tool_llm_name:
            raise LLMClientError("TOOL_LLM_NAME is required when ENABLE_LLM=true.")
        self.base_url = settings.tool_llm_url.rstrip("/")
        self.model = settings.tool_llm_name
        self.api_key = settings.api_key or "EMPTY"
        self.timeout = settings.llm_timeout_seconds

    def chat(self, *, system_prompt: str, user_prompt: str) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0,
        }
        try:
            response = httpx.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
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

