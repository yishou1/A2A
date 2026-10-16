import argparse
import logging
import os
from datetime import datetime

from src.synapserag import SynapseRAG
from src.synapserag.utils.config_utils import BaseConfig
from src.synapserag.utils.logging_utils import setup_logging

logger = logging.getLogger(__name__)


def parse_args():
    parser = argparse.ArgumentParser(description="Run SynapseRAG OpenAI-style smoke tests.")
    parser.add_argument('--save_dir', type=str, default='outputs/openai_test', help='输出目录')
    parser.add_argument('--llm_base_url', type=str, default='https://openrouter.ai/api/v1', help='LLM base URL')
    parser.add_argument('--llm_name', type=str, default='meta-llama/llama-3.3-70b-instruct', help='LLM name')
    parser.add_argument('--embedding_name', type=str, default='nvidia/NV-Embed-v2', help='embedding model name')
    parser.add_argument('--embedding_base_url', type=str, default=None, help='embedding base URL')
    parser.add_argument('--disable_agentic_ppr', action='store_true', help='设置后禁用 Agentic PPR，走基础版本')
    parser.add_argument('--log_level', type=str, default=os.getenv("LOG_LEVEL", "INFO"),
                        help='日志级别（例如 DEBUG/INFO/WARNING）。会同步给 agentic 相关日志。')
    return parser.parse_args()


def main():
    args = parse_args()
    use_agentic = not args.disable_agentic_ppr

    # Prepare datasets and evaluation
    docs = [
        "Oliver Badman is a politician.",
        "George Rankin is a politician.",
        "Thomas Marwick is a politician.",
        "Cinderella attended the royal ball.",
        "The prince used the lost glass slipper to search the kingdom.",
        "When the slipper fit perfectly, Cinderella was reunited with the prince.",
        "Erik Hort's birthplace is Montebello.",
        "Marina is bom in Minsk.",
        "Montebello is a part of Rockland County."
    ]

    save_dir = args.save_dir  # Define save directory for SynapseRAG objects (each LLM/Embedding model combination will create a new subdirectory)
    llm_model_name = args.llm_name  # Any OpenAI model name
    embedding_model_name = args.embedding_name  # Embedding model name

    log_path = setup_logging(
        log_dir=os.path.join(save_dir, "logs"),
        run_name=f"tests-openai-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
        level=args.log_level
    )
    logger.info("Writing OpenAI test logs to %s", log_path)

    # Ensure nested modules (agentic memory/reasoning/retrieval) honor the same verbosity.
    agentic_logger = logging.getLogger("synapserag.agentic")
    if agentic_logger:
        agentic_logger.setLevel(getattr(logging, str(args.log_level).upper(), logging.INFO))

    def build_rag():
        config = BaseConfig(
            use_agentic_ppr_reset=use_agentic,
            embedding_base_url=args.embedding_base_url
        )
        return SynapseRAG(save_dir=save_dir,
                          global_config=config,
                          llm_model_name=llm_model_name,
                          embedding_model_name=embedding_model_name,
                          llm_base_url=args.llm_base_url)

    synapserag = build_rag()

    # Run indexing
    synapserag.index(docs=docs)

    # Separate Retrieval & QA
    queries = [
        "What is George Rankin's occupation?",
        "How did Cinderella reach her happy ending?",
        "What county is Erik Hort's birthplace a part of?"
    ]

    # For Evaluation
    answers = [
        ["Politician"],
        ["By going to the ball."],
        ["Rockland County"]
    ]

    gold_docs = [
        ["George Rankin is a politician."],
        ["Cinderella attended the royal ball.",
         "The prince used the lost glass slipper to search the kingdom.",
         "When the slipper fit perfectly, Cinderella was reunited with the prince."],
        ["Erik Hort's birthplace is Montebello.",
         "Montebello is a part of Rockland County."]
    ]

    logger.info(
        "Initial OpenAI test QA metrics: %s",
        synapserag.rag_qa(queries=queries,
                          gold_docs=gold_docs,
                          gold_answers=answers)[-2:]
    )

    # Startup a SynapseRAG instance
    synapserag = build_rag()

    logger.info(
        "Repeated OpenAI test QA metrics (cache hit): %s",
        synapserag.rag_qa(queries=queries,
                          gold_docs=gold_docs,
                          gold_answers=answers)[-2:]
    )

    # Startup a SynapseRAG instance
    synapserag = build_rag()

    new_docs = [
        "Tom Hort's birthplace is Montebello.",
        "Sam Hort's birthplace is Montebello.",
        "Bill Hort's birthplace is Montebello.",
        "Cam Hort's birthplace is Montebello.",
        "Montebello is a part of Rockland County.."]

    # Run indexing
    synapserag.index(docs=new_docs)

    logger.info(
        "OpenAI test QA metrics after adding docs: %s",
        synapserag.rag_qa(queries=queries,
                          gold_docs=gold_docs,
                          gold_answers=answers)[-2:]
    )

    docs_to_delete = [
        "Tom Hort's birthplace is Montebello.",
        "Sam Hort's birthplace is Montebello.",
        "Bill Hort's birthplace is Montebello.",
        "Cam Hort's birthplace is Montebello.",
        "Montebello is a part of Rockland County.."
    ]

    synapserag.delete(docs_to_delete)

    logger.info(
        "OpenAI test QA metrics after deletion: %s",
        synapserag.rag_qa(queries=queries,
                          gold_docs=gold_docs,
                          gold_answers=answers)[-2:]
    )

if __name__ == "__main__":
    main()
