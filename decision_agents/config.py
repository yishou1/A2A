"""Environment configuration for local agent demos."""

from __future__ import annotations

import os

from dataclasses import dataclass

from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True)
class Settings:
    host: str
    decision_planning_port: int
    compliance_authorization_port: int
    enable_llm: bool
    tool_llm_url: str
    tool_llm_name: str
    api_key: str
    llm_timeout_seconds: float
    default_compute_budget: str
    default_risk_policy: str
    enable_local_rag_models: bool
    rag_query_model: str
    rag_embedding_model: str
    rag_rerank_model: str
    rag_generation_model: str
    rag_top_k_recall: int
    rag_top_k_final: int


def get_settings() -> Settings:
    return Settings(
        host=os.getenv("HOST", "localhost"),
        decision_planning_port=int(os.getenv("DECISION_PLANNING_AGENT_PORT", "10202")),
        compliance_authorization_port=int(
            os.getenv("COMPLIANCE_AUTHORIZATION_AGENT_PORT", "10203")
        ),
        enable_llm=os.getenv("ENABLE_LLM", "false").lower() == "true",
        tool_llm_url=os.getenv("TOOL_LLM_URL", ""),
        tool_llm_name=os.getenv("TOOL_LLM_NAME", ""),
        api_key=os.getenv("API_KEY", "EMPTY"),
        llm_timeout_seconds=float(os.getenv("LLM_TIMEOUT_SECONDS", "30")),
        default_compute_budget=os.getenv("DEFAULT_COMPUTE_BUDGET", "small"),
        default_risk_policy=os.getenv("DEFAULT_RISK_POLICY", "balanced"),
        enable_local_rag_models=os.getenv("ENABLE_LOCAL_RAG_MODELS", "false").lower()
        == "true",
        rag_query_model=os.getenv("RAG_QUERY_MODEL", "Qwen/Qwen3-0.6B"),
        rag_embedding_model=os.getenv(
            "RAG_EMBEDDING_MODEL",
            "BAAI/bge-small-zh-v1.5",
        ),
        rag_rerank_model=os.getenv("RAG_RERANK_MODEL", "BAAI/bge-reranker-v2-m3"),
        rag_generation_model=os.getenv("RAG_GENERATION_MODEL", "openbmb/MiniCPM3-4B"),
        rag_top_k_recall=int(os.getenv("RAG_TOP_K_RECALL", "20")),
        rag_top_k_final=int(os.getenv("RAG_TOP_K_FINAL", "6")),
    )
