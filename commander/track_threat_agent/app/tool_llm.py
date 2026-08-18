"""Small-model JSON planner for selecting Track Threat algorithms.

The model only proposes algorithm identifiers from a supplied catalog. It never
constructs URLs or executes algorithms; the runtime validates and executes the
plan separately.
"""

from __future__ import annotations

import json
from typing import Any, Callable
from urllib import error, request


class ToolLLMError(RuntimeError):
    pass


LLMTransport = Callable[[str, dict[str, str], dict[str, Any], float], dict[str, Any]]


class AzureToolLLM:
    """OpenAI-compatible chat-completions client returning one strict JSON plan."""

    def __init__(self, settings: Any, *, transport: LLMTransport | None = None) -> None:
        self.endpoint = str(settings.llm_endpoint).rstrip("/")
        self.deployment = str(settings.llm_deployment).strip()
        self.api_version = str(settings.llm_api_version).strip()
        self.api_key = str(settings.llm_api_key).strip()
        self.provider = str(getattr(settings, "llm_provider", "azure_openai")).strip().lower()
        self.timeout_seconds = float(settings.llm_timeout_seconds)
        self.transport = transport or _post_json

    def plan(
        self,
        *,
        requested_skills: list[str],
        algorithms: list[dict[str, Any]],
        request_summary: dict[str, Any],
    ) -> dict[str, Any]:
        if not self.endpoint:
            raise ToolLLMError("LLM endpoint is not configured")
        if not self.deployment:
            raise ToolLLMError("LLM chat model/deployment is not configured")
        if not self.api_key:
            raise ToolLLMError("LLM API key is not configured")

        system_prompt = (
            "You are the tool planner inside the Track Threat Agent. Return one valid JSON object only. "
            "Choose only algorithms present in the supplied active catalog. Do not invent outputs, URLs, "
            "versions, backend types, tactical conclusions, attack advice, weapon control, guidance, or "
            "engagement decisions. Select the smallest ordered set needed for the requested skills. "
            "The required shape is: "
            '{"intent":"short intent","algorithm_calls":[{"algorithm_id":"id",'
            '"version":"version","backend_type":"python_http_service","reason":"reason"}],'
            '"explanation":"short Chinese explanation"}.'
        )
        user_prompt = json.dumps(
            {
                "agent": "track_threat_agent",
                "requested_skills": requested_skills,
                "active_algorithm_catalog": algorithms,
                "request_summary": request_summary,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        payload = {
            "model": self.deployment,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        response = self.transport(
            self._chat_completions_url(),
            self._headers(),
            payload,
            self.timeout_seconds,
        )
        try:
            content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ToolLLMError("LLM response does not contain message content") from exc
        try:
            parsed = json.loads(_strip_json_fence(str(content)))
        except json.JSONDecodeError as exc:
            raise ToolLLMError("LLM response is not valid JSON") from exc
        if not isinstance(parsed, dict):
            raise ToolLLMError("LLM plan must be a JSON object")
        return parsed

    def _chat_completions_url(self) -> str:
        if "/chat/completions" in self.endpoint:
            return self.endpoint
        if not self._is_azure_provider():
            return f"{self.endpoint}/chat/completions"
        return (
            f"{self.endpoint}/openai/deployments/{self.deployment}/chat/completions"
            f"?api-version={self.api_version}"
        )

    def _headers(self) -> dict[str, str]:
        if self._is_azure_provider():
            return {"Content-Type": "application/json", "api-key": self.api_key}
        return {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}

    def _is_azure_provider(self) -> bool:
        provider = str(getattr(self, "provider", "")).lower()
        return provider in {"azure", "azure_openai"} or ".openai.azure.com" in self.endpoint


def _post_json(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout_seconds: float,
) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(url, data=body, headers=headers, method="POST")
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:
            result = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise ToolLLMError(f"Azure OpenAI HTTP {exc.code}: {detail}") from exc
    except (error.URLError, TimeoutError, OSError) as exc:
        raise ToolLLMError(f"Azure OpenAI request failed: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ToolLLMError("Azure OpenAI endpoint returned invalid JSON") from exc
    if not isinstance(result, dict):
        raise ToolLLMError("Azure OpenAI endpoint returned a non-object JSON value")
    return result


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
