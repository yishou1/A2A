import json
import os
import logging
import hashlib
import ast
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Union, Optional, List, Set, Dict, Any, Tuple, Literal
import numpy as np
import importlib
from collections import defaultdict
from transformers import HfArgumentParser
from concurrent.futures import ThreadPoolExecutor
from tqdm import tqdm
from igraph import Graph
import igraph as ig
import numpy as np
from collections import defaultdict
import re
import time
import math
from collections import Counter

from .llm import _get_llm_class, create_llm, BaseLLM
from .embedding_model import _get_embedding_model_class, BaseEmbeddingModel
from .embedding_store import EmbeddingStore
from .information_extraction import OpenIE
try:
    from .information_extraction.openie_vllm_offline import VLLMOfflineOpenIE
    from .information_extraction.openie_transformers_offline import TransformersOfflineOpenIE
except Exception:
    VLLMOfflineOpenIE = None
    TransformersOfflineOpenIE = None
from .evaluation.retrieval_eval import RetrievalRecall
from .evaluation.qa_eval import QAExactMatch, QAF1Score
from .prompts.linking import get_query_instruction
from .prompts.prompt_template_manager import PromptTemplateManager
from .agentic.ppr import AgenticGraphSearch
from .agentic.ppr.logging_utils import AgenticLogOptions
from .rerank import DSPyFilter
from .utils.misc_utils import *
from .utils.misc_utils import NerRawOutput, TripleRawOutput
from .utils.embed_utils import retrieve_knn
from .utils.typing import Triple
from .utils.config_utils import BaseConfig
from .tracing import RetrievalTraceCollector

logger = logging.getLogger(__name__)

class SynapseRAG:

    def __init__(self,
                 global_config=None,
                 save_dir=None,
                 llm_model_name=None,
                 llm_base_url=None,
                 embedding_model_name=None,
                 embedding_base_url=None,
                 azure_endpoint=None,
                 azure_embedding_endpoint=None):
        """
        Initializes an instance of the class and its related components.

        Attributes:
            global_config (BaseConfig): The global configuration settings for the instance. An instance
                of BaseConfig is used if no value is provided.
            saving_dir (str): The directory where specific SynapseRAG instances will be stored. This defaults
                to `outputs` if no value is provided.
            llm_model (BaseLLM): The language model used for processing based on the global
                configuration settings.
            openie (Union[OpenIE, VLLMOfflineOpenIE]): The Open Information Extraction module
                configured in either online or offline mode based on the global settings.
            graph: The graph instance initialized by the `initialize_graph` method.
            embedding_model (BaseEmbeddingModel): The embedding model associated with the current
                configuration.
            chunk_embedding_store (EmbeddingStore): The embedding store handling chunk embeddings.
            entity_embedding_store (EmbeddingStore): The embedding store handling entity embeddings.
            fact_embedding_store (EmbeddingStore): The embedding store handling fact embeddings.
            prompt_template_manager (PromptTemplateManager): The manager for handling prompt templates
                and roles mappings.
            openie_results_path (str): The file path for storing Open Information Extraction results
                based on the dataset and LLM name in the global configuration.
            rerank_filter (Optional[DSPyFilter]): The filter responsible for reranking information
                when a rerank file path is specified in the global configuration.
            ready_to_retrieve (bool): A flag indicating whether the system is ready for retrieval
                operations.

        Parameters:
            global_config: The global configuration object. Defaults to None, leading to initialization
                of a new BaseConfig object.
            working_dir: The directory for storing working files. Defaults to None, constructing a default
                directory based on the class name and timestamp.
            llm_model_name: LLM model name, can be inserted directly as well as through configuration file.
            embedding_model_name: Embedding model name, can be inserted directly as well as through configuration file.
            llm_base_url: LLM URL for a deployed LLM model, can be inserted directly as well as through configuration file.
        """
        if global_config is None:
            self.global_config = BaseConfig()
        else:
            self.global_config = global_config

        #Overwriting Configuration if Specified
        if save_dir is not None:
            self.global_config.save_dir = save_dir

        if llm_model_name is not None:
            self.global_config.llm_name = llm_model_name

        if embedding_model_name is not None:
            self.global_config.embedding_model_name = embedding_model_name

        if llm_base_url is not None:
            self.global_config.llm_base_url = llm_base_url

        if embedding_base_url is not None:
            self.global_config.embedding_base_url = embedding_base_url

        if azure_endpoint is not None:
            self.global_config.azure_endpoint = azure_endpoint

        if azure_embedding_endpoint is not None:
            self.global_config.azure_embedding_endpoint = azure_embedding_endpoint

        _print_config = ",\n  ".join([f"{k} = {v}" for k, v in asdict(self.global_config).items()])
        logger.debug(f"SynapseRAG init with config:\n  {_print_config}\n")

        # New indexes are independent of the OpenIE and QA model names.  The
        # historical layout remains the default when index_id is not supplied.
        llm_label = self.global_config.llm_name.replace("/", "_")
        embedding_label = self.global_config.embedding_model_name.replace("/", "_")
        if self.global_config.index_dir:
            self.working_dir = self.global_config.index_dir
        elif self.global_config.index_id:
            self.working_dir = os.path.join(
                self.global_config.save_dir,
                "indexes",
                self.global_config.index_id,
                embedding_label,
            )
        else:
            self.working_dir = os.path.join(self.global_config.save_dir, f"{llm_label}_{embedding_label}")

        if not os.path.exists(self.working_dir):
            logger.info(f"Creating working directory: {self.working_dir}")
            os.makedirs(self.working_dir, exist_ok=True)

        if self.global_config.openie_results_path:
            self.openie_results_path = self.global_config.openie_results_path
        elif self.global_config.index_id:
            self.openie_results_path = os.path.join(
                self.global_config.save_dir,
                "openie",
                self.global_config.index_id,
                "openie_results.json",
            )
        else:
            openie_label = self.global_config.get_llm_endpoint("openie").model_name.replace("/", "_")
            self.openie_results_path = os.path.join(
                self.global_config.save_dir, f'openie_results_ner_{openie_label}.json')
        os.makedirs(os.path.dirname(self.openie_results_path) or ".", exist_ok=True)

        self.index_manifest_path = os.path.join(self.working_dir, "index_manifest.json")
        self.openie_cache_metadata = {}
        self.openie = None
        self.openie_llm = None
        self.qa_llm = None
        self.rerank_llm = None
        self.llm_model = None  # Legacy alias for the QA model.
        self.embedding_model = None
        self.chunk_embedding_store = None
        self.entity_embedding_store = None
        self.fact_embedding_store = None

        stage = self.global_config.runtime_stage
        if stage != "openie":
            self._ensure_embedding_runtime()

        self.graph = self.initialize_graph() if stage != "openie" else ig.Graph(
            directed=self.global_config.is_directed_graph)

        if stage in ("all", "query"):
            self._ensure_qa_runtime()

        if stage == "query":
            self._validate_index_manifest()

        self.prompt_template_manager = PromptTemplateManager(role_mapping={"system": "system", "user": "user", "assistant": "assistant"})

        self.rerank_filter = DSPyFilter(self) if self.rerank_llm is not None else None

        self.ready_to_retrieve = False

        self.ppr_time = 0
        self.rerank_time = 0
        self.all_retrieval_time = 0

        self.ent_node_to_chunk_ids = None

    def _ensure_qa_runtime(self):
        if self.qa_llm is None:
            qa_endpoint = self.global_config.get_llm_endpoint("qa")
            self.qa_llm = create_llm(qa_endpoint, self.global_config, role="qa")
            self.llm_model = self.qa_llm

        rerank_endpoint = self.global_config.get_llm_endpoint("rerank")
        qa_endpoint = self.global_config.get_llm_endpoint("qa")
        if self.rerank_llm is None:
            if rerank_endpoint == qa_endpoint:
                self.rerank_llm = self.qa_llm
            else:
                self.rerank_llm = create_llm(
                    rerank_endpoint, self.global_config, role="rerank")
        return self.qa_llm

    def _ensure_openie_runtime(self):
        if self.openie is not None:
            return self.openie

        if self.global_config.openie_mode == 'online':
            endpoint = self.global_config.get_llm_endpoint("openie")
            self.openie_llm = create_llm(endpoint, self.global_config, role="openie")
            self.openie = OpenIE(llm_model=self.openie_llm)
        elif self.global_config.openie_mode == 'offline':
            if VLLMOfflineOpenIE is None:
                raise RuntimeError("vLLM offline OpenIE dependencies are not available")
            self.openie = VLLMOfflineOpenIE(self.global_config)
        elif self.global_config.openie_mode == 'Transformers-offline':
            if TransformersOfflineOpenIE is None:
                raise RuntimeError("Transformers offline OpenIE dependencies are not available")
            self.openie = TransformersOfflineOpenIE(self.global_config)
        else:
            raise ValueError(f"Unsupported openie_mode: {self.global_config.openie_mode}")
        return self.openie

    def _ensure_embedding_runtime(self):
        if self.embedding_model is None:
            embedding_class = _get_embedding_model_class(
                embedding_model_name=self.global_config.embedding_model_name)
            if embedding_class is None:
                raise RuntimeError(
                    f"Embedding dependencies are unavailable for {self.global_config.embedding_model_name}")
            self.embedding_model = embedding_class(
                global_config=self.global_config,
                embedding_model_name=self.global_config.embedding_model_name,
            )

        if self.chunk_embedding_store is None:
            self.chunk_embedding_store = EmbeddingStore(
                self.embedding_model,
                os.path.join(self.working_dir, "chunk_embeddings"),
                self.global_config.embedding_batch_size,
                'chunk',
            )
            self.entity_embedding_store = EmbeddingStore(
                self.embedding_model,
                os.path.join(self.working_dir, "entity_embeddings"),
                self.global_config.embedding_batch_size,
                'entity',
            )
            self.fact_embedding_store = EmbeddingStore(
                self.embedding_model,
                os.path.join(self.working_dir, "fact_embeddings"),
                self.global_config.embedding_batch_size,
                'fact',
            )
        return self.embedding_model


    def initialize_graph(self):
        """
        Initializes a graph using a Pickle file if available or creates a new graph.

        The function attempts to load a pre-existing graph stored in a Pickle file. If the file
        is not present or the graph needs to be created from scratch, it initializes a new directed
        or undirected graph based on the global configuration. If the graph is loaded successfully
        from the file, pertinent information about the graph (number of nodes and edges) is logged.

        Returns:
            ig.Graph: A pre-loaded or newly initialized graph.

        Raises:
            None
        """
        self._graph_pickle_filename = os.path.join(
            self.working_dir, f"graph.pickle"
        )

        preloaded_graph = None

        if not self.global_config.force_index_from_scratch:
            if os.path.exists(self._graph_pickle_filename):
                preloaded_graph = ig.Graph.Read_Pickle(self._graph_pickle_filename)

        if preloaded_graph is None:
            return ig.Graph(directed=self.global_config.is_directed_graph)
        else:
            logger.info(
                f"Loaded graph from {self._graph_pickle_filename} with {preloaded_graph.vcount()} nodes, {preloaded_graph.ecount()} edges"
            )
            return preloaded_graph

    @staticmethod
    def _make_chunk_rows(docs: List[str]) -> Dict[str, dict]:
        return {
            compute_mdhash_id(doc, prefix="chunk-"): {
                "hash_id": compute_mdhash_id(doc, prefix="chunk-"),
                "content": doc,
            }
            for doc in docs
        }

    @staticmethod
    def _partition_openie_batch_results(
        chunk_rows: Dict[str, dict],
        ner_results_dict: Dict[str, NerRawOutput],
        triple_results_dict: Dict[str, TripleRawOutput],
    ) -> Tuple[Dict[str, dict], Dict[str, str]]:
        """Split an OpenIE batch into successful rows and explicit failures.

        The online OpenIE adapter records provider errors in result metadata so
        one failed request does not abort its worker pool.  Treating those rows
        as successful would silently persist empty entities/triples.  Keep the
        successful rows cacheable and make the caller retry only failed rows.
        """
        successful: Dict[str, dict] = {}
        failures: Dict[str, str] = {}
        for chunk_key, row in chunk_rows.items():
            ner_result = ner_results_dict.get(chunk_key)
            triple_result = triple_results_dict.get(chunk_key)
            errors = []
            if ner_result is None:
                errors.append("missing NER result")
            elif ner_result.metadata.get("error"):
                errors.append(f"NER: {ner_result.metadata['error']}")
            if triple_result is None:
                errors.append("missing triple result")
            elif triple_result.metadata.get("error"):
                errors.append(f"triple: {triple_result.metadata['error']}")
            if errors:
                failures[chunk_key] = "; ".join(errors)
            else:
                successful[chunk_key] = row
        return successful, failures

    def _merge_cache_and_raise_openie_failures(
        self,
        all_openie_info: List[dict],
        new_openie_rows: Dict[str, dict],
        ner_results_dict: Dict[str, NerRawOutput],
        triple_results_dict: Dict[str, TripleRawOutput],
    ) -> int:
        successful_rows, failures = self._partition_openie_batch_results(
            new_openie_rows, ner_results_dict, triple_results_dict)
        if successful_rows:
            self.merge_openie_results(
                all_openie_info,
                successful_rows,
                ner_results_dict,
                triple_results_dict,
            )
            if self.global_config.save_openie:
                self.save_openie_results(all_openie_info)
        if failures:
            preview = ", ".join(
                f"{chunk_key}: {message}" for chunk_key, message in list(failures.items())[:3])
            raise RuntimeError(
                f"OpenIE failed for {len(failures)} chunk(s); successful chunks were cached. {preview}"
            )
        return len(successful_rows)

    def extract_openie(self, docs: List[str], force: bool = False) -> Dict[str, Any]:
        """Extract and persist OpenIE results without loading an embedding model."""
        chunks = self._make_chunk_rows(docs)
        if force:
            all_openie_info = []
            chunk_keys_to_process = set(chunks)
        else:
            all_openie_info, chunk_keys_to_process = self.load_existing_openie(chunks.keys())

        new_openie_rows = {key: chunks[key] for key in chunk_keys_to_process}
        processed_count = 0
        if new_openie_rows:
            openie = self._ensure_openie_runtime()
            new_ner_results_dict, new_triple_results_dict = openie.batch_openie(new_openie_rows)
            processed_count = self._merge_cache_and_raise_openie_failures(
                all_openie_info,
                new_openie_rows,
                new_ner_results_dict,
                new_triple_results_dict,
            )

        report = {
            "stage": "openie",
            "openie_results_path": self.openie_results_path,
            "document_count": len(chunks),
            "processed_count": processed_count,
            "reused_count": len(chunks) - len(new_openie_rows),
        }
        logger.info("OpenIE stage completed: %s", report)
        return report

    def pre_openie(self, docs: List[str]):
        """Backward-compatible alias for the explicit OpenIE-only stage."""
        return self.extract_openie(
            docs,
            force=self.global_config.force_openie_from_scratch,
        )

    def index(self, docs: List[str]):
        """Backward-compatible all-in-one indexing entry point."""
        if self.global_config.openie_mode == 'offline':
            return self.extract_openie(
                docs,
                force=self.global_config.force_openie_from_scratch,
            )
        return self.build_index(docs, allow_openie_calls=True)

    def build_index(self, docs: List[str], allow_openie_calls: bool = False):
        """
        Indexes the given documents based on the SynapseRAG 2 framework which generates an OpenIE knowledge graph
        based on the given documents and encodes passages, entities and facts separately for later retrieval.

        Parameters:
            docs : List[str]
                A list of documents to be indexed.
        """

        logger.info(f"Indexing Documents")
        self._ensure_embedding_runtime()
        if (os.path.isfile(self.index_manifest_path)
                and not self.global_config.force_index_from_scratch):
            self._validate_index_manifest()

        logger.info(f"Performing OpenIE")

        # Validate/extract OpenIE before mutating any embedding store.  A missing
        # cache in the explicit two-stage flow must not leave a partial index.
        requested_chunks = self._make_chunk_rows(docs)
        all_openie_info, chunk_keys_to_process = self.load_existing_openie(
            requested_chunks.keys())
        new_openie_rows = {k: requested_chunks[k] for k in chunk_keys_to_process}

        if len(chunk_keys_to_process) > 0:
            if not allow_openie_calls:
                raise RuntimeError(
                    f"OpenIE cache is missing {len(chunk_keys_to_process)} documents. "
                    "Run extract_openie() first or set allow_openie_calls=True."
                )
            openie = self._ensure_openie_runtime()
            new_ner_results_dict, new_triple_results_dict = openie.batch_openie(new_openie_rows)
            self._merge_cache_and_raise_openie_failures(
                all_openie_info,
                new_openie_rows,
                new_ner_results_dict,
                new_triple_results_dict,
            )

        self.chunk_embedding_store.insert_strings(docs)
        chunk_to_rows = self.chunk_embedding_store.get_all_id_to_rows()

        ner_results_dict, triple_results_dict = reformat_openie_results(all_openie_info)

        assert len(chunk_to_rows) == len(ner_results_dict) == len(triple_results_dict), f"len(chunk_to_rows): {len(chunk_to_rows)}, len(ner_results_dict): {len(ner_results_dict)}, len(triple_results_dict): {len(triple_results_dict)}"

        # prepare data_store
        chunk_ids = list(chunk_to_rows.keys())

        chunk_triples = [[text_processing(t) for t in triple_results_dict[chunk_id].triples] for chunk_id in chunk_ids]
        entity_nodes, chunk_triple_entities = extract_entity_nodes(chunk_triples)
        facts = flatten_facts(chunk_triples)

        logger.info(f"Encoding Entities")
        self.entity_embedding_store.insert_strings(entity_nodes)

        logger.info(f"Encoding Facts")
        self.fact_embedding_store.insert_strings([str(fact) for fact in facts])

        logger.info(f"Constructing Graph")

        # === 判断是否需要重建图拓扑 ===
        edge_base_path = os.path.join(self.working_dir, 'graph_edges_base.pkl')
        need_rebuild_topology = (
            self.global_config.force_rebuild_graph or
            not os.path.exists(edge_base_path)
        )

        if need_rebuild_topology:
            logger.info("构建图拓扑结构")

            # 重置边信息
            self.node_to_node_stats = {}
            self.ent_node_to_chunk_ids = {}

            # 构建拓扑（添加边）
            self.add_fact_edges(chunk_ids, chunk_triples)
            num_new_chunks = self.add_passage_edges(chunk_ids, chunk_triple_entities)

            if num_new_chunks > 0:
                logger.info(f"Found {num_new_chunks} new chunks to save into graph.")
                self.add_synonymy_edges()

                # 保存基础边信息（拓扑结构）
                self.save_edge_base_info()
        else:
            logger.info("复用已有图拓扑")
            # 加载基础边信息
            self.load_edge_base_info()

        # === 计算边权重（可快速修改） ===
        self._compute_edge_weights()

        # === 构建完整图 ===
        # Materialize from the persisted base topology into a fresh graph.
        # Reusing the graph loaded by __init__ would append the same edges on
        # every repeated build-index call (16 -> 32 -> 48 ...).
        self.graph = ig.Graph(directed=self.global_config.is_directed_graph)
        self.augment_graph()
        self.save_igraph()
        self._write_index_manifest()
        return {
            "stage": "build-index",
            "working_dir": self.working_dir,
            "manifest_path": self.index_manifest_path,
            "document_count": len(chunk_ids),
            "graph_nodes": self.graph.vcount(),
            "graph_edges": self.graph.ecount(),
        }

    def _embedding_dimension(self) -> Optional[int]:
        dimensions = set()
        for store in (
            self.chunk_embedding_store,
            self.entity_embedding_store,
            self.fact_embedding_store,
        ):
            if store is not None and store.embeddings:
                dimensions.add(int(np.asarray(store.embeddings[0]).size))
        if len(dimensions) > 1:
            raise ValueError(
                f"Embedding stores use inconsistent dimensions: {sorted(dimensions)}")
        return next(iter(dimensions), None)

    @staticmethod
    def _stable_hash(value: Any) -> str:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _embedding_fingerprint(self) -> Dict[str, Any]:
        fact_instruction = (
            self.global_config.embedding_query_to_fact_instruction
            or get_query_instruction('query_to_fact')
        )
        passage_instruction = (
            self.global_config.embedding_query_to_passage_instruction
            or get_query_instruction('query_to_passage')
        )
        return {
            "embedding_model": self.global_config.embedding_model_name,
            "embedding_dimension": self._embedding_dimension(),
            "embedding_normalized": self.global_config.embedding_return_as_normalized,
            "embedding_instruction_mode": self.global_config.embedding_query_instruction_mode,
            "document_instruction_hash": self._stable_hash(
                self.global_config.embedding_document_instruction),
            "query_to_fact_instruction_hash": self._stable_hash(fact_instruction),
            "query_to_passage_instruction_hash": self._stable_hash(passage_instruction),
        }

    def _write_index_manifest(self):
        if self.chunk_embedding_store is None:
            raise RuntimeError("Cannot write an index manifest before embeddings are initialized")
        existing = {}
        if os.path.isfile(self.index_manifest_path):
            with open(self.index_manifest_path, "r", encoding="utf-8") as file:
                existing = json.load(file)

        now = datetime.now().isoformat()
        document_ids = sorted(self.chunk_embedding_store.get_all_ids())
        manifest = {
            "schema_version": 1,
            "index_id": self.global_config.index_id,
            **self._embedding_fingerprint(),
            "openie_source_model": self.openie_cache_metadata.get(
                "source_model",
                self.global_config.get_llm_endpoint("openie").model_name,
            ),
            "openie_prompt_version": self.openie_cache_metadata.get(
                "prompt_version", self.global_config.openie_prompt_version),
            "document_count": len(document_ids),
            "document_ids_hash": self._stable_hash(document_ids),
            "created_at": existing.get("created_at", now),
            "updated_at": now,
        }
        temp_path = self.index_manifest_path + ".tmp"
        with open(temp_path, "w", encoding="utf-8") as file:
            json.dump(manifest, file, ensure_ascii=False, indent=2)
        os.replace(temp_path, self.index_manifest_path)

    def _validate_index_manifest(self):
        if not os.path.isfile(self.index_manifest_path):
            if self.global_config.index_id:
                raise FileNotFoundError(
                    f"Missing index manifest: {self.index_manifest_path}. Rebuild or migrate the index."
                )
            if self.global_config.allow_legacy_index_layout:
                logger.warning("Loading a legacy index without an index manifest: %s", self.working_dir)
                return None
            raise FileNotFoundError(f"Missing index manifest: {self.index_manifest_path}")

        with open(self.index_manifest_path, "r", encoding="utf-8") as file:
            manifest = json.load(file)
        expected = self._embedding_fingerprint()
        mismatches = {
            key: {"expected": value, "actual": manifest.get(key)}
            for key, value in expected.items()
            if manifest.get(key) != value
        }
        if mismatches:
            raise ValueError(
                "Embedding configuration does not match the existing index: "
                + json.dumps(mismatches, ensure_ascii=False, default=str)
            )
        actual_document_count = len(self.chunk_embedding_store.get_all_ids())
        if manifest.get("document_count") != actual_document_count:
            raise ValueError(
                "Index document count mismatch: "
                f"manifest={manifest.get('document_count')}, parquet={actual_document_count}")
        if self.graph.vcount() and "name" in self.graph.vs.attributes():
            graph_names = set(self.graph.vs["name"])
            missing_passages = set(self.chunk_embedding_store.get_all_ids()) - graph_names
            if missing_passages:
                raise ValueError(
                    f"Graph is missing {len(missing_passages)} passage nodes from the chunk store")
        return manifest

    def load_index(self) -> Dict[str, Any]:
        """Load and validate an existing index without initializing OpenIE."""
        self._ensure_embedding_runtime()
        self.graph = self.initialize_graph()
        manifest = self._validate_index_manifest()
        self._ensure_qa_runtime()
        self.rerank_filter = DSPyFilter(self)
        return {
            "stage": "query",
            "working_dir": self.working_dir,
            "manifest": manifest,
            "graph_nodes": self.graph.vcount(),
            "graph_edges": self.graph.ecount(),
        }

    def delete(self, docs_to_delete: List[str]):
        """
        Deletes the given documents from all data structures within the SynapseRAG class.
        Note that triples and entities which are indexed from chunks that are not being removed will not be removed.

        Parameters:
            docs : List[str]
                A list of documents to be deleted.
        """

        #Making sure that all the necessary structures have been built.
        if not self.ready_to_retrieve:
            self.prepare_retrieval_objects()

        current_docs = set(self.chunk_embedding_store.get_all_texts())
        docs_to_delete = [doc for doc in docs_to_delete if doc in current_docs]

        #Get ids for chunks to delete
        chunk_ids_to_delete = set(
            [self.chunk_embedding_store.text_to_hash_id[chunk] for chunk in docs_to_delete])

        #Find triples in chunks to delete
        all_openie_info, chunk_keys_to_process = self.load_existing_openie([])
        triples_to_delete = []

        all_openie_info_with_deletes = []

        for openie_doc in all_openie_info:
            if openie_doc['idx'] in chunk_ids_to_delete:
                triples_to_delete.append(openie_doc['extracted_triples'])
            else:
                all_openie_info_with_deletes.append(openie_doc)

        triples_to_delete = flatten_facts(triples_to_delete)

        #Filter out triples that appear in unaltered chunks
        true_triples_to_delete = []

        for triple in triples_to_delete:
            proc_triple = tuple(text_processing(list(triple)))

            doc_ids = self.proc_triples_to_docs[str(proc_triple)]

            non_deleted_docs = doc_ids.difference(chunk_ids_to_delete)

            if len(non_deleted_docs) == 0:
                true_triples_to_delete.append(triple)

        processed_true_triples_to_delete = [[text_processing(list(triple)) for triple in true_triples_to_delete]]
        entities_to_delete, _ = extract_entity_nodes(processed_true_triples_to_delete)
        processed_true_triples_to_delete = flatten_facts(processed_true_triples_to_delete)

        triple_ids_to_delete = set([self.fact_embedding_store.text_to_hash_id[str(triple)] for triple in processed_true_triples_to_delete])

        #Filter out entities that appear in unaltered chunks
        ent_ids_to_delete = [self.entity_embedding_store.text_to_hash_id[ent] for ent in entities_to_delete]

        filtered_ent_ids_to_delete = []

        for ent_node in ent_ids_to_delete:
            doc_ids = self.ent_node_to_chunk_ids[ent_node]

            non_deleted_docs = doc_ids.difference(chunk_ids_to_delete)

            if len(non_deleted_docs) == 0:
                filtered_ent_ids_to_delete.append(ent_node)

        logger.info(f"Deleting {len(chunk_ids_to_delete)} Chunks")
        logger.info(f"Deleting {len(triple_ids_to_delete)} Triples")
        logger.info(f"Deleting {len(filtered_ent_ids_to_delete)} Entities")

        self.save_openie_results(all_openie_info_with_deletes)

        self.entity_embedding_store.delete(filtered_ent_ids_to_delete)
        self.fact_embedding_store.delete(triple_ids_to_delete)
        self.chunk_embedding_store.delete(chunk_ids_to_delete)

        #Delete Nodes from Graph
        self.graph.delete_vertices(list(filtered_ent_ids_to_delete) + list(chunk_ids_to_delete))
        self.save_igraph()

        self.ready_to_retrieve = False

    def retrieve(self,
                 queries: List[str],
                 num_to_retrieve: int = None,
                 gold_docs: List[List[str]] = None,
                 trace_collector: Optional[RetrievalTraceCollector] = None) -> List[QuerySolution] | Tuple[List[QuerySolution], Dict]:
        """
        Performs retrieval using the SynapseRAG 2 framework, which consists of several steps:
        - Fact Retrieval
        - Recognition Memory for improved fact selection
        - Dense passage scoring
        - Personalized PageRank based re-ranking

        Parameters:
            queries: List[str]
                A list of query strings for which documents are to be retrieved.
            num_to_retrieve: int, optional
                The maximum number of documents to retrieve for each query. If not specified, defaults to
                the `retrieval_top_k` value defined in the global configuration.
            gold_docs: List[List[str]], optional
                A list of lists containing gold-standard documents corresponding to each query. Required
                if retrieval performance evaluation is enabled (`do_eval_retrieval` in global configuration).

        Returns:
            List[QuerySolution] or (List[QuerySolution], Dict)
                If retrieval performance evaluation is not enabled, returns a list of QuerySolution objects, each containing
                the retrieved documents and their scores for the corresponding query. If evaluation is enabled, also returns
                a dictionary containing the evaluation metrics computed over the retrieved results.

        Notes
        -----
        - Long queries with no relevant facts after reranking will default to results from dense passage retrieval.
        """
        retrieve_start_time = time.time()  # Record start time

        if num_to_retrieve is None:
            num_to_retrieve = self.global_config.retrieval_top_k

        if gold_docs is not None:
            retrieval_recall_evaluator = RetrievalRecall(global_config=self.global_config)

        if not self.ready_to_retrieve:
            self.prepare_retrieval_objects()

        embedding_started = time.perf_counter()
        self.get_query_embeddings(queries)
        if trace_collector is not None:
            trace_collector.record_request_timing(
                "query_embedding", time.perf_counter() - embedding_started
            )

        retrieval_results = []

        for q_idx, query in tqdm(enumerate(queries), desc="Retrieving", total=len(queries)):
            query_trace = trace_collector.begin_query(q_idx, query) if trace_collector else None
            query_started = time.perf_counter()
            try:
                rerank_start = time.perf_counter()
                query_fact_scores = self.get_fact_scores(query)
                top_k_fact_indices, top_k_facts, rerank_log = self.rerank_facts(query, query_fact_scores)
                rerank_duration = time.perf_counter() - rerank_start
                self.rerank_time += rerank_duration

                if query_trace is not None:
                    candidate_facts = rerank_log.get("facts_before_rerank", [])
                    score_order = np.argsort(query_fact_scores)[::-1].tolist() if len(query_fact_scores) else []
                    candidate_scores = [
                        float(query_fact_scores[index])
                        for index in score_order[:len(candidate_facts)]
                    ]
                    trace_collector.record_facts(
                        query_trace,
                        candidates=candidate_facts,
                        selected=top_k_facts,
                        candidate_scores=candidate_scores,
                        rerank_metadata=rerank_log,
                    )
                    trace_collector.record_timing(query_trace, "fact_rerank", rerank_duration)

                graph_started = time.perf_counter()
                if len(top_k_facts) == 0:
                    logger.info('No facts found after reranking, return DPR results')
                    sorted_doc_ids, sorted_doc_scores = self.dense_passage_retrieval(query)
                    graph_channel = "dense_fallback"
                    if query_trace is not None:
                        trace_collector.add_fallback(
                            query_trace,
                            "graph_retrieval",
                            "dense",
                            "no facts remained after reranking",
                        )
                else:
                    sorted_doc_ids, sorted_doc_scores = self.graph_search_with_fact_entities(
                        query=query,
                        link_top_k=self.global_config.linking_top_k,
                        query_fact_scores=query_fact_scores,
                        top_k_facts=top_k_facts,
                        top_k_fact_indices=top_k_fact_indices,
                        passage_node_weight=self.global_config.passage_node_weight,
                        trace_collector=trace_collector,
                        query_trace=query_trace,
                    )
                    graph_channel = "graph"
                graph_duration = time.perf_counter() - graph_started

                if query_trace is not None:
                    trace_collector.record_channel(
                        query_trace,
                        graph_channel,
                        sorted_doc_ids,
                        sorted_doc_scores,
                        self.passage_node_keys,
                    )
                    trace_collector.record_timing(query_trace, "graph_retrieval", graph_duration)

                if self.global_config.retrieval_fusion_mode == "hybrid":
                    dense_started = time.perf_counter()
                    dense_doc_ids, dense_doc_scores = self.dense_passage_retrieval(query)
                    dense_duration = time.perf_counter() - dense_started
                    lexical_started = time.perf_counter()
                    lexical_doc_ids, lexical_doc_scores = self.lexical_passage_retrieval(query)
                    lexical_duration = time.perf_counter() - lexical_started
                    named_rankings = [
                        (graph_channel, sorted_doc_ids, sorted_doc_scores, self.global_config.retrieval_graph_weight),
                        ("dense", dense_doc_ids, dense_doc_scores, self.global_config.retrieval_dense_weight),
                    ]
                    if len(lexical_doc_ids) > 0:
                        named_rankings.append(
                            ("lexical", lexical_doc_ids, lexical_doc_scores, self.global_config.retrieval_lexical_weight)
                        )
                    fusion_started = time.perf_counter()
                    sorted_doc_ids, sorted_doc_scores, contributions = self._weighted_score_fusion_with_contributions(
                        named_rankings
                    )
                    fusion_duration = time.perf_counter() - fusion_started
                    if query_trace is not None:
                        trace_collector.record_channel(
                            query_trace, "dense", dense_doc_ids, dense_doc_scores, self.passage_node_keys
                        )
                        trace_collector.record_channel(
                            query_trace, "lexical", lexical_doc_ids, lexical_doc_scores, self.passage_node_keys
                        )
                        trace_collector.record_fusion(
                            query_trace,
                            sorted_doc_ids,
                            sorted_doc_scores,
                            contributions,
                            self.passage_node_keys,
                        )
                        trace_collector.record_timing(query_trace, "dense_retrieval", dense_duration)
                        trace_collector.record_timing(query_trace, "lexical_retrieval", lexical_duration)
                        trace_collector.record_timing(query_trace, "score_fusion", fusion_duration)

                top_k_docs = [
                    self.chunk_embedding_store.get_row(self.passage_node_keys[idx])["content"]
                    for idx in sorted_doc_ids[:num_to_retrieve]
                ]

                retrieval_results.append(QuerySolution(
                    question=query,
                    docs=top_k_docs,
                    doc_scores=sorted_doc_scores[:num_to_retrieve],
                ))
                if query_trace is not None:
                    trace_collector.record_timing(
                        query_trace, "total", time.perf_counter() - query_started
                    )
                    trace_collector.finish_query(query_trace)
            except Exception as error:
                if query_trace is not None:
                    trace_collector.record_timing(
                        query_trace, "total", time.perf_counter() - query_started
                    )
                    trace_collector.fail_query(query_trace, error)
                raise

        retrieve_end_time = time.time()  # Record end time

        self.all_retrieval_time += retrieve_end_time - retrieve_start_time

        logger.info(f"Total Retrieval Time {self.all_retrieval_time:.2f}s")
        logger.info(f"Total Recognition Memory Time {self.rerank_time:.2f}s")
        logger.info(f"Total PPR Time {self.ppr_time:.2f}s")
        logger.info(f"Total Misc Time {self.all_retrieval_time - (self.rerank_time + self.ppr_time):.2f}s")

        # Evaluate retrieval
        if gold_docs is not None:
            k_list = [1, 2, 5, 10, 20, 30, 50, 100, 150, 200]
            overall_retrieval_result, example_retrieval_results = retrieval_recall_evaluator.calculate_metric_scores(gold_docs=gold_docs, retrieved_docs=[retrieval_result.docs for retrieval_result in retrieval_results], k_list=k_list)
            logger.info(f"Evaluation results for retrieval: {overall_retrieval_result}")

            return retrieval_results, overall_retrieval_result
        else:
            return retrieval_results

    def rag_qa(self,
               queries: List[str|QuerySolution],
               gold_docs: List[List[str]] = None,
               gold_answers: List[List[str]] = None) -> Tuple[List[QuerySolution], List[str], List[Dict]] | Tuple[List[QuerySolution], List[str], List[Dict], Dict, Dict]:
        """
        Performs retrieval-augmented generation enhanced QA using the SynapseRAG 2 framework.

        This method can handle both string-based queries and pre-processed QuerySolution objects. Depending
        on its inputs, it returns answers only or additionally evaluate retrieval and answer quality using
        recall @ k, exact match and F1 score metrics.

        Parameters:
            queries (List[Union[str, QuerySolution]]): A list of queries, which can be either strings or
                QuerySolution instances. If they are strings, retrieval will be performed.
            gold_docs (Optional[List[List[str]]]): A list of lists containing gold-standard documents for
                each query. This is used if document-level evaluation is to be performed. Default is None.
            gold_answers (Optional[List[List[str]]]): A list of lists containing gold-standard answers for
                each query. Required if evaluation of question answering (QA) answers is enabled. Default
                is None.

        Returns:
            Union[
                Tuple[List[QuerySolution], List[str], List[Dict]],
                Tuple[List[QuerySolution], List[str], List[Dict], Dict, Dict]
            ]: A tuple that always includes:
                - List of QuerySolution objects containing answers and metadata for each query.
                - List of response messages for the provided queries.
                - List of metadata dictionaries for each query.
                If evaluation is enabled, the tuple also includes:
                - A dictionary with overall results from the retrieval phase (if applicable).
                - A dictionary with overall QA evaluation metrics (exact match and F1 scores).

        """
        if gold_answers is not None:
            qa_em_evaluator = QAExactMatch(global_config=self.global_config)
            qa_f1_evaluator = QAF1Score(global_config=self.global_config)

        # Retrieving (if necessary)
        overall_retrieval_result = None

        if not isinstance(queries[0], QuerySolution):
            if gold_docs is not None:
                queries, overall_retrieval_result = self.retrieve(queries=queries, gold_docs=gold_docs)
            else:
                queries = self.retrieve(queries=queries)

        # Performing QA
        queries_solutions, all_response_message, all_metadata = self.qa(queries)

        # Evaluating QA
        if gold_answers is not None:
            overall_qa_em_result, example_qa_em_results = qa_em_evaluator.calculate_metric_scores(
                gold_answers=gold_answers, predicted_answers=[qa_result.answer for qa_result in queries_solutions],
                aggregation_fn=np.max)
            overall_qa_f1_result, example_qa_f1_results = qa_f1_evaluator.calculate_metric_scores(
                gold_answers=gold_answers, predicted_answers=[qa_result.answer for qa_result in queries_solutions],
                aggregation_fn=np.max)

            # round off to 4 decimal places for QA results
            overall_qa_em_result.update(overall_qa_f1_result)
            overall_qa_results = overall_qa_em_result
            overall_qa_results = {k: round(float(v), 4) for k, v in overall_qa_results.items()}
            logger.info(f"Evaluation results for QA: {overall_qa_results}")

            # Save retrieval and QA results
            for idx, q in enumerate(queries_solutions):
                q.gold_answers = list(gold_answers[idx])
                if gold_docs is not None:
                    q.gold_docs = gold_docs[idx]

            return queries_solutions, all_response_message, all_metadata, overall_retrieval_result, overall_qa_results
        else:
            return queries_solutions, all_response_message, all_metadata

    def save_experiment_results(self,
                               experiment_name: str,
                               queries: List[str],
                               retrieval_results: List,
                               qa_responses: List[str],
                               overall_retrieval_result: Dict = None,
                               overall_qa_result: Dict = None,
                               gold_docs: List[List[str]] = None,
                               gold_answers: List[List[str]] = None):
        """
        保存完整的实验结果

        参数：
            experiment_name: 实验名称
            queries: 查询列表
            retrieval_results: 检索结果列表（QuerySolution对象）
            qa_responses: QA响应列表
            overall_retrieval_result: 整体检索指标
            overall_qa_result: 整体QA指标
            gold_docs: 金标准文档
            gold_answers: 金标准答案
        """
        import json

        exp_dir = os.path.join(self.working_dir, 'experiments')
        os.makedirs(exp_dir, exist_ok=True)

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"exp_{experiment_name}_{timestamp}.json"
        filepath = os.path.join(exp_dir, filename)

        # 收集图统计信息
        graph_stats = self.get_graph_info()
        edge_weights = [e['weight'] for e in self.graph.es if 'weight' in e.attributes()]

        # 构建完整结果
        results = {
            'timestamp': datetime.now().isoformat(),
            'experiment_name': experiment_name,

            # 配置信息
            'config': {
                'dataset': self.global_config.dataset,
                'llm_name': self.global_config.llm_name,
                'llm_base_url': self.global_config.llm_base_url,
                'embedding_name': self.global_config.embedding_model_name,
                'damping': self.global_config.damping,
                'max_steps': self.global_config.max_steps,
                'linking_top_k': self.global_config.linking_top_k,
                'retrieval_top_k': self.global_config.retrieval_top_k,
                'passage_node_weight': self.global_config.passage_node_weight,
                'use_softmax_fusion': self.global_config.use_softmax_fusion,
                'openie_mode': self.global_config.openie_mode,
                'force_rebuild_graph': getattr(self.global_config, 'force_rebuild_graph', False),
                'use_agentic_ppr_reset': self.global_config.use_agentic_ppr_reset,
            },

            # 图统计
            'graph_stats': {
                'num_nodes': self.graph.vcount(),
                'num_edges': self.graph.ecount(),
                'num_chunks': sum(1 for v in self.graph.vs if v['name'].startswith('chunk-')),
                'num_entities': sum(1 for v in self.graph.vs if v['name'].startswith('entity-')),
                'edge_weight_stats': {
                    'min': float(np.min(edge_weights)) if edge_weights else 0,
                    'max': float(np.max(edge_weights)) if edge_weights else 0,
                    'mean': float(np.mean(edge_weights)) if edge_weights else 0,
                    'median': float(np.median(edge_weights)) if edge_weights else 0,
                    'std': float(np.std(edge_weights)) if edge_weights else 0,
                }
            },

            # 整体指标
            'retrieval_metrics': overall_retrieval_result or {},
            'qa_metrics': overall_qa_result or {},

            # 时间统计
            'timing': {
                'total_retrieval_time': self.all_retrieval_time,
                'ppr_time': self.ppr_time,
                'rerank_time': self.rerank_time,
                'avg_retrieval_per_query': self.all_retrieval_time / len(queries) if queries else 0,
            },

            # 每个查询的详细结果
            'per_query_results': []
        }

        # 添加每个查询的详细信息
        for i, query in enumerate(queries):
            query_result = {
                'query_id': i,
                'question': query,
                'predicted_answer': qa_responses[i] if i < len(qa_responses) else None,
            }

            if gold_answers and i < len(gold_answers):
                query_result['gold_answers'] = list(gold_answers[i])

            if gold_docs and i < len(gold_docs):
                query_result['gold_docs'] = gold_docs[i]

            if retrieval_results and i < len(retrieval_results):
                ret_result = retrieval_results[i]
                query_result['retrieved_docs'] = ret_result.docs[:10]  # 只保存前10个
                query_result['retrieval_scores'] = [float(s) for s in ret_result.doc_scores[:10]]

            results['per_query_results'].append(query_result)

        # 保存到文件
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2, ensure_ascii=False)

        logger.info(f"实验结果已保存到: {filepath}")
        logger.info(f"  - Retrieval Metrics: {overall_retrieval_result}")
        logger.info(f"  - QA Metrics: {overall_qa_result}")

        return filepath

    def retrieve_dpr(self,
                     queries: List[str],
                     num_to_retrieve: int = None,
                     gold_docs: List[List[str]] = None) -> List[QuerySolution] | Tuple[List[QuerySolution], Dict]:
        """
        Performs retrieval using a DPR framework, which consists of several steps:
        - Dense passage scoring

        Parameters:
            queries: List[str]
                A list of query strings for which documents are to be retrieved.
            num_to_retrieve: int, optional
                The maximum number of documents to retrieve for each query. If not specified, defaults to
                the `retrieval_top_k` value defined in the global configuration.
            gold_docs: List[List[str]], optional
                A list of lists containing gold-standard documents corresponding to each query. Required
                if retrieval performance evaluation is enabled (`do_eval_retrieval` in global configuration).

        Returns:
            List[QuerySolution] or (List[QuerySolution], Dict)
                If retrieval performance evaluation is not enabled, returns a list of QuerySolution objects, each containing
                the retrieved documents and their scores for the corresponding query. If evaluation is enabled, also returns
                a dictionary containing the evaluation metrics computed over the retrieved results.

        Notes
        -----
        - Long queries with no relevant facts after reranking will default to results from dense passage retrieval.
        """
        retrieve_start_time = time.time()  # Record start time

        if num_to_retrieve is None:
            num_to_retrieve = self.global_config.retrieval_top_k

        if gold_docs is not None:
            retrieval_recall_evaluator = RetrievalRecall(global_config=self.global_config)

        if not self.ready_to_retrieve:
            self.prepare_retrieval_objects()

        self.get_query_embeddings(queries)

        retrieval_results = []

        for q_idx, query in tqdm(enumerate(queries), desc="Retrieving", total=len(queries)):
            logger.info('No facts found after reranking, return DPR results')
            sorted_doc_ids, sorted_doc_scores = self.dense_passage_retrieval(query)

            top_k_docs = [self.chunk_embedding_store.get_row(self.passage_node_keys[idx])["content"] for idx in
                          sorted_doc_ids[:num_to_retrieve]]

            retrieval_results.append(
                QuerySolution(question=query, docs=top_k_docs, doc_scores=sorted_doc_scores[:num_to_retrieve]))

        retrieve_end_time = time.time()  # Record end time

        self.all_retrieval_time += retrieve_end_time - retrieve_start_time

        logger.info(f"Total Retrieval Time {self.all_retrieval_time:.2f}s")

        # Evaluate retrieval
        if gold_docs is not None:
            k_list = [1, 2, 5, 10, 20, 30, 50, 100, 150, 200]
            overall_retrieval_result, example_retrieval_results = retrieval_recall_evaluator.calculate_metric_scores(
                gold_docs=gold_docs, retrieved_docs=[retrieval_result.docs for retrieval_result in retrieval_results],
                k_list=k_list)
            logger.info(f"Evaluation results for retrieval: {overall_retrieval_result}")

            return retrieval_results, overall_retrieval_result
        else:
            return retrieval_results

    def rag_qa_dpr(self,
               queries: List[str|QuerySolution],
               gold_docs: List[List[str]] = None,
               gold_answers: List[List[str]] = None) -> Tuple[List[QuerySolution], List[str], List[Dict]] | Tuple[List[QuerySolution], List[str], List[Dict], Dict, Dict]:
        """
        Performs retrieval-augmented generation enhanced QA using a standard DPR framework.

        This method can handle both string-based queries and pre-processed QuerySolution objects. Depending
        on its inputs, it returns answers only or additionally evaluate retrieval and answer quality using
        recall @ k, exact match and F1 score metrics.

        Parameters:
            queries (List[Union[str, QuerySolution]]): A list of queries, which can be either strings or
                QuerySolution instances. If they are strings, retrieval will be performed.
            gold_docs (Optional[List[List[str]]]): A list of lists containing gold-standard documents for
                each query. This is used if document-level evaluation is to be performed. Default is None.
            gold_answers (Optional[List[List[str]]]): A list of lists containing gold-standard answers for
                each query. Required if evaluation of question answering (QA) answers is enabled. Default
                is None.

        Returns:
            Union[
                Tuple[List[QuerySolution], List[str], List[Dict]],
                Tuple[List[QuerySolution], List[str], List[Dict], Dict, Dict]
            ]: A tuple that always includes:
                - List of QuerySolution objects containing answers and metadata for each query.
                - List of response messages for the provided queries.
                - List of metadata dictionaries for each query.
                If evaluation is enabled, the tuple also includes:
                - A dictionary with overall results from the retrieval phase (if applicable).
                - A dictionary with overall QA evaluation metrics (exact match and F1 scores).

        """
        if gold_answers is not None:
            qa_em_evaluator = QAExactMatch(global_config=self.global_config)
            qa_f1_evaluator = QAF1Score(global_config=self.global_config)

        # Retrieving (if necessary)
        overall_retrieval_result = None

        if not isinstance(queries[0], QuerySolution):
            if gold_docs is not None:
                queries, overall_retrieval_result = self.retrieve_dpr(queries=queries, gold_docs=gold_docs)
            else:
                queries = self.retrieve_dpr(queries=queries)

        # Performing QA
        queries_solutions, all_response_message, all_metadata = self.qa(queries)

        # Evaluating QA
        if gold_answers is not None:
            overall_qa_em_result, example_qa_em_results = qa_em_evaluator.calculate_metric_scores(
                gold_answers=gold_answers, predicted_answers=[qa_result.answer for qa_result in queries_solutions],
                aggregation_fn=np.max)
            overall_qa_f1_result, example_qa_f1_results = qa_f1_evaluator.calculate_metric_scores(
                gold_answers=gold_answers, predicted_answers=[qa_result.answer for qa_result in queries_solutions],
                aggregation_fn=np.max)

            # round off to 4 decimal places for QA results
            overall_qa_em_result.update(overall_qa_f1_result)
            overall_qa_results = overall_qa_em_result
            overall_qa_results = {k: round(float(v), 4) for k, v in overall_qa_results.items()}
            logger.info(f"Evaluation results for QA: {overall_qa_results}")

            # Save retrieval and QA results
            for idx, q in enumerate(queries_solutions):
                q.gold_answers = list(gold_answers[idx])
                if gold_docs is not None:
                    q.gold_docs = gold_docs[idx]

            return queries_solutions, all_response_message, all_metadata, overall_retrieval_result, overall_qa_results
        else:
            return queries_solutions, all_response_message, all_metadata

    def qa(self, queries: List[QuerySolution]) -> Tuple[List[QuerySolution], List[str], List[Dict]]:
        """
        Executes question-answering (QA) inference using a provided set of query solutions and a language model.

        Parameters:
            queries: List[QuerySolution]
                A list of QuerySolution objects that contain the user queries, retrieved documents, and other related information.

        Returns:
            Tuple[List[QuerySolution], List[str], List[Dict]]
                A tuple containing:
                - A list of updated QuerySolution objects with the predicted answers embedded in them.
                - A list of raw response messages from the language model.
                - A list of metadata dictionaries associated with the results.
        """
        # Running inference for QA. OpenIE is intentionally not initialized here.
        self._ensure_qa_runtime()
        all_qa_messages = []

        for query_solution in tqdm(queries, desc="Collecting QA prompts"):

            # obtain the retrieved docs
            retrieved_passages = query_solution.docs[:self.global_config.qa_top_k]

            prompt_user = ''
            for passage in retrieved_passages:
                prompt_user += f'Wikipedia Title: {passage}\n\n'
            prompt_user += 'Question: ' + query_solution.question + '\nThought: '

            if self.prompt_template_manager.is_template_name_valid(name=f'rag_qa_{self.global_config.dataset}'):
                # find the corresponding prompt for this dataset
                prompt_dataset_name = self.global_config.dataset
            else:
                # the dataset does not have a customized prompt template yet
                logger.debug(
                    f"rag_qa_{self.global_config.dataset} does not have a customized prompt template. Using MUSIQUE's prompt template instead.")
                prompt_dataset_name = 'musique'
            all_qa_messages.append(
                self.prompt_template_manager.render(name=f'rag_qa_{prompt_dataset_name}', prompt_user=prompt_user))

        all_qa_results = [
            self.qa_llm.infer(qa_messages)
            for qa_messages in tqdm(all_qa_messages, desc="QA Reading")
        ]

        all_response_message, all_metadata, all_cache_hit = zip(*all_qa_results)
        all_response_message, all_metadata = list(all_response_message), list(all_metadata)

        #Process responses and extract predicted answers.
        queries_solutions = []
        for query_solution_idx, query_solution in tqdm(enumerate(queries), desc="Extraction Answers from LLM Response"):
            response_content = all_response_message[query_solution_idx]
            try:
                pred_ans = response_content.split('Answer:')[1].strip()
            except Exception as e:
                logger.warning(f"Error in parsing the answer from the raw LLM QA inference response: {str(e)}!")
                pred_ans = response_content

            query_solution.answer = pred_ans
            queries_solutions.append(query_solution)

        return queries_solutions, all_response_message, all_metadata

    def add_fact_edges(self, chunk_ids: List[str], chunk_triples: List[Tuple]):
        """
        Adds fact edges from given triples to the graph.

        The method processes chunks of triples, computes unique identifiers
        for entities and relations, and updates various internal statistics
        to build and maintain the graph structure. Entities are uniquely
        identified and linked based on their relationships.

        Parameters:
            chunk_ids: List[str]
                A list of unique identifiers for the chunks being processed.
            chunk_triples: List[Tuple]
                A list of tuples representing triples to process. Each triple
                consists of a subject, predicate, and object.

        Raises:
            Does not explicitly raise exceptions within the provided function logic.
        """

        if "name" in self.graph.vs:
            current_graph_nodes = set(self.graph.vs["name"])
        else:
            current_graph_nodes = set()

        logger.info(f"Adding OpenIE triples to graph.")

        for chunk_key, triples in tqdm(zip(chunk_ids, chunk_triples)):
            entities_in_chunk = set()

            if chunk_key not in current_graph_nodes:
                for triple in triples:
                    triple = tuple(triple)

                    node_key = compute_mdhash_id(content=triple[0], prefix=("entity-"))
                    node_2_key = compute_mdhash_id(content=triple[2], prefix=("entity-"))

                    self.node_to_node_stats[(node_key, node_2_key)] = self.node_to_node_stats.get(
                        (node_key, node_2_key), 0.0) + 1
                    self.node_to_node_stats[(node_2_key, node_key)] = self.node_to_node_stats.get(
                        (node_2_key, node_key), 0.0) + 1

                    entities_in_chunk.add(node_key)
                    entities_in_chunk.add(node_2_key)

                for node in entities_in_chunk:
                    self.ent_node_to_chunk_ids[node] = self.ent_node_to_chunk_ids.get(node, set()).union(set([chunk_key]))

    def add_passage_edges(self, chunk_ids: List[str], chunk_triple_entities: List[List[str]]):
        """
        Adds edges connecting passage nodes to phrase nodes in the graph.

        This method is responsible for iterating through a list of chunk identifiers
        and their corresponding triple entities. It calculates and adds new edges
        between the passage nodes (defined by the chunk identifiers) and the phrase
        nodes (defined by the computed unique hash IDs of triple entities). The method
        also updates the node-to-node statistics map and keeps count of newly added
        passage nodes.

        Parameters:
            chunk_ids : List[str]
                A list of identifiers representing passage nodes in the graph.
            chunk_triple_entities : List[List[str]]
                A list of lists where each sublist contains entities (strings) associated
                with the corresponding chunk in the chunk_ids list.

        Returns:
            int
                The number of new passage nodes added to the graph.
        """

        if "name" in self.graph.vs.attribute_names():
            current_graph_nodes = set(self.graph.vs["name"])
        else:
            current_graph_nodes = set()

        num_new_chunks = 0

        logger.info(f"Connecting passage nodes to phrase nodes.")

        for idx, chunk_key in tqdm(enumerate(chunk_ids)):

            if chunk_key not in current_graph_nodes:
                for chunk_ent in chunk_triple_entities[idx]:
                    node_key = compute_mdhash_id(chunk_ent, prefix="entity-")

                    self.node_to_node_stats[(chunk_key, node_key)] = 1.0

                num_new_chunks += 1

        return num_new_chunks

    def add_synonymy_edges(self):
        """
        Adds synonymy edges between similar nodes in the graph to enhance connectivity by identifying and linking synonym entities.

        This method performs key operations to compute and add synonymy edges. It first retrieves embeddings for all nodes, then conducts
        a nearest neighbor (KNN) search to find similar nodes. These similar nodes are identified based on a score threshold, and edges
        are added to represent the synonym relationship.

        Attributes:
            entity_id_to_row: dict (populated within the function). Maps each entity ID to its corresponding row data, where rows
                              contain `content` of entities used for comparison.
            entity_embedding_store: Manages retrieval of texts and embeddings for all rows related to entities.
            global_config: Configuration object that defines parameters such as `synonymy_edge_topk`, `synonymy_edge_sim_threshold`,
                           `synonymy_edge_query_batch_size`, and `synonymy_edge_key_batch_size`.
            node_to_node_stats: dict. Stores scores for edges between nodes representing their relationship.

        """
        logger.info(f"Expanding graph with synonymy edges")

        self.entity_id_to_row = self.entity_embedding_store.get_all_id_to_rows()
        entity_node_keys = list(self.entity_id_to_row.keys())

        logger.info(f"Performing KNN retrieval for each phrase nodes ({len(entity_node_keys)}).")

        entity_embs = self.entity_embedding_store.get_embeddings(entity_node_keys)

        # Here we build synonymy edges only between newly inserted phrase nodes and all phrase nodes in the storage to reduce cost for incremental graph updates
        query_node_key2knn_node_keys = retrieve_knn(query_ids=entity_node_keys,
                                                    key_ids=entity_node_keys,
                                                    query_vecs=entity_embs,
                                                    key_vecs=entity_embs,
                                                    k=self.global_config.synonymy_edge_topk,
                                                    query_batch_size=self.global_config.synonymy_edge_query_batch_size,
                                                    key_batch_size=self.global_config.synonymy_edge_key_batch_size)

        num_synonym_triple = 0
        synonym_candidates = []  # [(node key, [(synonym node key, corresponding score), ...]), ...]

        for node_key in tqdm(query_node_key2knn_node_keys.keys(), total=len(query_node_key2knn_node_keys)):
            synonyms = []

            entity = self.entity_id_to_row[node_key]["content"]

            if len(re.sub('[^A-Za-z0-9]', '', entity)) > 2:
                nns = query_node_key2knn_node_keys[node_key]

                num_nns = 0
                for nn, score in zip(nns[0], nns[1]):
                    if score < self.global_config.synonymy_edge_sim_threshold or num_nns > 100:
                        break

                    nn_phrase = self.entity_id_to_row[nn]["content"]

                    if nn != node_key and nn_phrase != '':
                        sim_edge = (node_key, nn)
                        synonyms.append((nn, score))
                        num_synonym_triple += 1

                        self.node_to_node_stats[sim_edge] = score  # Need to seriously discuss on this
                        num_nns += 1

            synonym_candidates.append((node_key, synonyms))

    def load_existing_openie(self, chunk_keys: List[str]) -> Tuple[List[dict], Set[str]]:
        """
        Loads existing OpenIE results from the specified file if it exists and combines
        them with new content while standardizing indices. If the file does not exist or
        is configured to be re-initialized from scratch with the flag `force_openie_from_scratch`,
        it prepares new entries for processing.

        Args:
            chunk_keys (List[str]): A list of chunk keys that represent identifiers
                                     for the content to be processed.

        Returns:
            Tuple[List[dict], Set[str]]: A tuple where the first element is the existing OpenIE
                                         information (if any) loaded from the file, and the
                                         second element is a set of chunk keys that still need to
                                         be saved or processed.
        """

        # combine openie_results with contents already in file, if file exists
        chunk_keys_to_save = set()

        if not self.global_config.force_openie_from_scratch and os.path.isfile(self.openie_results_path):
            with open(self.openie_results_path, "r", encoding="utf-8") as file:
                openie_results = json.load(file)
            self.openie_cache_metadata = {
                key: openie_results.get(key)
                for key in ("schema_version", "index_id", "source_model", "prompt_version")
            }
            configured_source = self.global_config.get_llm_endpoint("openie").model_name
            cached_source = self.openie_cache_metadata.get("source_model")
            if (self.global_config.runtime_stage in ("openie", "all")
                    and cached_source
                    and cached_source != configured_source):
                raise ValueError(
                    "OpenIE cache source model mismatch: "
                    f"cached={cached_source}, configured={configured_source}. "
                    "Use force_openie_from_scratch=True or a different index_id/path."
                )
            all_openie_info = openie_results.get('docs', [])

            #Standardizing indices for OpenIE Files.

            renamed_openie_info = []
            for openie_info in all_openie_info:
                openie_info['idx'] = compute_mdhash_id(openie_info['passage'], 'chunk-')
                renamed_openie_info.append(openie_info)

            all_openie_info = renamed_openie_info

            existing_openie_keys = set([info['idx'] for info in all_openie_info])

            for chunk_key in chunk_keys:
                if chunk_key not in existing_openie_keys:
                    chunk_keys_to_save.add(chunk_key)
        else:
            all_openie_info = []
            chunk_keys_to_save = chunk_keys

        return all_openie_info, chunk_keys_to_save

    def merge_openie_results(self,
                             all_openie_info: List[dict],
                             chunks_to_save: Dict[str, dict],
                             ner_results_dict: Dict[str, NerRawOutput],
                             triple_results_dict: Dict[str, TripleRawOutput]) -> List[dict]:
        """
        Merges OpenIE extraction results with corresponding passage and metadata.

        This function integrates the OpenIE extraction results, including named-entity
        recognition (NER) entities and triples, with their respective text passages
        using the provided chunk keys. The resulting merged data is appended to
        the `all_openie_info` list containing dictionaries with combined and organized
        data for further processing or storage.

        Parameters:
            all_openie_info (List[dict]): A list to hold dictionaries of merged OpenIE
                results and metadata for all chunks.
            chunks_to_save (Dict[str, dict]): A dict of chunk identifiers (keys) to process
                and merge OpenIE results to dictionaries with `hash_id` and `content` keys.
            ner_results_dict (Dict[str, NerRawOutput]): A dictionary mapping chunk keys
                to their corresponding NER extraction results.
            triple_results_dict (Dict[str, TripleRawOutput]): A dictionary mapping chunk
                keys to their corresponding OpenIE triple extraction results.

        Returns:
            List[dict]: The `all_openie_info` list containing dictionaries with merged
            OpenIE results, metadata, and the passage content for each chunk.

        """

        for chunk_key, row in chunks_to_save.items():
            passage = row['content']
            try:
                chunk_openie_info = {'idx': chunk_key, 'passage': passage,
                                 'extracted_entities': ner_results_dict[chunk_key].unique_entities,
                                 'extracted_triples': triple_results_dict[chunk_key].triples}
            except Exception as e:
                logger.error(f"Error processing chunk {chunk_key}: {e}")
                chunk_openie_info = {'idx': chunk_key, 'passage': passage,
                                 'extracted_entities': [],
                                 'extracted_triples': []}
            all_openie_info.append(chunk_openie_info)

        return all_openie_info

    def save_openie_results(self, all_openie_info: List[dict]):
        """
        Computes statistics on extracted entities from OpenIE results and saves the aggregated data in a
        JSON file. The function calculates the average character and word lengths of the extracted entities
        and writes them along with the provided OpenIE information to a file.

        Parameters:
            all_openie_info : List[dict]
                List of dictionaries, where each dictionary represents information from OpenIE, including
                extracted entities.
        """

        sum_phrase_chars = sum([len(e) for chunk in all_openie_info for e in chunk['extracted_entities']])
        sum_phrase_words = sum([len(e.split()) for chunk in all_openie_info for e in chunk['extracted_entities']])
        num_phrases = sum([len(chunk['extracted_entities']) for chunk in all_openie_info])

        if len(all_openie_info) > 0:
            # Avoid division by zero if there are no phrases
            if num_phrases > 0:
                avg_ent_chars = round(sum_phrase_chars / num_phrases, 4)
                avg_ent_words = round(sum_phrase_words / num_phrases, 4)
            else:
                avg_ent_chars = 0
                avg_ent_words = 0
                
            openie_dict = {
                'schema_version': 2,
                'index_id': self.global_config.index_id,
                'source_model': self.global_config.get_llm_endpoint("openie").model_name,
                'prompt_version': self.global_config.openie_prompt_version,
                'text_processing_version': 'cn-preserving-v1',
                'docs': all_openie_info,
                'avg_ent_chars': avg_ent_chars,
                'avg_ent_words': avg_ent_words
            }
            self.openie_cache_metadata = {
                key: openie_dict[key]
                for key in ("schema_version", "index_id", "source_model", "prompt_version")
            }
            
            with open(self.openie_results_path, 'w', encoding='utf-8') as f:
                json.dump(openie_dict, f, ensure_ascii=False, indent=2)
            logger.info(f"OpenIE results saved to {self.openie_results_path}")

    def _compute_edge_weights(self):
        """
        在基础边统计上计算最终权重（可插拔策略模式）

        根据 config.edge_weight_mode 选择不同的权重策略：
        - 'uniform': 所有边权重 = 1.0
        - 'base': 使用原始共现统计（默认）
        - 'heuristic_mroaw': 启发式多维度加权（线性组合）
        - 'probabilistic_mroaw': 概率化 MROAW（贝叶斯置信度）

        使用方式：
            # 在配置中指定策略
            config = BaseConfig(edge_weight_mode='probabilistic_mroaw')
            rag = SynapseRAG(global_config=config)

            # 或运行时指定
            python main.py --edge_weight_mode probabilistic_mroaw

        消融实验配置：
            # 1. Baseline (均匀权重)
            config.edge_weight_mode = 'uniform'

            # 2. 原始统计 (HippoRAG默认)
            config.edge_weight_mode = 'base'

            # 3. 启发式 MROAW
            config.edge_weight_mode = 'heuristic_mroaw'
            config.mroaw_info_weight = 0.4
            config.mroaw_struct_weight = 0.3
            config.mroaw_semantic_weight = 0.3

            # 4. 概率化 MROAW (推荐)
            config.edge_weight_mode = 'probabilistic_mroaw'
            config.mroaw_use_info_likelihood = True
            config.mroaw_use_struct_likelihood = True
            config.mroaw_use_semantic_likelihood = True
        """
        from .edge_weight import get_edge_weight_strategy

        edge_weight_mode = getattr(self.global_config, 'edge_weight_mode', 'base')
        logger.info(f"计算边权重，策略: {edge_weight_mode}")

        # 统计信息
        edge_count = len(self.node_to_node_stats)
        logger.info(f"处理 {edge_count} 条边的权重")

        # 获取策略实例
        strategy = get_edge_weight_strategy(edge_weight_mode, self.global_config)

        if strategy is None:
            # 'base' 策略：直接使用原始统计，不做修改
            logger.info("使用默认权重（基础统计值）")
            return

        # 准备策略所需的数据
        entity_embeddings = None
        if hasattr(self, 'entity_embedding_store') and self.entity_embedding_store is not None:
            try:
                entity_keys = list(self.entity_embedding_store.get_all_ids())
                if entity_keys:
                    embeddings = self.entity_embedding_store.get_embeddings(entity_keys)
                    entity_embeddings = dict(zip(entity_keys, embeddings))
            except Exception as e:
                logger.warning(f"无法获取实体嵌入用于 MROAW: {e}")

        # 使用策略计算权重
        new_weights = strategy.compute_weights(
            node_to_node_stats=self.node_to_node_stats,
            ent_node_to_chunk_ids=self.ent_node_to_chunk_ids,
            entity_embeddings=entity_embeddings,
            graph=self.graph if self.graph.vcount() > 0 else None,
        )

        # 更新权重
        self.node_to_node_stats = new_weights

        logger.info(f"边权重计算完成，策略: {edge_weight_mode}")

        # 如果策略支持，输出统计信息
        if hasattr(strategy, 'get_stats'):
            stats = strategy.get_stats()
            logger.debug(f"MROAW 统计: {stats}")

    def augment_graph(self):
        """
        Provides utility functions to augment a graph by adding new nodes and edges.
        It ensures that the graph structure is extended to include additional components,
        and logs the completion status along with printing the updated graph information.
        """

        self.add_new_nodes()
        self.add_new_edges()

        logger.info("Graph construction completed!")
        logger.info("Graph stats: %s", self.get_graph_info())

    def add_new_nodes(self):
        """
        Adds new nodes to the graph from entity and passage embedding stores based on their attributes.

        This method identifies and adds new nodes to the graph by comparing existing nodes
        in the graph and nodes retrieved from the entity embedding store and the passage
        embedding store. The method checks attributes and ensures no duplicates are added.
        New nodes are prepared and added in bulk to optimize graph updates.
        """

        existing_nodes = {v["name"]: v for v in self.graph.vs if "name" in v.attributes()}

        entity_to_row = self.entity_embedding_store.get_all_id_to_rows()
        passage_to_row = self.chunk_embedding_store.get_all_id_to_rows()

        node_to_rows = entity_to_row
        node_to_rows.update(passage_to_row)

        new_nodes = {}
        for node_id, node in node_to_rows.items():
            node['name'] = node_id
            if node_id not in existing_nodes:
                for k, v in node.items():
                    if k not in new_nodes:
                        new_nodes[k] = []
                    new_nodes[k].append(v)

        if len(new_nodes) > 0:
            self.graph.add_vertices(n=len(next(iter(new_nodes.values()))), attributes=new_nodes)

    def add_new_edges(self):
        """
        Processes edges from `node_to_node_stats` to add them into a graph object while
        managing adjacency lists, validating edges, and logging invalid edge cases.
        """

        graph_adj_list = defaultdict(dict)
        graph_inverse_adj_list = defaultdict(dict)
        edge_source_node_keys = []
        edge_target_node_keys = []
        edge_metadata = []
        for edge, weight in self.node_to_node_stats.items():
            if edge[0] == edge[1]: continue
            graph_adj_list[edge[0]][edge[1]] = weight
            graph_inverse_adj_list[edge[1]][edge[0]] = weight

            edge_source_node_keys.append(edge[0])
            edge_target_node_keys.append(edge[1])
            edge_metadata.append({
                "weight": weight
            })

        valid_edges, valid_weights = [], {"weight": []}
        current_node_ids = set(self.graph.vs["name"])
        for source_node_id, target_node_id, edge_d in zip(edge_source_node_keys, edge_target_node_keys, edge_metadata):
            if source_node_id in current_node_ids and target_node_id in current_node_ids:
                valid_edges.append((source_node_id, target_node_id))
                weight = edge_d.get("weight", 1.0)
                valid_weights["weight"].append(weight)
            else:
                logger.warning(f"Edge {source_node_id} -> {target_node_id} is not valid.")
        self.graph.add_edges(
            valid_edges,
            attributes=valid_weights
        )

    def save_igraph(self):
        logger.info(
            f"Writing graph with {len(self.graph.vs())} nodes, {len(self.graph.es())} edges"
        )
        self.graph.write_pickle(self._graph_pickle_filename)
        logger.info(f"Saving graph completed!")

    def save_edge_base_info(self):
        """保存基础边信息（用于后续权重重算）"""
        import pickle
        edge_info = {
            'node_to_node_stats': self.node_to_node_stats,
            'ent_node_to_chunk_ids': self.ent_node_to_chunk_ids,
            'timestamp': datetime.now().isoformat(),
            'num_edges': len(self.node_to_node_stats)
        }
        path = os.path.join(self.working_dir, 'graph_edges_base.pkl')
        with open(path, 'wb') as f:
            pickle.dump(edge_info, f)
        logger.info(f"保存基础边信息到 {path}，共 {len(self.node_to_node_stats)} 条边")

    def load_edge_base_info(self) -> bool:
        """加载基础边信息"""
        import pickle
        path = os.path.join(self.working_dir, 'graph_edges_base.pkl')
        if os.path.exists(path):
            with open(path, 'rb') as f:
                edge_info = pickle.load(f)
            self.node_to_node_stats = edge_info['node_to_node_stats']
            self.ent_node_to_chunk_ids = edge_info['ent_node_to_chunk_ids']
            logger.info(f"加载基础边信息从 {path}，共 {len(self.node_to_node_stats)} 条边")
            return True
        return False

    def get_graph_info(self) -> Dict:
        """
        Obtains detailed information about the graph such as the number of nodes,
        triples, and their classifications.

        This method calculates various statistics about the graph based on the
        stores and node-to-node relationships, including counts of phrase and
        passage nodes, total nodes, extracted triples, triples involving passage
        nodes, synonymy triples, and total triples.

        Returns:
            Dict
                A dictionary containing the following keys and their respective values:
                - num_phrase_nodes: The number of unique phrase nodes.
                - num_passage_nodes: The number of unique passage nodes.
                - num_total_nodes: The total number of nodes (sum of phrase and passage nodes).
                - num_extracted_triples: The number of unique extracted triples.
                - num_triples_with_passage_node: The number of triples involving at least one
                  passage node.
                - num_synonymy_triples: The number of synonymy triples (distinct from extracted
                  triples and those with passage nodes).
                - num_total_triples: The total number of triples.
        """
        graph_info = {}

        # get # of phrase nodes
        phrase_nodes_keys = self.entity_embedding_store.get_all_ids()
        graph_info["num_phrase_nodes"] = len(set(phrase_nodes_keys))

        # get # of passage nodes
        passage_nodes_keys = self.chunk_embedding_store.get_all_ids()
        graph_info["num_passage_nodes"] = len(set(passage_nodes_keys))

        # get # of total nodes
        graph_info["num_total_nodes"] = graph_info["num_phrase_nodes"] + graph_info["num_passage_nodes"]

        # get # of extracted triples
        graph_info["num_extracted_triples"] = len(self.fact_embedding_store.get_all_ids())

        num_triples_with_passage_node = 0
        passage_nodes_set = set(passage_nodes_keys)
        num_triples_with_passage_node = sum(
            1 for node_pair in self.node_to_node_stats
            if node_pair[0] in passage_nodes_set or node_pair[1] in passage_nodes_set
        )
        graph_info['num_triples_with_passage_node'] = num_triples_with_passage_node

        graph_info['num_synonymy_triples'] = len(self.node_to_node_stats) - graph_info[
            "num_extracted_triples"] - num_triples_with_passage_node

        # get # of total triples
        graph_info["num_total_triples"] = len(self.node_to_node_stats)

        return graph_info

    def prepare_retrieval_objects(self):
        """
        Prepares various in-memory objects and attributes necessary for fast retrieval processes, such as embedding data and graph relationships, ensuring consistency
        and alignment with the underlying graph structure.
        """

        logger.info("Preparing for fast retrieval.")

        logger.info("Loading keys.")
        self.query_to_embedding: Dict = {'triple': {}, 'passage': {}}

        self.entity_node_keys: List = list(self.entity_embedding_store.get_all_ids()) # a list of phrase node keys
        self.passage_node_keys: List = list(self.chunk_embedding_store.get_all_ids()) # a list of passage node keys
        self.fact_node_keys: List = list(self.fact_embedding_store.get_all_ids())

        # Check if the graph has the expected number of nodes
        expected_node_count = len(self.entity_node_keys) + len(self.passage_node_keys)
        actual_node_count = self.graph.vcount()
        
        if expected_node_count != actual_node_count:
            logger.warning(f"Graph node count mismatch: expected {expected_node_count}, got {actual_node_count}")
            # If the graph is empty but we have nodes, we need to add them
            if actual_node_count == 0 and expected_node_count > 0:
                logger.info(f"Initializing graph with {expected_node_count} nodes")
                self.add_new_nodes()
                self.save_igraph()

        # Create mapping from node name to vertex index
        try:
            igraph_name_to_idx = {node["name"]: idx for idx, node in enumerate(self.graph.vs)} # from node key to the index in the backbone graph
            self.node_name_to_vertex_idx = igraph_name_to_idx
            
            # Check if all entity and passage nodes are in the graph
            missing_entity_nodes = [node_key for node_key in self.entity_node_keys if node_key not in igraph_name_to_idx]
            missing_passage_nodes = [node_key for node_key in self.passage_node_keys if node_key not in igraph_name_to_idx]
            
            if missing_entity_nodes or missing_passage_nodes:
                logger.warning(f"Missing nodes in graph: {len(missing_entity_nodes)} entity nodes, {len(missing_passage_nodes)} passage nodes")
                # If nodes are missing, rebuild the graph
                self.add_new_nodes()
                self.save_igraph()
                # Update the mapping
                igraph_name_to_idx = {node["name"]: idx for idx, node in enumerate(self.graph.vs)}
                self.node_name_to_vertex_idx = igraph_name_to_idx
            
            self.entity_node_idxs = [igraph_name_to_idx[node_key] for node_key in self.entity_node_keys] # a list of backbone graph node index
            self.passage_node_idxs = [igraph_name_to_idx[node_key] for node_key in self.passage_node_keys] # a list of backbone passage node index
        except Exception as e:
            logger.error(f"Error creating node index mapping: {str(e)}")
            # Initialize with empty lists if mapping fails
            self.node_name_to_vertex_idx = {}
            self.entity_node_idxs = []
            self.passage_node_idxs = []

        logger.info("Loading embeddings.")
        self.entity_embeddings = np.array(self.entity_embedding_store.get_embeddings(self.entity_node_keys))
        self.passage_embeddings = np.array(self.chunk_embedding_store.get_embeddings(self.passage_node_keys))

        self.fact_embeddings = np.array(self.fact_embedding_store.get_embeddings(self.fact_node_keys))

        all_openie_info, chunk_keys_to_process = self.load_existing_openie([])

        self.proc_triples_to_docs = {}

        for doc in all_openie_info:
            triples = flatten_facts([doc['extracted_triples']])
            for triple in triples:
                if len(triple) == 3:
                    proc_triple = tuple(text_processing(list(triple)))
                    self.proc_triples_to_docs[str(proc_triple)] = self.proc_triples_to_docs.get(str(proc_triple), set()).union(set([doc['idx']]))

        if self.ent_node_to_chunk_ids is None:
            ner_results_dict, triple_results_dict = reformat_openie_results(all_openie_info)

            # Check if the lengths match
            if not (len(self.passage_node_keys) == len(ner_results_dict) == len(triple_results_dict)):
                logger.warning(f"Length mismatch: passage_node_keys={len(self.passage_node_keys)}, ner_results_dict={len(ner_results_dict)}, triple_results_dict={len(triple_results_dict)}")
                
                # If there are missing keys, create empty entries for them
                for chunk_id in self.passage_node_keys:
                    if chunk_id not in ner_results_dict:
                        ner_results_dict[chunk_id] = NerRawOutput(
                            chunk_id=chunk_id,
                            response=None,
                            metadata={},
                            unique_entities=[]
                        )
                    if chunk_id not in triple_results_dict:
                        triple_results_dict[chunk_id] = TripleRawOutput(
                            chunk_id=chunk_id,
                            response=None,
                            metadata={},
                            triples=[]
                        )

            # prepare data_store
            chunk_triples = [[text_processing(t) for t in triple_results_dict[chunk_id].triples] for chunk_id in self.passage_node_keys]

            self.node_to_node_stats = {}
            self.ent_node_to_chunk_ids = {}
            self.add_fact_edges(self.passage_node_keys, chunk_triples)

        self.ready_to_retrieve = True

    def _get_embedding_instruction(self, purpose: str) -> str:
        if purpose == 'query_to_fact':
            configured = self.global_config.embedding_query_to_fact_instruction
        elif purpose == 'query_to_passage':
            configured = self.global_config.embedding_query_to_passage_instruction
        else:
            raise ValueError(f"Unknown embedding instruction purpose: {purpose}")
        return configured if configured is not None else get_query_instruction(purpose)

    def get_query_embeddings(self, queries: List[str] | List[QuerySolution]):
        """
        Retrieves embeddings for given queries and updates the internal query-to-embedding mapping. The method determines whether each query
        is already present in the `self.query_to_embedding` dictionary under the keys 'triple' and 'passage'. If a query is not present in
        either, it is encoded into embeddings using the embedding model and stored.

        Args:
            queries List[str] | List[QuerySolution]: A list of query strings or QuerySolution objects. Each query is checked for
            its presence in the query-to-embedding mappings.
        """

        all_query_strings = []
        for query in queries:
            if isinstance(query, QuerySolution) and (
                    query.question not in self.query_to_embedding['triple'] or query.question not in
                    self.query_to_embedding['passage']):
                all_query_strings.append(query.question)
            elif query not in self.query_to_embedding['triple'] or query not in self.query_to_embedding['passage']:
                all_query_strings.append(query)

        if len(all_query_strings) > 0:
            # get all query embeddings
            logger.info(f"Encoding {len(all_query_strings)} queries for query_to_fact.")
            query_embeddings_for_triple = self.embedding_model.batch_encode(all_query_strings,
                                                                            instruction=self._get_embedding_instruction('query_to_fact'),
                                                                            norm=True)
            for query, embedding in zip(all_query_strings, query_embeddings_for_triple):
                self.query_to_embedding['triple'][query] = embedding

            logger.info(f"Encoding {len(all_query_strings)} queries for query_to_passage.")
            query_embeddings_for_passage = self.embedding_model.batch_encode(all_query_strings,
                                                                             instruction=self._get_embedding_instruction('query_to_passage'),
                                                                             norm=True)
            for query, embedding in zip(all_query_strings, query_embeddings_for_passage):
                self.query_to_embedding['passage'][query] = embedding

    def get_fact_scores(self, query: str) -> np.ndarray:
        """
        Retrieves and computes normalized similarity scores between the given query and pre-stored fact embeddings.

        Parameters:
        query : str
            The input query text for which similarity scores with fact embeddings
            need to be computed.

        Returns:
        numpy.ndarray
            A normalized array of similarity scores between the query and fact
            embeddings. The shape of the array is determined by the number of
            facts.

        Raises:
        KeyError
            If no embedding is found for the provided query in the stored query
            embeddings dictionary.
        """
        query_embedding = self.query_to_embedding['triple'].get(query, None)
        if query_embedding is None:
            query_embedding = self.embedding_model.batch_encode(query,
                                                                instruction=self._get_embedding_instruction('query_to_fact'),
                                                                norm=True)

        # Check if there are any facts
        if len(self.fact_embeddings) == 0:
            logger.warning("No facts available for scoring. Returning empty array.")
            return np.array([])
            
        try:
            query_fact_scores = np.dot(self.fact_embeddings, query_embedding.T) # shape: (#facts, )
            query_fact_scores = np.squeeze(query_fact_scores) if query_fact_scores.ndim == 2 else query_fact_scores
            query_fact_scores = min_max_normalize(query_fact_scores)
            return query_fact_scores
        except Exception as e:
            logger.error(f"Error computing fact scores: {str(e)}")
            return np.array([])

    def dense_passage_retrieval(self, query: str) -> Tuple[np.ndarray, np.ndarray]:
        """
        Conduct dense passage retrieval to find relevant documents for a query.

        This function processes a given query using a pre-trained embedding model
        to generate query embeddings. The similarity scores between the query
        embedding and passage embeddings are computed using dot product, followed
        by score normalization. Finally, the function ranks the documents based
        on their similarity scores and returns the ranked document identifiers
        and their scores.

        Parameters
        ----------
        query : str
            The input query for which relevant passages should be retrieved.

        Returns
        -------
        tuple : Tuple[np.ndarray, np.ndarray]
            A tuple containing two elements:
            - A list of sorted document identifiers based on their relevance scores.
            - A numpy array of the normalized similarity scores for the corresponding
              documents.
        """
        query_embedding = self.query_to_embedding['passage'].get(query, None)
        if query_embedding is None:
            query_embedding = self.embedding_model.batch_encode(query,
                                                                instruction=self._get_embedding_instruction('query_to_passage'),
                                                                norm=True)
        query_doc_scores = np.dot(self.passage_embeddings, query_embedding.T)
        query_doc_scores = np.squeeze(query_doc_scores) if query_doc_scores.ndim == 2 else query_doc_scores
        query_doc_scores = min_max_normalize(query_doc_scores)

        sorted_doc_ids = np.argsort(query_doc_scores)[::-1]
        sorted_doc_scores = query_doc_scores[sorted_doc_ids.tolist()]
        return sorted_doc_ids, sorted_doc_scores

    @staticmethod
    def _lexical_tokens(text: str) -> List[str]:
        normalized = re.sub(r"(?<=\d)(?=[a-zA-Z])|(?<=[a-zA-Z])(?=\d)", " ", text.lower())
        tokens = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", normalized)
        stopwords = {
            "a", "an", "and", "are", "as", "at", "be", "by", "do", "for", "from",
            "how", "in", "is", "it", "of", "on", "or", "the", "through", "to", "what",
            "with", "according", "state", "its", "each", "rule", "rules", "手", "册", "根",
            "据", "分", "别", "如", "何", "定", "义", "请", "严", "格", "原", "文",
        }
        return [token for token in tokens if token not in stopwords]

    def lexical_passage_retrieval(self, query: str) -> Tuple[np.ndarray, np.ndarray]:
        """Rank passages with a small in-memory BM25 scorer for exact terms and rule numbers."""
        query_tokens = self._lexical_tokens(query)
        if not query_tokens:
            return np.array([], dtype=int), np.array([], dtype=float)

        documents = [
            self.chunk_embedding_store.get_row(key)["content"]
            for key in self.passage_node_keys
        ]
        tokenized = [self._lexical_tokens(document) for document in documents]
        document_count = len(tokenized)
        if document_count == 0:
            return np.array([], dtype=int), np.array([], dtype=float)
        average_length = sum(map(len, tokenized)) / document_count or 1.0
        document_frequency = Counter(
            token for tokens in tokenized for token in set(tokens)
        )
        query_frequency = Counter(query_tokens)
        scores = np.zeros(document_count, dtype=float)
        k1, b = 1.5, 0.75
        for doc_id, tokens in enumerate(tokenized):
            frequencies = Counter(tokens)
            length_normalizer = k1 * (1 - b + b * len(tokens) / average_length)
            for token, query_count in query_frequency.items():
                frequency = frequencies.get(token, 0)
                if frequency == 0:
                    continue
                df = document_frequency[token]
                inverse_frequency = math.log(1 + (document_count - df + 0.5) / (df + 0.5))
                scores[doc_id] += (
                    inverse_frequency
                    * frequency
                    * (k1 + 1)
                    / (frequency + length_normalizer)
                    * query_count
                )
        positive_ids = np.flatnonzero(scores > 0)
        if len(positive_ids) == 0:
            return np.array([], dtype=int), np.array([], dtype=float)
        order = positive_ids[np.argsort(scores[positive_ids])[::-1]]
        return order, scores[order]

    @staticmethod
    def _weighted_score_fusion(
        rankings: List[Tuple[np.ndarray, np.ndarray, float]],
    ) -> Tuple[np.ndarray, np.ndarray]:
        named_rankings = [
            (f"channel_{index}", document_ids, scores, weight)
            for index, (document_ids, scores, weight) in enumerate(rankings)
        ]
        ordered, scores, _ = SynapseRAG._weighted_score_fusion_with_contributions(named_rankings)
        return ordered, scores

    @staticmethod
    def _weighted_score_fusion_with_contributions(
        rankings: List[Tuple[str, np.ndarray, np.ndarray, float]],
    ) -> Tuple[np.ndarray, np.ndarray, Dict[int, Dict[str, float]]]:
        fused_scores: Dict[int, float] = defaultdict(float)
        contributions: Dict[int, Dict[str, float]] = defaultdict(dict)
        for channel, document_ids, scores, weight in rankings:
            if len(document_ids) == 0:
                continue
            scores = np.asarray(scores, dtype=float)
            score_min, score_max = float(np.min(scores)), float(np.max(scores))
            if score_max > score_min:
                normalized_scores = (scores - score_min) / (score_max - score_min)
            else:
                normalized_scores = np.ones_like(scores)
            for document_id, score in zip(document_ids.tolist(), normalized_scores.tolist()):
                document_id = int(document_id)
                contribution = float(weight) * float(score)
                fused_scores[document_id] += contribution
                contributions[document_id][channel] = contribution
        ordered = sorted(fused_scores, key=fused_scores.get, reverse=True)
        return (
            np.asarray(ordered, dtype=int),
            np.asarray([fused_scores[document_id] for document_id in ordered], dtype=float),
            dict(contributions),
        )

    def get_top_k_weights(self,
                          link_top_k: int,
                          all_phrase_weights: np.ndarray,
                          linking_score_map: Dict[str, float]) -> Tuple[np.ndarray, Dict[str, float]]:
        """
        This function filters the all_phrase_weights to retain only the weights for the
        top-ranked phrases in terms of the linking_score_map. It also filters linking scores
        to retain only the top `link_top_k` ranked nodes. Non-selected phrases in phrase
        weights are reset to a weight of 0.0.

        Args:
            link_top_k (int): Number of top-ranked nodes to retain in the linking score map.
            all_phrase_weights (np.ndarray): An array representing the phrase weights, indexed
                by phrase ID.
            linking_score_map (Dict[str, float]): A mapping of phrase content to its linking
                score, sorted in descending order of scores.

        Returns:
            Tuple[np.ndarray, Dict[str, float]]: A tuple containing the filtered array
            of all_phrase_weights with unselected weights set to 0.0, and the filtered
            linking_score_map containing only the top `link_top_k` phrases.
        """
        # Only rank phrases that resolve to actual graph entity nodes. Candidate
        # facts may contain an endpoint that was filtered during graph creation,
        # and a min-max-normalized top phrase may legitimately have weight 0.
        # Neither case should make retrieval fail on a count assertion.
        selected_scores = {}
        selected_vertex_ids = set()
        for phrase, score in sorted(
            linking_score_map.items(), key=lambda item: item[1], reverse=True
        ):
            phrase_key = compute_mdhash_id(content=phrase, prefix="entity-")
            phrase_id = self.node_name_to_vertex_idx.get(phrase_key)
            if phrase_id is None:
                continue
            selected_scores[phrase] = score
            selected_vertex_ids.add(phrase_id)
            if len(selected_scores) >= link_top_k:
                break

        filtered_weights = np.zeros_like(all_phrase_weights)
        for phrase_id in selected_vertex_ids:
            filtered_weights[phrase_id] = all_phrase_weights[phrase_id]

        return filtered_weights, selected_scores

    def graph_search_with_fact_entities(self, query: str,
                                        link_top_k: int,
                                        query_fact_scores: np.ndarray,
                                        top_k_facts: List[Tuple],
                                        top_k_fact_indices: List[str],
                                        passage_node_weight: float = 0.05,
                                        log_options: AgenticLogOptions | None = None,
                                        trace_collector: Optional[RetrievalTraceCollector] = None,
                                        query_trace: Optional[Dict[str, Any]] = None) -> Tuple[np.ndarray, np.ndarray]:
        """
        Computes document scores based on fact-based similarity and relevance using personalized
        PageRank (PPR) and dense retrieval models. This function combines the signal from the relevant
        facts identified with passage similarity and graph-based search for enhanced result ranking.

        Parameters:
            query (str): The input query string for which similarity and relevance computations
                need to be performed.
            link_top_k (int): The number of top phrases to include from the linking score map for
                downstream processing.
            query_fact_scores (np.ndarray): An array of scores representing fact-query similarity
                for each of the provided facts.
            top_k_facts (List[Tuple]): A list of top-ranked facts, where each fact is represented
                as a tuple of its subject, predicate, and object.
            top_k_fact_indices (List[str]): Corresponding indices or identifiers for the top-ranked
                facts in the query_fact_scores array.
            passage_node_weight (float): Default weight to scale passage scores in the graph.

        Returns:
            Tuple[np.ndarray, np.ndarray]: A tuple containing two arrays:
                - The first array corresponds to document IDs sorted based on their scores.
                - The second array consists of the PPR scores associated with the sorted document IDs.
        """

        if getattr(self.global_config, 'use_agentic_ppr_reset', False):
            if not hasattr(self, '_agentic_graph_search'):
                self._agentic_graph_search = AgenticGraphSearch(self)
            ppr_start = time.time()
            ppr_sorted_doc_ids, ppr_sorted_doc_scores = self._agentic_graph_search.search(
                query=query,
                link_top_k=link_top_k,
                query_fact_scores=query_fact_scores,
                top_k_facts=top_k_facts,
                top_k_fact_indices=top_k_fact_indices,
                passage_node_weight=passage_node_weight,
                log_options=log_options,
                trace_collector=trace_collector,
                query_trace=query_trace,
            )
            ppr_end = time.time()
            self.ppr_time += (ppr_end - ppr_start)

            assert len(ppr_sorted_doc_ids) == len(
                self.passage_node_idxs), f"Doc prob length {len(ppr_sorted_doc_ids)} != corpus length {len(self.passage_node_idxs)}"
            return ppr_sorted_doc_ids, ppr_sorted_doc_scores

        # Legacy behavior (matches upstream SynapseRAG)
        linking_score_map = {}  # from phrase to the average scores of the facts that contain the phrase
        phrase_scores = {}  # store all fact scores for each phrase regardless of whether they exist in the knowledge graph or not
        phrase_weights = np.zeros(len(self.graph.vs['name']))
        passage_weights = np.zeros(len(self.graph.vs['name']))
        number_of_occurs = np.zeros(len(self.graph.vs['name']))

        phrases_and_ids = set()

        for rank, f in enumerate(top_k_facts):
            subject_phrase = f[0].lower()
            predicate_phrase = f[1].lower()
            object_phrase = f[2].lower()
            fact_score = query_fact_scores[
                top_k_fact_indices[rank]] if query_fact_scores.ndim > 0 else query_fact_scores

            for phrase in [subject_phrase, object_phrase]:
                phrase_key = compute_mdhash_id(
                    content=phrase,
                    prefix="entity-"
                )
                phrase_id = self.node_name_to_vertex_idx.get(phrase_key, None)

                if phrase_id is not None:
                    weighted_fact_score = fact_score

                    if len(self.ent_node_to_chunk_ids.get(phrase_key, set())) > 0:
                        weighted_fact_score /= len(self.ent_node_to_chunk_ids[phrase_key])

                    phrase_weights[phrase_id] += weighted_fact_score
                    number_of_occurs[phrase_id] += 1

                phrases_and_ids.add((phrase, phrase_id))

        phrase_weights /= number_of_occurs

        for phrase, phrase_id in phrases_and_ids:
            if phrase not in phrase_scores:
                phrase_scores[phrase] = []

            phrase_scores[phrase].append(phrase_weights[phrase_id])

        # calculate average fact score for each phrase
        for phrase, scores in phrase_scores.items():
            linking_score_map[phrase] = float(np.mean(scores))

        if link_top_k:
            phrase_weights, linking_score_map = self.get_top_k_weights(link_top_k,
                                                                           phrase_weights,
                                                                           linking_score_map)  # at this stage, the length of linking_scope_map is determined by link_top_k

        #Get passage scores according to chosen dense retrieval model
        dpr_sorted_doc_ids, dpr_sorted_doc_scores = self.dense_passage_retrieval(query)
        normalized_dpr_sorted_scores = min_max_normalize(dpr_sorted_doc_scores)

        for i, dpr_sorted_doc_id in enumerate(dpr_sorted_doc_ids.tolist()):
            passage_node_key = self.passage_node_keys[dpr_sorted_doc_id]
            passage_dpr_score = normalized_dpr_sorted_scores[i]
            passage_node_id = self.node_name_to_vertex_idx[passage_node_key]
            passage_weights[passage_node_id] = passage_dpr_score * passage_node_weight
            passage_node_text = self.chunk_embedding_store.get_row(passage_node_key)["content"]
            linking_score_map[passage_node_text] = passage_dpr_score * passage_node_weight

        #Combining phrase and passage scores into one array for PPR
        node_weights = phrase_weights + passage_weights

        #Recording top 30 facts in linking_score_map
        if len(linking_score_map) > 30:
            linking_score_map = dict(sorted(linking_score_map.items(), key=lambda x: x[1], reverse=True)[:30])

        assert sum(node_weights) > 0, f'No phrases found in the graph for the given facts: {top_k_facts}'

        #Running PPR algorithm based on the passage and phrase weights previously assigned
        ppr_start = time.time()
        if trace_collector is not None and query_trace is not None:
            trace_collector.record_seed_nodes(query_trace, [
                {
                    "node_key": str(self.graph.vs[node_id]["name"]),
                    "node_type": "entity" if str(self.graph.vs[node_id]["name"]).startswith("entity-") else "chunk",
                    "reset_score": float(node_weights[node_id]),
                }
                for node_id in np.flatnonzero(node_weights > 0)
            ])
        ppr_sorted_doc_ids, ppr_sorted_doc_scores = self.run_ppr(
            node_weights,
            damping=self.global_config.damping,
            trace_collector=trace_collector,
            query_trace=query_trace,
        )
        ppr_end = time.time()

        self.ppr_time += (ppr_end - ppr_start)
        if trace_collector is not None and query_trace is not None:
            trace_collector.record_timing(query_trace, "ppr", ppr_end - ppr_start)

        assert len(ppr_sorted_doc_ids) == len(
            self.passage_node_idxs), f"Doc prob length {len(ppr_sorted_doc_ids)} != corpus length {len(self.passage_node_idxs)}"

        return ppr_sorted_doc_ids, ppr_sorted_doc_scores

    def rerank_facts(self, query: str, query_fact_scores: np.ndarray) -> Tuple[List[int], List[Tuple], dict]:
        """

        Args:

        Returns:
            top_k_fact_indicies:
            top_k_facts:
            rerank_log (dict): {'facts_before_rerank': candidate_facts, 'facts_after_rerank': top_k_facts}
                - candidate_facts (list): list of link_top_k facts (each fact is a relation triple in tuple data type).
                - top_k_facts:


        """
        candidate_top_k = max(
            self.global_config.fact_candidate_top_k,
            self.global_config.fact_rerank_top_k,
        )
        result_top_k = min(
            self.global_config.fact_rerank_top_k,
            self.global_config.linking_top_k,
        )
        
        # Check if there are any facts to rerank
        if len(query_fact_scores) == 0 or len(self.fact_node_keys) == 0:
            logger.warning("No facts available for reranking. Returning empty lists.")
            return [], [], {'facts_before_rerank': [], 'facts_after_rerank': []}
            
        try:
            # Get the top k facts by score
            if len(query_fact_scores) <= candidate_top_k:
                # If we have fewer facts than requested, use all of them
                candidate_fact_indices = np.argsort(query_fact_scores)[::-1].tolist()
            else:
                # Otherwise get the top k
                candidate_fact_indices = np.argsort(query_fact_scores)[-candidate_top_k:][::-1].tolist()
                
            # Get the actual fact IDs
            real_candidate_fact_ids = [self.fact_node_keys[idx] for idx in candidate_fact_indices]
            fact_row_dict = self.fact_embedding_store.get_rows(real_candidate_fact_ids)
            candidate_facts = [
                ast.literal_eval(fact_row_dict[fact_id]['content'])
                for fact_id in real_candidate_fact_ids
            ]

            mode = self.global_config.fact_rerank_mode
            if mode == "embedding":
                selected_indices = candidate_fact_indices[:result_top_k]
                selected_facts = candidate_facts[:result_top_k]
                reranker_dict = {"mode": "embedding", "fallback": None}
            else:
                if self.rerank_filter is None:
                    self._ensure_qa_runtime()
                    self.rerank_filter = DSPyFilter(self)
                selected_indices, selected_facts, reranker_dict = self.rerank_filter(
                    query,
                    candidate_facts,
                    candidate_fact_indices,
                    len_after_rerank=result_top_k,
                )

                if not selected_facts:
                    fallback = self.global_config.fact_rerank_fallback
                    if fallback == "embedding":
                        selected_indices = candidate_fact_indices[:result_top_k]
                        selected_facts = candidate_facts[:result_top_k]
                        reranker_dict["fallback"] = "embedding"
                        reranker_dict["fallback_reason"] = reranker_dict.get(
                            "error", "empty_selection")
                    elif fallback == "error":
                        raise RuntimeError(
                            f"Fact reranking failed: {reranker_dict.get('error', 'empty selection')}")
                    # fallback == "dpr" deliberately keeps the result empty.

            rerank_log = {
                'facts_before_rerank': candidate_facts,
                'facts_after_rerank': selected_facts,
                **reranker_dict,
                'mode': mode,
            }
            return selected_indices, selected_facts, rerank_log
            
        except Exception as e:
            logger.error(f"Error in rerank_facts: {str(e)}")
            if self.global_config.fact_rerank_fallback == "error":
                raise
            return [], [], {
                'facts_before_rerank': [],
                'facts_after_rerank': [],
                'error': str(e),
                'fallback': self.global_config.fact_rerank_fallback,
            }
    
    def run_ppr(self,
                reset_prob: np.ndarray,
                damping: float =0.5,
                trace_collector: Optional[RetrievalTraceCollector] = None,
                query_trace: Optional[Dict[str, Any]] = None) -> Tuple[np.ndarray, np.ndarray]:
        """
        Runs Personalized PageRank (PPR) on a graph and computes relevance scores for
        nodes corresponding to document passages. The method utilizes a damping
        factor for teleportation during rank computation and can take a reset
        probability array to influence the starting state of the computation.

        Parameters:
            reset_prob (np.ndarray): A 1-dimensional array specifying the reset
                probability distribution for each node. The array must have a size
                equal to the number of nodes in the graph. NaNs or negative values
                within the array are replaced with zeros.
            damping (float): A scalar specifying the damping factor for the
                computation. Defaults to 0.5 if not provided or set to `None`.

        Returns:
            Tuple[np.ndarray, np.ndarray]: A tuple containing two numpy arrays. The
                first array represents the sorted node IDs of document passages based
                on their relevance scores in descending order. The second array
                contains the corresponding relevance scores of each document passage
                in the same order.
        """

        if damping is None: damping = 0.5 # for potential compatibility
        reset_prob = np.where(np.isnan(reset_prob) | (reset_prob < 0), 0, reset_prob)
        pagerank_scores = self.graph.personalized_pagerank(
            vertices=range(len(self.node_name_to_vertex_idx)),
            damping=damping,
            directed=False,
            weights='weight',
            reset=reset_prob,
            implementation='prpack'
        )

        if trace_collector is not None and query_trace is not None:
            top_node_indices = np.argsort(np.asarray(pagerank_scores))[::-1][
                :trace_collector.max_ppr_nodes
            ]
            trace_collector.record_ppr_nodes(query_trace, [
                {
                    "node_key": str(self.graph.vs[int(node_id)]["name"]),
                    "node_type": "entity" if str(self.graph.vs[int(node_id)]["name"]).startswith("entity-") else "chunk",
                    "ppr_score": float(pagerank_scores[int(node_id)]),
                }
                for node_id in top_node_indices
            ])

        doc_scores = np.array([pagerank_scores[idx] for idx in self.passage_node_idxs])
        sorted_doc_ids = np.argsort(doc_scores)[::-1]
        sorted_doc_scores = doc_scores[sorted_doc_ids.tolist()]

        return sorted_doc_ids, sorted_doc_scores
