"""Environment configuration for local agent demos."""

from __future__ import annotations

import os

from dataclasses import dataclass

from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True)
class Settings:
    host: str
    track_threat_port: int
    decision_planning_port: int
    compliance_authorization_port: int
    enable_llm: bool
    tool_llm_url: str
    tool_llm_name: str
    api_key: str
    llm_timeout_seconds: float
    default_compute_budget: str
    default_risk_policy: str


def get_settings() -> Settings:
    return Settings(
        host=os.getenv("HOST", "localhost"),
        track_threat_port=int(os.getenv("TRACK_THREAT_AGENT_PORT", "10201")),
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
    )
