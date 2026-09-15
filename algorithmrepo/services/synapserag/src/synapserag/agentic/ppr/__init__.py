"""
GeAR Agent模块

实现基于GeAR论文的Agent框架，用于提升多跳推理准确性。

主要组件：
- GEARAgentPPR: PPR版本的GEAR Agent (新版,推荐)
- GeARAgent: 原始主控制器 (占位符)
- QueryAnalyzer: 查询分析器
- PathExplorer: 路径探索器
- PathEvaluator: 路径评估器

Memory模块:
- GistMemory: 摘要记忆
- Triple: 三元组数据结构

Retrieval模块:
- PPRRetriever: PPR检索器

Reasoning模块:
- Reasoner: 推理验证器
- QueryRewriter: 查询重写器
"""

from .agent import GeARAgent
from .agentic_graph_search import AgenticGraphSearch
from .data_structures import Path, QueryPlan, InitialNode, PathScore
from .query_analyzer import QueryAnalyzer
from .path_explorer import PathExplorer
from .path_evaluator import PathEvaluator

# 新模块
from .gear_agent_ppr import GEARAgentPPR, RetrievalResult
from .memory import GistMemory, Triple
from .logging_utils import AgenticLogContext, AgenticLogOptions, AgenticLogFormatter
from .retrieval import PPRRetriever
from .reasoning import Reasoner, QueryRewriter

__all__ = [
    # 主控制器
    'GEARAgentPPR',  # 新版(推荐)
    'GeARAgent',     # 旧版

    # 分析和探索
    'QueryAnalyzer',
    'PathExplorer',
    'PathEvaluator',

    # 数据结构
    'Path',
    'QueryPlan',
    'InitialNode',
    'PathScore',
    'RetrievalResult',

    # 记忆模块
    'GistMemory',
    'Triple',

    # 检索模块
    'PPRRetriever',

    # 推理模块
    'Reasoner',
    'QueryRewriter',

    # Agentic graph search + logging
    'AgenticGraphSearch',
    'AgenticLogContext',
    'AgenticLogOptions',
    'AgenticLogFormatter',
]

__version__ = '0.1.0'
