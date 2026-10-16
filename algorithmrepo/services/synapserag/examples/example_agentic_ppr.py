#!/usr/bin/env python3
"""
Minimal SynapseRAG + GEARAgentPPR demo.

This script:
1. Builds SynapseRAG from scratch on a tiny multi-hop corpus
2. Runs the new agentic retrieval loop to answer a sample query

Usage:
    python examples/example_agentic_ppr.py \\
        --save-dir outputs/example_agentic_ppr \\
        --query "When was the organization that Messi's club country belongs to established?"

Environment variables such as OPENROUTER_API_KEY (or OPENAI_API_KEY/Azure equivalents)
must be set before running because the default configuration uses online OpenIE +
OpenRouter-hosted Llama models.
"""

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

# Ensure src is on the path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from synapserag import SynapseRAG
from synapserag.utils.config_utils import BaseConfig
from synapserag.utils.logging_utils import setup_logging
from synapserag.agentic.ppr import GEARAgentPPR


DEMO_DOCS = [
    "Lionel Messi was born in Rosario, Argentina on June 24, 1987.",
    "Messi plays for FC Barcelona, which is located in Barcelona, Spain.",
    "FC Barcelona was founded in 1899 by Joan Gamper.",
    "Joan Gamper was born in Switzerland but moved to Barcelona.",
    "Barcelona is the capital of Catalonia, an autonomous community in Spain.",
    "Spain is a member of the European Union since 1986.",
    "The European Union was established by the Treaty of Rome in 1957.",
    "Cristiano Ronaldo plays for Al Nassr, which is based in Saudi Arabia.",
    "Saudi Arabia is located in the Middle East region.",
    "The Middle East is known for its oil reserves and strategic importance."
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run SynapseRAG with the GEAR agent on a toy corpus.")
    parser.add_argument("--save-dir", type=str, default="outputs/example_agentic_ppr",
                        help="Directory to store SynapseRAG artifacts.")
    parser.add_argument("--log-dir", type=str, default=None,
                        help="Optional directory for log files. Defaults to <save-dir>/logs")
    parser.add_argument("--log-level", type=str, default=os.getenv("LOG_LEVEL", "INFO"),
                        help="Logging level for the demo run.")
    parser.add_argument("--openie-mode", choices=["online", "offline"], default="online",
                        help="Whether to use online OpenIE (LLM) or offline extraction.")
    parser.add_argument("--max-iterations", type=int, default=5,
                        help="Maximum agent iterations.")
    parser.add_argument("--top-k-docs", type=int, default=8,
                        help="Documents returned per PPR pass.")
    parser.add_argument("--query", type=str,
                        default="When was the organization that Messi's club country belongs to established?",
                        help="Question for the agent.")
    parser.add_argument("--llm-base-url", type=str, default="https://openrouter.ai/api/v1",
                        help="LLM base URL (e.g., OpenRouter endpoint).")
    parser.add_argument("--llm-name", type=str, default="meta-llama/llama-3.3-70b-instruct",
                        help="LLM model name (default matches main.py).")
    parser.add_argument("--embedding-model", type=str, default="nvidia/NV-Embed-v2", help="Embedding model name.")
    parser.add_argument("--force-reindex", action="store_true",
                        help="Force OpenIE and graph rebuild even if cache exists.")
    return parser.parse_args()


def build_config(args: argparse.Namespace) -> BaseConfig:
    return BaseConfig(
        save_dir=args.save_dir,
        openie_mode=args.openie_mode,
        llm_base_url=args.llm_base_url,
        llm_name=args.llm_name,
        embedding_model_name=args.embedding_model,
        force_openie_from_scratch=args.force_reindex,
        force_index_from_scratch=args.force_reindex,
        use_agentic_ppr_reset=True,
    )


def configure_logging(log_dir: str | None, save_dir: str, log_level: str | int | None) -> str:
    resolved_dir = log_dir or os.path.join(save_dir, "logs")
    run_name = f"example_agentic_ppr-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    log_path = setup_logging(
        log_dir=resolved_dir,
        run_name=run_name,
        level=log_level,
    )
    logging.getLogger(__name__).info("Writing logs to %s", log_path)
    return log_path


def main() -> None:
    args = parse_args()
    log_path = configure_logging(args.log_dir, args.save_dir, args.log_level)
    logger = logging.getLogger(__name__)

    logger.info("Initializing SynapseRAG with save_dir=%s", args.save_dir)
    config = build_config(args)
    synapserag = SynapseRAG(global_config=config)

    logger.info("Indexing %d demo docs", len(DEMO_DOCS))
    synapserag.index(DEMO_DOCS)

    logger.info("Creating GEARAgentPPR (max_iterations=%d)", args.max_iterations)
    agent = GEARAgentPPR(synapserag, max_iterations=args.max_iterations, use_query_analysis=True)

    logger.info("Running agentic retrieval for query: %s", args.query)
    result = agent.retrieve(args.query, top_k_docs=args.top_k_docs, verbose=True)

    logger.info("Finished. iterations=%d answerable=%s", result.iterations, result.answerable)
    if result.answer:
        logger.info("Answer: %s", result.answer)
    logger.info("Reasoning: %s", result.reasoning)
    logger.info("Gist triples collected: %d", result.gist_memory.size())

    print("\n=== Demo Result ===")
    print(f"Answerable: {result.answerable}")
    if result.answer:
        print(f"Answer: {result.answer}")
    print(f"Reasoning:\n{result.reasoning}")
    print(f"Gist triple count: {result.gist_memory.size()}")
    print(f"Detailed logs written to: {log_path}")


if __name__ == "__main__":
    main()
