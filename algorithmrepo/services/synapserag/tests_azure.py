import os
from typing import List
import json
import argparse
import logging
from datetime import datetime

from src.synapserag import SynapseRAG
from src.synapserag.utils.logging_utils import setup_logging

logger = logging.getLogger(__name__)

def main():
    parser = argparse.ArgumentParser(description="Testing SynapseRAG")
    parser.add_argument('--azure_endpoint', type=str, default=None, help='Azure Endpoint URL')
    parser.add_argument('--azure_embedding_endpoint', type=str, default=None, help='Azure Embedding Endpoint')
    args = parser.parse_args()

    azure_endpoint = args.azure_endpoint
    azure_embedding_endpoint = args.azure_embedding_endpoint

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

    save_dir = 'outputs/azure_test'  # Define save directory for SynapseRAG objects (each LLM/Embedding model combination will create a new subdirectory)
    llm_model_name = 'meta-llama/llama-3.3-70b-instruct'  # Any OpenAI model name
    embedding_model_name = 'text-embedding-3-small'  # Embedding model name (NV-Embed, GritLM or Contriever for now)

    log_path = setup_logging(
        log_dir=os.path.join(save_dir, "logs"),
        run_name=f"tests-azure-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
        level=os.getenv("LOG_LEVEL", "INFO")
    )
    logger.info("Writing Azure test logs to %s", log_path)

    # Startup a SynapseRAG instance
    synapserag = SynapseRAG(save_dir=save_dir,
                        llm_model_name=llm_model_name,
                        embedding_model_name=embedding_model_name,
                        azure_endpoint=azure_endpoint,
                        azure_embedding_endpoint=azure_embedding_endpoint
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
        "Initial Azure test QA metrics: %s",
        synapserag.rag_qa(queries=queries,
                          gold_docs=gold_docs,
                          gold_answers=answers)[-2:]
    )

    # Startup a SynapseRAG instance
    synapserag = SynapseRAG(save_dir=save_dir,
                        llm_model_name=llm_model_name,
                        embedding_model_name=embedding_model_name,
                        azure_endpoint="https://bernal-synapserag.openai.azure.com/openai/deployments/meta-llama-3.3-70b-instruct/chat/completions?api-version=2025-01-01-preview",
                        azure_embedding_endpoint="https://bernal-synapserag.openai.azure.com/openai/deployments/text-embedding-3-small/embeddings?api-version=2023-05-15"
                        )

    logger.info(
        "Azure test QA metrics after redeploy: %s",
        synapserag.rag_qa(queries=queries,
                          gold_docs=gold_docs,
                          gold_answers=answers)[-2:]
    )

    # Startup a SynapseRAG instance
    synapserag = SynapseRAG(save_dir=save_dir,
                        llm_model_name=llm_model_name,
                        embedding_model_name=embedding_model_name,
                        azure_endpoint="https://bernal-synapserag.openai.azure.com/openai/deployments/meta-llama-3.3-70b-instruct/chat/completions?api-version=2025-01-01-preview",
                        azure_embedding_endpoint="https://bernal-synapserag.openai.azure.com/openai/deployments/text-embedding-3-small/embeddings?api-version=2023-05-15"
                        )

    new_docs = [
        "Tom Hort's birthplace is Montebello.",
        "Sam Hort's birthplace is Montebello.",
        "Bill Hort's birthplace is Montebello.",
        "Cam Hort's birthplace is Montebello.",
        "Montebello is a part of Rockland County.."]

    # Run indexing
    synapserag.index(docs=new_docs)

    logger.info(
        "Azure test QA metrics after adding docs: %s",
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
        "Azure test QA metrics after deletion: %s",
        synapserag.rag_qa(queries=queries,
                          gold_docs=gold_docs,
                          gold_answers=answers)[-2:]
    )

if __name__ == "__main__":
    main()
