"""战术情报 Agent — A2A-main 分支入口包。"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tactical_intelligence_agent.service import TacticalIntelligenceCommanderAgent

__all__ = ["TacticalIntelligenceCommanderAgent"]


def __getattr__(name: str):
    if name == "TacticalIntelligenceCommanderAgent":
        from tactical_intelligence_agent.service import TacticalIntelligenceCommanderAgent as _Cls

        return _Cls
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
