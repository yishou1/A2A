import os
import unittest

from unittest.mock import patch

from decision_agents.common.config import get_settings
from llm.client import LLMClientError, OpenAICompatibleClient, _strip_model_wrappers


class FakeResponse:
    def __init__(self, content: str):
        self._content = content

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"choices": [{"message": {"content": self._content}}]}


class LLMClientTest(unittest.TestCase):
    def setUp(self) -> None:
        self._env = os.environ.copy()
        os.environ.update(
            {
                "LLM_PROVIDER": "openai_compatible",
                "TOOL_LLM_URL": "http://127.0.0.1:11434/v1",
                "TOOL_LLM_NAME": "qwen3:1.7b",
                "API_KEY": "ollama",
                "LLM_MAX_TOKENS": "512",
                "LLM_TEMPERATURE": "0.1",
                "LLM_JSON_MODE": "true",
                "LLM_STRIP_THINKING": "true",
                "LLM_JSON_RETRY_COUNT": "1",
                "LLM_REASONING_EFFORT": "none",
            }
        )

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env)

    @patch("llm.client.httpx.post")
    def test_ollama_payload_uses_json_and_non_thinking_controls(self, post) -> None:
        post.return_value = FakeResponse('{"ok": true}')

        result = OpenAICompatibleClient(get_settings()).chat_json(
            system_prompt="Return JSON.",
            user_prompt="Return ok.",
        )

        self.assertEqual(result, {"ok": True})
        _, kwargs = post.call_args
        self.assertEqual(post.call_args.args[0], "http://127.0.0.1:11434/v1/chat/completions")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer ollama")
        self.assertEqual(kwargs["json"]["max_tokens"], 512)
        self.assertEqual(kwargs["json"]["temperature"], 0.1)
        self.assertEqual(kwargs["json"]["response_format"], {"type": "json_object"})
        self.assertEqual(kwargs["json"]["reasoning_effort"], "none")

    def test_strip_model_wrappers_removes_thinking_and_fence(self) -> None:
        content = '<think>private reasoning</think>\n```json\n{"ok": true}\n```'

        self.assertEqual(
            _strip_model_wrappers(content, strip_thinking=True),
            '{"ok": true}',
        )

    @patch("llm.client.httpx.post")
    def test_chat_json_retries_invalid_model_output(self, post) -> None:
        post.side_effect = [FakeResponse("not json"), FakeResponse('{"ok": true}')]

        result = OpenAICompatibleClient(get_settings()).chat_json(
            system_prompt="Return JSON.",
            user_prompt="Return ok.",
        )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(post.call_count, 2)
        retry_payload = post.call_args.kwargs["json"]
        self.assertIn("previous response", retry_payload["messages"][1]["content"].lower())

    @patch("llm.client.httpx.post")
    def test_chat_json_raises_after_retry_limit(self, post) -> None:
        post.return_value = FakeResponse("not json")

        with self.assertRaisesRegex(LLMClientError, "not valid JSON"):
            OpenAICompatibleClient(get_settings()).chat_json(
                system_prompt="Return JSON.",
                user_prompt="Return ok.",
            )

        self.assertEqual(post.call_count, 2)


if __name__ == "__main__":
    unittest.main()
