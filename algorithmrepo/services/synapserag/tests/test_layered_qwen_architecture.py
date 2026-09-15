import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from pydantic import TypeAdapter, ValidationError

from src.synapserag.SynapseRAG import SynapseRAG
from src.synapserag.embedding_model.OpenAI import OpenAIEmbeddingModel
from src.synapserag.embedding_model import _get_embedding_model_class
from src.synapserag.llm import _resolve_api_key, create_llm
from src.synapserag.llm.base import LLMResponse, coerce_llm_response
from src.synapserag.rerank import DSPyFilter, RerankSelection
from src.synapserag.utils.config_utils import BaseConfig, LLMEndpointConfig
from src.synapserag.utils.misc_utils import compute_mdhash_id


def local_endpoint(model="qwen3:1.7b"):
    return LLMEndpointConfig(
        model_name=model,
        base_url="http://127.0.0.1:11434/v1",
    )


class LayeredQwenArchitectureTests(unittest.TestCase):
    def test_ollama_qwen_embedding_name_routes_to_openai_client(self):
        self.assertIs(
            _get_embedding_model_class("qwen3-embedding:0.6b"),
            OpenAIEmbeddingModel,
        )

    def test_role_key_precedence_and_local_dummy(self):
        config = BaseConfig(qa_llm=local_endpoint())
        with patch.dict(os.environ, {
            "OPENAI_API_KEY": "generic",
            "SYNAPSERAG_QA_API_KEY": "role-key",
        }, clear=True):
            self.assertEqual(_resolve_api_key(config, role="qa"), "role-key")
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(_resolve_api_key(config, role="qa"), "sk-local")

    def test_azure_role_passes_endpoint_version_and_key(self):
        with tempfile.TemporaryDirectory() as directory:
            endpoint = LLMEndpointConfig(
                model_name="gpt-4o-mini",
                provider="azure",
                api_key_env="TEST_AZURE_KEY",
                azure_endpoint="https://example.openai.azure.com",
                api_version="2024-12-01-preview",
            )
            config = BaseConfig(save_dir=directory, openie_llm=endpoint)
            with patch.dict(os.environ, {"TEST_AZURE_KEY": "secret"}, clear=True), patch(
                "src.synapserag.llm.openai_gpt.AzureOpenAI"
            ) as azure_client:
                create_llm(endpoint, config, role="openie")
            kwargs = azure_client.call_args.kwargs
            self.assertEqual(kwargs["azure_endpoint"], endpoint.azure_endpoint)
            self.assertEqual(kwargs["api_version"], endpoint.api_version)
            self.assertEqual(kwargs["api_key"], "secret")

    def test_top_k_phrase_weights_skip_candidates_missing_from_graph(self):
        rag = object.__new__(SynapseRAG)
        known_key = compute_mdhash_id(content="known", prefix="entity-")
        rag.node_name_to_vertex_idx = {known_key: 1}
        weights, scores = rag.get_top_k_weights(
            link_top_k=1,
            all_phrase_weights=np.asarray([0.0, 0.5]),
            linking_score_map={"missing": 1.0, "known": 0.5},
        )
        np.testing.assert_array_equal(weights, np.asarray([0.0, 0.5]))
        self.assertEqual(scores, {"known": 0.5})

    def test_legacy_responses_are_normalized(self):
        response = coerce_llm_response(("answer", {"tokens": 3}))
        self.assertEqual(response, LLMResponse("answer", {"tokens": 3}, False))
        self.assertEqual(tuple(response), ("answer", {"tokens": 3}, False))

    def test_qa_model_does_not_change_index_path(self):
        with tempfile.TemporaryDirectory() as directory:
            common = dict(
                save_dir=directory,
                index_id="stable-kb-v1",
                runtime_stage="openie",
                embedding_model_name="Qwen/Qwen3-Embedding-0.6B",
            )
            first = SynapseRAG(global_config=BaseConfig(
                **common, qa_llm=local_endpoint("qwen3:1.7b")))
            second = SynapseRAG(global_config=BaseConfig(
                **common, qa_llm=local_endpoint("another-local-model")))
            self.assertEqual(first.working_dir, second.working_dir)

    def test_openie_stage_does_not_initialize_embedding(self):
        with tempfile.TemporaryDirectory() as directory:
            config = BaseConfig(
                save_dir=directory,
                index_id="openie-only",
                runtime_stage="openie",
                openie_llm=LLMEndpointConfig(model_name="cached-openie"),
                embedding_model_name="Qwen/Qwen3-Embedding-0.6B",
            )
            rag = SynapseRAG(global_config=config)
            self.assertIsNone(rag.embedding_model)

            docs = ["顾明澈创办了青岚研究所。"]
            chunk_id = next(iter(rag._make_chunk_rows(docs)))
            with open(rag.openie_results_path, "w", encoding="utf-8") as file:
                json.dump({
                    "schema_version": 2,
                    "index_id": config.index_id,
                    "source_model": "cached-openie",
                    "prompt_version": "test-v1",
                    "docs": [{
                        "idx": chunk_id,
                        "passage": docs[0],
                        "extracted_entities": ["顾明澈", "青岚研究所"],
                        "extracted_triples": [["顾明澈", "创办", "青岚研究所"]],
                    }],
                }, file, ensure_ascii=False)

            report = rag.extract_openie(docs)
            self.assertEqual(report["processed_count"], 0)
            self.assertIsNone(rag.embedding_model)
            self.assertIsNone(rag.openie_llm)

    def test_build_index_checks_openie_before_writing_vectors(self):
        with tempfile.TemporaryDirectory() as directory:
            config = BaseConfig(
                save_dir=directory,
                index_id="missing-openie",
                runtime_stage="build-index",
                embedding_model_name="Qwen/Qwen3-Embedding-0.6B",
                embedding_base_url="http://127.0.0.1:9999/v1",
            )
            rag = SynapseRAG(global_config=config)
            with self.assertRaisesRegex(RuntimeError, "OpenIE cache is missing 1 documents"):
                rag.build_index(["不会发起 embedding 请求。"], allow_openie_calls=False)
            parquet = (
                Path(directory) / "indexes" / "missing-openie" /
                "Qwen_Qwen3-Embedding-0.6B" / "chunk_embeddings" /
                "vdb_chunk.parquet"
            )
            self.assertFalse(parquet.exists())

    def test_qwen_rerank_parser_is_strict_and_cleans_response(self):
        reranker = object.__new__(DSPyFilter)
        reranker.response_adapter = TypeAdapter(RerankSelection)
        response = '<think>hidden</think>```json\n{"selected_ids":[2,0,2]}\n```'
        self.assertEqual(
            reranker.parse_filter(response, candidate_count=3, limit=3), [2, 0])
        with self.assertRaises(ValueError):
            reranker.parse_filter('{"selected_ids":[4]}', candidate_count=3, limit=3)
        with self.assertRaises(ValidationError):
            reranker.parse_filter('{"selected_ids":["1"]}', candidate_count=3, limit=3)

    def test_invalid_llm_rerank_falls_back_to_embedding_order(self):
        class Store:
            @staticmethod
            def get_rows(ids):
                return {
                    fact_id: {"content": str((fact_id, "关系", "对象"))}
                    for fact_id in ids
                }

        class BrokenReranker:
            def __call__(self, *args, **kwargs):
                return [], [], {"mode": "llm", "error": "invalid_json"}

        rag = object.__new__(SynapseRAG)
        rag.global_config = BaseConfig(
            fact_rerank_mode="hybrid",
            fact_candidate_top_k=3,
            fact_rerank_top_k=2,
            linking_top_k=2,
            fact_rerank_fallback="embedding",
        )
        rag.fact_node_keys = ["f0", "f1", "f2"]
        rag.fact_embedding_store = Store()
        rag.rerank_filter = BrokenReranker()

        indices, facts, metadata = rag.rerank_facts(
            "问题", np.asarray([0.1, 0.9, 0.5]))
        self.assertEqual(indices, [1, 2])
        self.assertEqual([fact[0] for fact in facts], ["f1", "f2"])
        self.assertEqual(metadata["mode"], "hybrid")
        self.assertEqual(metadata["fallback"], "embedding")

    def test_openai_embedding_prefix_instruction_and_shape_validation(self):
        class Item:
            def __init__(self, embedding):
                self.embedding = embedding

        class Embeddings:
            def __init__(self):
                self.last_input = None

            def create(self, *, input, model, **kwargs):
                self.last_input = input
                response = type("Response", (), {})()
                response.data = [Item([1.0, 2.0]) for _ in input]
                return response

        model = object.__new__(OpenAIEmbeddingModel)
        model.global_config = BaseConfig(embedding_query_instruction_mode="prefix")
        model.embedding_model_name = "Qwen/Qwen3-Embedding-0.6B"
        model.client = type("Client", (), {"embeddings": Embeddings()})()
        vectors = model.encode(["苏州"], instruction="检索相关事实")
        self.assertEqual(vectors.shape, (1, 2))
        self.assertEqual(
            model.client.embeddings.last_input,
            ["Instruct: 检索相关事实\nQuery: 苏州"],
        )


if __name__ == "__main__":
    unittest.main()
