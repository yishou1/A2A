"""Agent 包：延迟导入以避免与 tactical_intelligence_agent 循环依赖。"""

from __future__ import annotations

from typing import TYPE_CHECKING

__all__ = ["TacticalIntelligenceAgent"]

if TYPE_CHECKING:
    from agent.orchestrator import TacticalIntelligenceAgent


def __getattr__(name: str):
    if name == "TacticalIntelligenceAgent":
        from agent.orchestrator import TacticalIntelligenceAgent

        return TacticalIntelligenceAgent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
