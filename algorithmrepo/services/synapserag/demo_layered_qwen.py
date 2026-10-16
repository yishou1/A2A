#!/usr/bin/env python3
"""Three-stage SynapseRAG demo: strong OpenIE, Qwen embedding, local Qwen QA."""

import argparse
import os

from src.synapserag import SynapseRAG
from src.synapserag.utils.config_utils import BaseConfig, LLMEndpointConfig


DOCS = [
    "顾明澈是青岚研究所的创办人。",
    "青岚研究所研发了星桥系统。",
    "星桥系统被北辰机器人公司收购。",
    "北辰机器人公司的总部位于苏州。",
]

QUESTION = "收购顾明澈创办机构所研发系统的公司，总部在哪里？请使用中文回答。"


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=["openie", "build-index", "query", "all"], required=True)
    parser.add_argument("--save-dir", default="outputs/layered_qwen_demo")
    parser.add_argument("--index-id", default="layered-qwen-demo-v1")
    parser.add_argument("--force-openie", action="store_true")
    default_openie_provider = os.getenv(
        "SYNAPSERAG_OPENIE_PROVIDER", os.getenv("LLM_PROVIDER", "openai_compatible"))
    parser.add_argument(
        "--openie-provider",
        choices=["openai_compatible", "azure"],
        default=default_openie_provider,
    )
    parser.add_argument(
        "--openie-model",
        default=(
            os.getenv("SYNAPSERAG_OPENIE_MODEL")
            or os.getenv("TOOL_LLM_NAME")
            or "qwen-plus"
        ),
    )
    parser.add_argument(
        "--openie-base-url",
        default=(
            os.getenv("SYNAPSERAG_OPENIE_BASE_URL")
            or os.getenv("TOOL_LLM_URL")
            or "https://dashscope.aliyuncs.com/compatible-mode/v1"
        ),
    )
    parser.add_argument(
        "--openie-api-version",
        default=(
            os.getenv("SYNAPSERAG_OPENIE_API_VERSION")
            or os.getenv("AZURE_OPENAI_API_VERSION")
        ),
    )
    parser.add_argument(
        "--openie-api-key-env",
        default=os.getenv(
            "SYNAPSERAG_OPENIE_API_KEY_ENV",
            "API_KEY" if default_openie_provider == "azure" else "SYNAPSERAG_OPENIE_API_KEY",
        ),
    )
    parser.add_argument(
        "--openie-timeout-seconds",
        type=int,
        default=int(os.getenv("LLM_TIMEOUT_SECONDS", "300")),
    )
    parser.add_argument(
        "--embedding-model",
        default=os.getenv("SYNAPSERAG_EMBEDDING_MODEL", "qwen3-embedding:0.6b"),
    )
    parser.add_argument(
        "--embedding-base-url",
        default=os.getenv(
            "SYNAPSERAG_EMBEDDING_BASE_URL",
            "http://127.0.0.1:11434/v1",
        ),
    )
    parser.add_argument("--qa-model", default="qwen3:1.7b")
    parser.add_argument("--qa-base-url", default="http://127.0.0.1:11434/v1")
    return parser.parse_args()


def main():
    args = parse_args()
    openie_is_azure = args.openie_provider == "azure"
    config = BaseConfig(
        save_dir=args.save_dir,
        index_id=args.index_id,
        runtime_stage=args.stage,
        openie_prompt_version="ner-triple-cn-v2",
        openie_llm=LLMEndpointConfig(
            model_name=args.openie_model,
            provider=args.openie_provider,
            base_url=None if openie_is_azure else args.openie_base_url,
            api_key_env=args.openie_api_key_env,
            azure_endpoint=args.openie_base_url if openie_is_azure else None,
            api_version=args.openie_api_version if openie_is_azure else None,
            timeout_seconds=args.openie_timeout_seconds,
            temperature=0.0,
            max_tokens=2048,
        ),
        qa_llm=LLMEndpointConfig(
            model_name=args.qa_model,
            base_url=args.qa_base_url,
            api_key_env="SYNAPSERAG_QA_API_KEY",
            temperature=0.1,
            max_tokens=1024,
            extra_body={"reasoning_effort": "none"},
        ),
        embedding_model_name=args.embedding_model,
        embedding_base_url=args.embedding_base_url,
        fact_rerank_mode="hybrid",
        fact_candidate_top_k=20,
        fact_rerank_top_k=5,
        linking_top_k=5,
        retrieval_top_k=8,
        qa_top_k=5,
    )
    rag = SynapseRAG(global_config=config)

    if args.stage == "openie":
        print(rag.extract_openie(DOCS, force=args.force_openie))
    elif args.stage == "build-index":
        print(rag.build_index(DOCS, allow_openie_calls=False))
    elif args.stage == "query":
        print(rag.load_index())
        print(rag.rag_qa([QUESTION]))
    else:
        print(rag.index(DOCS))
        print(rag.rag_qa([QUESTION]))


if __name__ == "__main__":
    main()
