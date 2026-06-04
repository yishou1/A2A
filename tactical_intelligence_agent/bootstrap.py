"""加载配置并构建 TacticalIntelligenceAgent 引擎。"""

from __future__ import annotations

import os
from typing import Any

from agent.orchestrator import TacticalIntelligenceAgent
from agent.pipeline import agent_config_from_yaml, create_agent, load_config


def create_engine(config: dict[str, Any] | None = None) -> TacticalIntelligenceAgent:
    if config is None:
        config = load_config()
    return create_agent(config)


def default_role() -> str:
    return os.environ.get("TIA_A2A_ROLE", "tactical_intelligence")
