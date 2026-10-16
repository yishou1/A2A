import argparse
import json
import logging
import os
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from main import get_gold_answers, get_gold_docs
from src.synapserag import SynapseRAG
from src.synapserag.utils.config_utils import BaseConfig
from src.synapserag.utils.logging_utils import setup_logging
from src.synapserag.utils.misc_utils import string_to_bool

logger = logging.getLogger(__name__)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATASET_DIR = os.path.join(SCRIPT_DIR, "reproduce", "dataset")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Demo how agentic PPR resets improve SynapseRAG retrieval."
    )
    parser.add_argument(
        "--dataset",
        type=str,
        default="sample",
        help="Dataset name under reproduce/dataset (e.g., sample, musique, hotpotqa).",
    )
    parser.add_argument(
        "--llm_base_url",
        type=str,
        default="https://openrouter.ai/api/v1",
        help="LLM base URL (OpenAI-compatible).",
    )
    parser.add_argument(
        "--llm_name",
        type=str,
        default="meta-llama/llama-3.3-70b-instruct",
        help="LLM name to evaluate.",
    )
    parser.add_argument(
        "--embedding_name",
        type=str,
        default="nvidia/NV-Embed-v2",
        help="Embedding model name.",
    )
    parser.add_argument(
        "--save_dir",
        type=str,
        default="outputs",
        help="Base directory for caches and artifacts.",
    )
    parser.add_argument(
        "--log_dir",
        type=str,
        default=None,
        help="Optional directory for logs (defaults to <save_dir>/logs).",
    )
    parser.add_argument(
        "--log_level",
        type=str,
        default=os.getenv("LOG_LEVEL", "INFO"),
        help="Python logging level.",
    )
    parser.add_argument(
        "--max_questions",
        type=int,
        default=0,
        help="Optional cap on number of evaluation questions (0 = use all).",
    )
    parser.add_argument(
        "--force_index_from_scratch",
        type=str,
        default="false",
        help="Set true to rebuild embeddings/graph from scratch before testing.",
    )
    parser.add_argument(
        "--force_openie_from_scratch",
        type=str,
        default="false",
        help="Set true to re-run OpenIE even if cached triples exist.",
    )
    parser.add_argument(
        "--openie_mode",
        choices=["online", "offline"],
        default="online",
        help="Use online (API) or offline (batch) OpenIE during indexing.",
    )
    return parser.parse_args()


def resolve_save_dir(base_dir: str, dataset_name: str) -> str:
    if base_dir == "outputs":
        return os.path.join(base_dir, dataset_name, "agentic_ppr_demo")
    return f"{base_dir}_{dataset_name}"


def load_dataset(
    dataset_name: str,
) -> Tuple[List[str], List[str], Optional[List[List[str]]], List[List[str]]]:
    corpus_path = os.path.join(DATASET_DIR, f"{dataset_name}_corpus.json")
    dataset_path = os.path.join(DATASET_DIR, f"{dataset_name}.json")

    if not os.path.exists(corpus_path):
        raise FileNotFoundError(
            f"Corpus file not found for dataset '{dataset_name}': {corpus_path}"
        )
    if not os.path.exists(dataset_path):
        raise FileNotFoundError(
            f"Dataset file not found for dataset '{dataset_name}': {dataset_path}"
        )

    with open(corpus_path, "r", encoding="utf-8") as corpus_f:
        corpus = json.load(corpus_f)
    docs = [f"{doc['title']}\n{doc['text']}" for doc in corpus]

    with open(dataset_path, "r", encoding="utf-8") as dataset_f:
        samples = json.load(dataset_f)

    queries = [sample["question"] for sample in samples]
    gold_answers = get_gold_answers(samples)
    try:
        gold_docs = get_gold_docs(samples, dataset_name)
    except Exception:
        gold_docs = None

    return docs, queries, gold_docs, gold_answers


def maybe_limit_examples(
    queries: List[str],
    gold_docs: Optional[List[List[str]]],
    gold_answers: List[List[str]],
    max_questions: int,
) -> Tuple[List[str], Optional[List[List[str]]], List[List[str]]]:
    if max_questions is None or max_questions <= 0:
        return queries, gold_docs, gold_answers
    limit = min(max_questions, len(queries))
    return (
        queries[:limit],
        gold_docs[:limit] if gold_docs is not None else None,
        gold_answers[:limit],
    )


def build_base_config(
    args: argparse.Namespace,
    save_dir: str,
    corpus_len: int,
) -> BaseConfig:
    return BaseConfig(
        save_dir=save_dir,
        llm_base_url=args.llm_base_url,
        llm_name=args.llm_name,
        dataset=args.dataset,
        embedding_model_name=args.embedding_name,
        force_index_from_scratch=string_to_bool(args.force_index_from_scratch),
        force_openie_from_scratch=string_to_bool(args.force_openie_from_scratch),
        rerank_dspy_file_path="src/synapserag/prompts/dspy_prompts/filter_llama3.3-70B-Instruct.json",
        retrieval_top_k=200,
        linking_top_k=5,
        max_qa_steps=3,
        qa_top_k=5,
        graph_type="facts_and_sim_passage_node_unidirectional",
        embedding_batch_size=8,
        max_new_tokens=None,
        corpus_len=corpus_len,
        openie_mode=args.openie_mode,
        use_agentic_ppr_reset=True,
    )


def evaluate_mode(
    rag: SynapseRAG,
    *,
    mode_name: str,
    use_agentic: bool,
    queries: List[str],
    gold_docs: Optional[List[List[str]]],
    gold_answers: List[List[str]],
) -> Dict[str, Dict[str, float]]:
    rag.global_config.use_agentic_ppr_reset = use_agentic
    logger.info("Running %s (use_agentic_ppr_reset=%s)", mode_name, use_agentic)
    qa_output = rag.rag_qa(
        queries=queries, gold_docs=gold_docs, gold_answers=gold_answers
    )
    retrieval_metrics: Dict[str, float] = {}
    qa_metrics: Dict[str, float] = {}
    if len(qa_output) == 5:
        _, _, _, retrieval_metrics, qa_metrics = qa_output
    else:
        logger.warning(
            "No eval metrics returned for %s. Provide gold docs/answers to compare modes.",
            mode_name,
        )
    logger.info(
        "%s retrieval metrics: %s", mode_name, retrieval_metrics or "Unavailable"
    )
    logger.info("%s QA metrics: %s", mode_name, qa_metrics or "Unavailable")
    return {"retrieval": retrieval_metrics, "qa": qa_metrics}


def compute_deltas(
    agentic_metrics: Dict[str, float],
    baseline_metrics: Dict[str, float],
) -> Dict[str, float]:
    deltas: Dict[str, float] = {}
    numeric_keys = set(agentic_metrics.keys()) & set(baseline_metrics.keys())
    for key in sorted(numeric_keys):
        agentic_val = agentic_metrics[key]
        baseline_val = baseline_metrics[key]
        if isinstance(agentic_val, (int, float)) and isinstance(
            baseline_val, (int, float)
        ):
            deltas[key] = round(agentic_val - baseline_val, 4)
    return deltas


def main():
    args = parse_args()

    docs, queries, gold_docs, gold_answers = load_dataset(args.dataset)
    queries, gold_docs, gold_answers = maybe_limit_examples(
        queries, gold_docs, gold_answers, args.max_questions
    )
    logger.info(
        "Loaded %d docs and %d labeled questions from %s",
        len(docs),
        len(queries),
        args.dataset,
    )

    save_dir = resolve_save_dir(args.save_dir, args.dataset)
    os.makedirs(save_dir, exist_ok=True)
    log_dir = args.log_dir or os.path.join(save_dir, "logs")
    run_name = (
        f"agentic-ppr-demo-{args.dataset}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    )
    log_path = setup_logging(
        log_dir=log_dir, run_name=run_name, level=args.log_level
    )
    logger.info("Writing logs to %s", log_path)

    base_config = build_base_config(args, save_dir, len(docs))
    synapserag = SynapseRAG(global_config=base_config)
    synapserag.index(docs)

    agentic_results = evaluate_mode(
        synapserag,
        mode_name="Agentic PPR",
        use_agentic=True,
        queries=queries,
        gold_docs=gold_docs,
        gold_answers=gold_answers,
    )
    baseline_results = evaluate_mode(
        synapserag,
        mode_name="Baseline PPR",
        use_agentic=False,
        queries=queries,
        gold_docs=gold_docs,
        gold_answers=gold_answers,
    )

    retrieval_delta = compute_deltas(
        agentic_results["retrieval"], baseline_results["retrieval"]
    )
    qa_delta = compute_deltas(agentic_results["qa"], baseline_results["qa"])

    if retrieval_delta:
        logger.info(
            "Retrieval improvements (Agentic - Baseline): %s", retrieval_delta
        )
    if qa_delta:
        logger.info("QA improvements (Agentic - Baseline): %s", qa_delta)
    if not retrieval_delta and not qa_delta:
        logger.warning(
            "No overlapping metrics to compare. Ensure gold docs/answers are available."
        )


if __name__ == "__main__":
    main()
