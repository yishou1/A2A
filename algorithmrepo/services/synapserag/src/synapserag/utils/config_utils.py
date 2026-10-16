import os
from dataclasses import dataclass, field
from typing import (
    Dict,
    Literal,
    Union,
    Optional
)

from .logging_utils import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class LLMEndpointConfig:
    """Role-specific LLM endpoint without embedding credentials or index state."""

    model_name: str
    base_url: Optional[str] = None
    provider: str = "openai_compatible"
    api_key_env: Optional[str] = None
    azure_endpoint: Optional[str] = None
    api_version: Optional[str] = None
    timeout_seconds: int = 300
    max_retries: int = 2
    temperature: float = 0.0
    max_tokens: Optional[int] = 2048
    response_format: Optional[dict] = None
    extra_body: Dict = field(default_factory=dict)


@dataclass
class BaseConfig:
    """One and only configuration."""
    # LLM specific attributes 
    llm_name: str = field(
        default="meta-llama/llama-3.3-70b-instruct",
        metadata={"help": "Class name indicating which LLM model to use."}
    )
    llm_base_url: str = field(
        default="https://openrouter.ai/api/v1",
        metadata={"help": "Base URL for the LLM model, if none, means using OPENAI service."}
    )
    embedding_base_url: str = field(
        default=None,
        metadata={"help": "Base URL for an OpenAI compatible embedding model, if none, means using OPENAI service."}
    )
    azure_endpoint: str = field(
        default=None,
        metadata={"help": "Azure Endpoint URI for the LLM model, if none, uses OPENAI service directly."}
    )
    azure_api_version: str = field(
        default=None,
        metadata={"help": "Azure OpenAI API version, for example 2024-12-01-preview."}
    )
    azure_embedding_endpoint: str = field(
        default=None,
        metadata={"help": "Azure Endpoint URI for the OpenAI embedding model, if none, uses OPENAI service directly."}
    )
    max_new_tokens: Union[None, int] = field(
        default=2048,
        metadata={"help": "Max new tokens to generate in each inference."}
    )
    num_gen_choices: int = field(
        default=1,
        metadata={"help": "How many chat completion choices to generate for each input message."}
    )
    seed: Union[None, int] = field(
        default=None,
        metadata={"help": "Random seed."}
    )
    temperature: float = field(
        default=0,
        metadata={"help": "Temperature for sampling in each inference."}
    )
    response_format: Union[dict, None] = field(
        default_factory=lambda: { "type": "json_object" },
        metadata={"help": "Specifying the format that the model must output."}
    )
    openie_llm: Optional[LLMEndpointConfig] = field(
        default=None,
        metadata={"help": "Dedicated OpenIE endpoint. Falls back to the legacy llm_* fields."}
    )
    qa_llm: Optional[LLMEndpointConfig] = field(
        default=None,
        metadata={"help": "Dedicated QA endpoint. Falls back to the legacy llm_* fields."}
    )
    rerank_llm: Optional[LLMEndpointConfig] = field(
        default=None,
        metadata={"help": "Dedicated rerank endpoint. Defaults to sharing qa_llm."}
    )
    
    ## LLM specific attributes -> Async hyperparameters
    max_retry_attempts: int = field(
        default=5,
        metadata={"help": "Max number of retry attempts for an asynchronous API calling."}
    )
    # Storage specific attributes
    force_openie_from_scratch: bool = field(
        default=False,
        metadata={"help": "If set to True, will ignore all existing openie files and rebuild them from scratch."}
    )
    openie_prompt_version: str = field(
        default="ner-triple-v1",
        metadata={"help": "Version tag persisted with OpenIE cache metadata."}
    )

    # Storage specific attributes
    force_index_from_scratch: bool = field(
        default=False,
        metadata={"help": "If set to True, will ignore all existing storage files and graph data and will rebuild from scratch."}
    )
    force_rebuild_graph: bool = field(
        default=False,
        metadata={"help": "If set to True, will force rebuild graph topology from scratch, ignoring cached edge info."}
    )
    rerank_dspy_file_path: str = field(
        default=None,
        metadata={"help": "Path to the rerank dspy file."}
    )
    passage_node_weight: float = field(
        default=0.05,
        metadata={"help": "Multiplicative factor that modified the passage node weights in PPR."}
    )
    lambda_mix: float = field(
        default=0.5,
        metadata={"help": "Weight to mix phrase vs passage resets when softmax fusion is enabled."}
    )
    phrase_temp: float = field(
        default=1.0,
        metadata={"help": "Temperature used in softmax over phrase nodes for reset weighting."}
    )
    passage_temp: float = field(
        default=1.0,
        metadata={"help": "Temperature used in softmax over passage nodes for reset weighting."}
    )
    dpr_topP_for_reset: int = field(
        default=200,
        metadata={"help": "Only DPR top-P passages contribute to reset weights when set >0."}
    )
    use_softmax_fusion: bool = field(
        default=True,
        metadata={"help": "Whether to normalize phrase/passage weights with softmax before mixing."}
    )
    use_masked_softmax: bool = field(
        default=True,
        metadata={"help": "Whether softmax should ignore zero entries (masked)."}
    )
    use_agentic_ppr_reset: bool = field(
        default=True,
        metadata={"help": "Enable enhanced agentic PPR reset (softmax + topP). Disable to run legacy baseline."}
    )
    save_openie: bool = field(
        default=True,
        metadata={"help": "If set to True, will save the OpenIE model to disk."}
    )
    
    # Preprocessing specific attributes
    text_preprocessor_class_name: str = field(
        default="TextPreprocessor",
        metadata={"help": "Name of the text-based preprocessor to use in preprocessing."}
    )
    preprocess_encoder_name: str = field(
        default="gpt-4o",
        metadata={"help": "Name of the encoder to use in preprocessing (currently implemented specifically for doc chunking)."}
    )
    preprocess_chunk_overlap_token_size: int = field(
        default=128,
        metadata={"help": "Number of overlap tokens between neighbouring chunks."}
    )
    preprocess_chunk_max_token_size: int = field(
        default=None,
        metadata={"help": "Max number of tokens each chunk can contain. If set to None, the whole doc will treated as a single chunk."}
    )
    preprocess_chunk_func: Literal["by_token", "by_word"] = field(default='by_token')
    
    
    # Information extraction specific attributes
    information_extraction_model_name: Literal["openie_openai_gpt", ] = field(
        default="openie_openai_gpt",
        metadata={"help": "Class name indicating which information extraction model to use."}
    )
    openie_mode: Literal["offline", "online", "Transformers-offline"] = field(
        default="online",
        metadata={"help": "Mode of the OpenIE model to use."}
    )
    skip_graph: bool = field(
        default=False,
        metadata={"help": "Whether to skip graph construction or not. Set it to be true when running vllm offline indexing for the first time."}
    )
    
    
    # Embedding specific attributes
    embedding_model_name: str = field(
        default="nvidia/NV-Embed-v2",
        metadata={"help": "Class name indicating which embedding model to use."}
    )
    embedding_batch_size: int = field(
        default=16,
        metadata={"help": "Batch size of calling embedding model."}
    )
    embedding_return_as_normalized: bool = field(
        default=True,
        metadata={"help": "Whether to normalize encoded embeddings not."}
    )
    embedding_max_seq_len: int = field(
        default=2048,
        metadata={"help": "Max sequence length for the embedding model."}
    )
    embedding_model_dtype: Literal["float16", "float32", "bfloat16", "auto"] = field(
        default="auto",
        metadata={"help": "Data type for local embedding model."}
    )
    embedding_api_key_env: Optional[str] = field(
        default="SYNAPSERAG_EMBEDDING_API_KEY",
        metadata={"help": "Environment variable containing the embedding API key."}
    )
    embedding_timeout_seconds: int = field(
        default=300,
        metadata={"help": "Timeout for OpenAI-compatible embedding requests."}
    )
    embedding_query_instruction_mode: Literal["none", "prefix", "provider"] = field(
        default="prefix",
        metadata={"help": "How query instructions are supplied to the embedding provider."}
    )
    embedding_document_instruction: str = field(default="")
    embedding_query_to_fact_instruction: Optional[str] = field(default=None)
    embedding_query_to_passage_instruction: Optional[str] = field(default=None)
    
    
    
    # Graph construction specific attributes
    synonymy_edge_topk: int = field(
        default=2047,
        metadata={"help": "k for knn retrieval in buiding synonymy edges."}
    )
    synonymy_edge_query_batch_size: int = field(
        default=1000,
        metadata={"help": "Batch size for query embeddings for knn retrieval in buiding synonymy edges."}
    )
    synonymy_edge_key_batch_size: int = field(
        default=10000,
        metadata={"help": "Batch size for key embeddings for knn retrieval in buiding synonymy edges."}
    )
    synonymy_edge_sim_threshold: float = field(
        default=0.8,
        metadata={"help": "Similarity threshold to include candidate synonymy nodes."}
    )
    is_directed_graph: bool = field(
        default=False,
        metadata={"help": "Whether the graph is directed or not."}
    )

    # === Edge Weight Strategy (MROAW) ===
    edge_weight_mode: Literal["uniform", "base", "heuristic_mroaw", "probabilistic_mroaw"] = field(
        default="base",
        metadata={"help": "Edge weight calculation strategy: 'uniform' (all=1), 'base' (raw stats), 'heuristic_mroaw' (linear weighting), 'probabilistic_mroaw' (Bayesian confidence)."}
    )

    # MROAW 通用参数
    mroaw_use_info_likelihood: bool = field(
        default=True,
        metadata={"help": "Whether to use information likelihood (entity rarity) in MROAW."}
    )
    mroaw_use_struct_likelihood: bool = field(
        default=True,
        metadata={"help": "Whether to use structural likelihood (path support) in MROAW."}
    )
    mroaw_use_semantic_likelihood: bool = field(
        default=True,
        metadata={"help": "Whether to use semantic likelihood (embedding similarity) in MROAW."}
    )

    # 信息似然参数
    mroaw_info_alpha: float = field(
        default=1.0,
        metadata={"help": "Sensitivity parameter for information likelihood (IDF-style)."}
    )

    # 结构似然参数
    mroaw_struct_lambda: float = field(
        default=2.0,
        metadata={"help": "Decay parameter for structural likelihood (path count)."}
    )
    mroaw_struct_max_path_length: int = field(
        default=3,
        metadata={"help": "Maximum path length to consider for structural support."}
    )

    # 语义似然参数
    mroaw_semantic_mu: float = field(
        default=0.5,
        metadata={"help": "Optimal similarity center for semantic likelihood Gaussian."}
    )
    mroaw_semantic_sigma: float = field(
        default=0.25,
        metadata={"help": "Width of Gaussian for semantic likelihood."}
    )

    # 先验参数
    mroaw_prior_smoothing: float = field(
        default=1.0,
        metadata={"help": "Laplace smoothing for prior probability calculation."}
    )

    # 启发式 MROAW 专用参数（线性权重）
    mroaw_info_weight: float = field(
        default=0.4,
        metadata={"help": "Weight for information dimension in heuristic MROAW (α)."}
    )
    mroaw_struct_weight: float = field(
        default=0.3,
        metadata={"help": "Weight for structural dimension in heuristic MROAW (β)."}
    )
    mroaw_semantic_weight: float = field(
        default=0.3,
        metadata={"help": "Weight for semantic dimension in heuristic MROAW (γ)."}
    )


    # Retrieval specific attributes
    linking_top_k: int = field(
        default=5,
        metadata={"help": "The number of linked nodes at each retrieval step"}
    )
    fact_rerank_mode: Literal["embedding", "llm", "hybrid"] = field(
        default="hybrid",
        metadata={"help": "Fact selection mode before graph search."}
    )
    fact_candidate_top_k: int = field(
        default=20,
        metadata={"help": "Embedding-ranked fact candidate pool size."}
    )
    fact_rerank_top_k: int = field(
        default=5,
        metadata={"help": "Maximum number of facts retained after reranking."}
    )
    fact_rerank_fallback: Literal["embedding", "dpr", "error"] = field(
        default="embedding",
        metadata={"help": "Fallback used when LLM fact reranking fails."}
    )
    fact_rerank_min_score: Optional[float] = field(
        default=None,
        metadata={"help": "Optional score threshold for accepting an empty LLM selection."}
    )
    retrieval_top_k: int = field(
        default=200,
        metadata={"help": "Retrieving k documents at each step"}
    )
    retrieval_fusion_mode: Literal["graph", "hybrid"] = field(
        default="graph",
        metadata={"help": "Fuse graph, dense, and lexical passage rankings when set to hybrid."}
    )
    retrieval_graph_weight: float = field(default=0.2)
    retrieval_dense_weight: float = field(default=0.2)
    retrieval_lexical_weight: float = field(default=0.6)
    damping: float = field(
        default=0.5,
        metadata={"help": "Damping factor for ppr algorithm."}
    )
    
    
    # QA specific attributes
    max_qa_steps: int = field(
        default=1,
        metadata={"help": "For answering a single question, the max steps that we use to interleave retrieval and reasoning."}
    )
    qa_top_k: int = field(
        default=5,
        metadata={"help": "Feeding top k documents to the QA model for reading."}
    )
    
    # Save dir (highest level directory)
    save_dir: str = field(
        default=None,
        metadata={"help": "Directory to save all related information. If it's given, will overwrite all default save_dir setups. If it's not given, then if we're not running specific datasets, default to `outputs`, otherwise, default to a dataset-customized output dir."}
    )
    index_id: Optional[str] = field(
        default=None,
        metadata={"help": "Stable index identity independent of OpenIE and QA model names."}
    )
    index_dir: Optional[str] = field(
        default=None,
        metadata={"help": "Explicit index directory override."}
    )
    openie_results_path: Optional[str] = field(
        default=None,
        metadata={"help": "Explicit OpenIE result path override."}
    )
    allow_legacy_index_layout: bool = field(
        default=True,
        metadata={"help": "Allow loading the historical <llm>_<embedding> directory layout."}
    )
    runtime_stage: Literal["all", "openie", "build-index", "query"] = field(
        default="all",
        metadata={"help": "Controls which model roles are initialized by SynapseRAG."}
    )
    
    
    
    # Dataset running specific attributes
    ## Dataset running specific attributes -> General
    dataset: Optional[Literal['hotpotqa', 'hotpotqa_train', 'musique', '2wikimultihopqa']] = field(
        default=None,
        metadata={"help": "Dataset to use. If specified, it means we will run specific datasets. If not specified, it means we're running freely."}
    )
    ## Dataset running specific attributes -> Graph
    graph_type: Literal[
        'dpr_only', 
        'entity', 
        'passage_entity', 'relation_aware_passage_entity',
        'passage_entity_relation', 
        'facts_and_sim_passage_node_unidirectional',
    ] = field(
        default="facts_and_sim_passage_node_unidirectional",
        metadata={"help": "Type of graph to use in the experiment."}
    )
    corpus_len: Optional[int] = field(
        default=None,
        metadata={"help": "Length of the corpus to use."}
    )
    
    
    def __post_init__(self):
        if self.save_dir is None: # If save_dir not given
            if self.dataset is None: self.save_dir = 'outputs' # running freely
            else: self.save_dir = os.path.join('outputs', self.dataset) # customize your dataset's output dir here
        logger.debug(f"Initializing the highest level of save_dir to be {self.save_dir}")

    def get_llm_endpoint(self, role: Literal["openie", "rerank", "qa"]) -> LLMEndpointConfig:
        """Resolve a role endpoint while preserving legacy llm_* compatibility."""
        endpoint = {
            "openie": self.openie_llm,
            "qa": self.qa_llm,
            "rerank": self.rerank_llm or self.qa_llm,
        }[role]
        if endpoint is not None:
            return endpoint

        return LLMEndpointConfig(
            model_name=self.llm_name,
            base_url=self.llm_base_url,
            azure_endpoint=self.azure_endpoint,
            api_version=self.azure_api_version,
            max_retries=self.max_retry_attempts,
            temperature=self.temperature,
            max_tokens=self.max_new_tokens,
            response_format=self.response_format,
        )
