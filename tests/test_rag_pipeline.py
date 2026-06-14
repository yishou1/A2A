import os
import unittest
from unittest.mock import patch

from decision_agents.rag.documents import load_rag_chunks
from decision_agents.rag.pipeline import run_rag


class RagPipelineTest(unittest.TestCase):
    def test_markdown_knowledge_base_loads_rule_chunks(self):
        chunks = load_rag_chunks()

        self.assertTrue(chunks)
        self.assertTrue(any(chunk.rule_id == "AUTH-STATE-PENDING" for chunk in chunks))
        first = chunks[0]
        self.assertTrue(first.rule_id)
        self.assertIsInstance(first.tags, tuple)
        self.assertTrue(first.text)

    def test_disabled_local_models_fall_back_to_keyword_retrieval(self):
        env = {
            **os.environ,
            "ENABLE_LOCAL_RAG_MODELS": "false",
        }
        with patch.dict(os.environ, env, clear=True):
            result = run_rag(
                "pending authorization decision-support review",
                purpose="compliance",
                top_k=3,
            )

        self.assertTrue(result.evidence)
        self.assertTrue(result.answer)
        self.assertFalse(result.model_profile["enabled"])
        self.assertTrue(
            any("local_rag_models_disabled" in warning for warning in result.warnings)
        )


if __name__ == "__main__":
    unittest.main()
