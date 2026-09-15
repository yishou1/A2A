import os
from typing import List
import json
import argparse
import logging
from datetime import datetime

from src.synapserag import SynapseRAG
from src.synapserag.utils.config_utils import BaseConfig
from src.synapserag.utils.logging_utils import setup_logging

logger = logging.getLogger(__name__)

def main():



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

    save_dir = 'outputs/local_test'  # Define save directory for SynapseRAG objects (each LLM/Embedding model combination will create a new subdirectory)
    llm_model_name = 'Transformers/Qwen/Qwen2.5-7B-Instruct'  # Any OpenAI model name
    embedding_model_name = 'Transformers/BAAI/bge-m3'  # Embedding model name (NV-Embed, GritLM or Contriever for now)

    global_config = BaseConfig(
        openie_mode='Transformers-offline',
        information_extraction_model_name='Transformers/Qwen/Qwen2.5-7B-Instruct'
    )

    log_path = setup_logging(
        log_dir=os.path.join(save_dir, "logs"),
        run_name=f"tests-transformers-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
        level=os.getenv("LOG_LEVEL", "INFO")
    )
    logger.info("Writing transformers test logs to %s", log_path)

    # Startup a SynapseRAG instance
    synapserag = SynapseRAG(global_config,
                        save_dir=save_dir,
                        llm_model_name=llm_model_name,
                        embedding_model_name=embedding_model_name,
                        )

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
        "Transformers test QA metrics: %s",
        synapserag.rag_qa(queries=queries,
                          gold_docs=gold_docs,
                          gold_answers=answers)[-2:]
    )

if __name__ == "__main__":
    main()
