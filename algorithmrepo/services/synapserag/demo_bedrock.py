import os
import logging
from datetime import datetime

from synapserag import SynapseRAG
from synapserag.utils.logging_utils import setup_logging


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

    save_dir = 'outputs/bedrock'  # Define save directory for SynapseRAG objects (each LLM/Embedding model combination will create a new subdirectory)
    llm_model_name = 'bedrock/anthropic.claude-3-5-haiku-20241022-v1:0'  # Any Bedrock model name
    embedding_model_name = 'cohere.embed-multilingual-v3'  


    log_path = setup_logging(
        log_dir=os.path.join(save_dir, "logs"),
        run_name=f"demo-bedrock-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
        level=os.getenv("LOG_LEVEL", "INFO")
    )
    logger = logging.getLogger(__name__)
    logger.info("Writing demo (Bedrock) logs to %s", log_path)

    logger.info("Startup a SynapseRAG instance")
    synapserag = SynapseRAG(save_dir=save_dir,
                        llm_model_name=llm_model_name,
                        embedding_model_name=embedding_model_name)

    logger.info("Run indexing")
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

    logger.info("RAG Q&A")
    results = synapserag.rag_qa(
        queries=queries,
        gold_docs=gold_docs,
        gold_answers=answers
    )
    logger.info("Demo (Bedrock) QA results: %s", results)


if __name__ == "__main__":
    main()
