"""战术情报 Agent — A2A-main 分支入口包。"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tactical_intelligence_agent.service import TacticalIntelligenceCommanderAgent

__all__ = ["TacticalIntelligenceCommanderAgent"]


def __getattr__(name: str):
    """延迟加载依赖外部 A2A SDK 的服务入口，保持内部工具可独立测试。"""
    if name == "TacticalIntelligenceCommanderAgent":
        from tactical_intelligence_agent.service import TacticalIntelligenceCommanderAgent

        return TacticalIntelligenceCommanderAgent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
