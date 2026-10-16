import os
import logging
from datetime import datetime

from src.synapserag import SynapseRAG
from src.synapserag.utils.config_utils import BaseConfig, LLMEndpointConfig
from src.synapserag.utils.logging_utils import setup_logging

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

    save_dir = os.getenv("SYNAPSERAG_SAVE_DIR", "outputs/api")
    openie_model_name = os.getenv("SYNAPSERAG_OPENIE_MODEL", "qwen-plus")
    openie_base_url = os.getenv(
        "SYNAPSERAG_OPENIE_BASE_URL",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    embedding_model_name = os.getenv(
        "SYNAPSERAG_EMBEDDING_NAME",
        os.getenv("SYNAPSERAG_EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-0.6B"),
    )
    embedding_base_url = os.getenv(
        "SYNAPSERAG_EMBEDDING_BASE_URL",
        "http://127.0.0.1:8000/v1",
    )
    qa_model_name = os.getenv("SYNAPSERAG_QA_MODEL", "qwen3:1.7b")
    qa_base_url = os.getenv(
        "SYNAPSERAG_QA_BASE_URL", "http://127.0.0.1:11434/v1")
    stage = os.getenv("SYNAPSERAG_STAGE", "all")

    log_path = setup_logging(
        log_dir=os.path.join(save_dir, "logs"),
        run_name=f"demo-openai-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
        level=os.getenv("LOG_LEVEL", "INFO")
    )
    logger = logging.getLogger(__name__)
    logger.info("Writing demo (OpenAI) logs to %s", log_path)

    config = BaseConfig(
        save_dir=save_dir,
        index_id=os.getenv("SYNAPSERAG_INDEX_ID", "demo-openai-qwen3-v1"),
        runtime_stage=stage,
        openie_llm=LLMEndpointConfig(
            model_name=openie_model_name,
            base_url=openie_base_url,
            api_key_env="SYNAPSERAG_OPENIE_API_KEY",
            temperature=0.0,
        ),
        qa_llm=LLMEndpointConfig(
            model_name=qa_model_name,
            base_url=qa_base_url,
            api_key_env="SYNAPSERAG_QA_API_KEY",
            temperature=0.1,
            max_tokens=1024,
            extra_body={"reasoning_effort": "none"},
        ),
        embedding_model_name=embedding_model_name,
        embedding_base_url=embedding_base_url,
        fact_rerank_mode="hybrid",
        fact_candidate_top_k=20,
        fact_rerank_top_k=5,
    )
    synapserag = SynapseRAG(global_config=config)

    if stage == "openie":
        logger.info("OpenIE report: %s", synapserag.extract_openie(docs))
        return
    if stage == "build-index":
        logger.info("Index report: %s", synapserag.build_index(docs))
        return
    if stage == "query":
        synapserag.load_index()
    else:
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

    results = synapserag.rag_qa(queries=queries,
                                gold_docs=gold_docs,
                                gold_answers=answers)
    logger.info("Demo (OpenAI) QA results: %s", results)

if __name__ == "__main__":
    main()
