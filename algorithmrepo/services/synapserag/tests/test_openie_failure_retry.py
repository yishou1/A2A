import json
import tempfile
import unittest
from pathlib import Path

from src.synapserag import SynapseRAG
from src.synapserag.utils.config_utils import BaseConfig, LLMEndpointConfig
from src.synapserag.utils.misc_utils import NerRawOutput, TripleRawOutput


class PartialOpenIE:
    def __init__(self):
        self.calls = []
        self.fail_once = True

    def batch_openie(self, rows):
        self.calls.append(set(rows))
        ner = {}
        triples = {}
        for position, chunk_id in enumerate(rows):
            failed = self.fail_once and position == 0
            metadata = {"error": "temporary provider error"} if failed else {}
            ner[chunk_id] = NerRawOutput(chunk_id, "", ["实体"], metadata)
            triples[chunk_id] = TripleRawOutput(chunk_id, "", [["实体", "关系", "对象"]], metadata)
        self.fail_once = False
        return ner, triples


class OpenIEFailureRetryTests(unittest.TestCase):
    def test_successful_chunks_are_cached_and_retry_only_processes_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            config = BaseConfig(
                save_dir=directory,
                index_id="partial-openie",
                runtime_stage="openie",
                openie_prompt_version="ner-triple-cn-v2",
                openie_llm=LLMEndpointConfig(model_name="test-openie"),
            )
            rag = SynapseRAG(global_config=config)
            fake = PartialOpenIE()
            rag._ensure_openie_runtime = lambda: fake
            docs = ["第一段。", "第二段。"]

            with self.assertRaisesRegex(RuntimeError, "OpenIE failed for 1 chunk"):
                rag.extract_openie(docs)
            cached = json.loads(Path(rag.openie_results_path).read_text(encoding="utf-8"))
            self.assertEqual(len(cached["docs"]), 1)

            report = rag.extract_openie(docs)
            self.assertEqual(report["processed_count"], 1)
            self.assertEqual(report["reused_count"], 1)
            self.assertEqual(len(fake.calls[1]), 1)


if __name__ == "__main__":
    unittest.main()
