"""
Agentic modules built on top of SynapseRAG, e.g., enhanced PPR and iterative agents.
"""

from .ppr import AgenticGraphSearch
from . import ppr

__all__ = ["AgenticGraphSearch", "ppr"]
