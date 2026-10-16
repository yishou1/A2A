"""
Agent (PPR版本) - 主控制器


"""

import logging
import numpy as np
from typing import List, Tuple, Optional, Dict, TYPE_CHECKING
from dataclasses import dataclass

from .memory.gist_memory import GistMemory, Triple
from .retrieval.ppr_retriever import PPRRetriever
from .reasoning.reasoner import Reasoner
from .reasoning.query_rewriter import QueryRewriter
from .query_analyzer import QueryAnalyzer
from .data_structures import QueryPlan
from .logging_utils import AgenticLogContext, AgenticLogOptions

if TYPE_CHECKING:
    from ...SynapseRAG import SynapseRAG

logger = logging.getLogger(__name__)


@dataclass
class RetrievalResult:
    """检索结果"""
    doc_ids: np.ndarray
    doc_scores: np.ndarray
    gist_memory: GistMemory
    iterations: int
    answerable: bool
    answer: Optional[str] = None
    reasoning: str = ""


class GEARAgentPPR:
    """
    Agent (PPR版本)

    核心流程:
    for iteration in 1..max_iterations:
        1. PPR检索 → 获取文档和三元组
        2. 更新Gist Memory
        3. Reasoner判断是否终止
           - 是 → 返回结果
           - 否 → Query Rewrite → 下一轮
    """

    def __init__(self,
                 synapserag: "SynapseRAG",
                 max_iterations: int = 5,
                 use_query_analysis: bool = True):
        """
        初始化GEAR Agent

        Args:
            synapserag: SynapseRAG实例
            max_iterations: 最大迭代次数
            use_query_analysis: 是否使用QueryAnalyzer分析查询
        """
        self.synapserag = synapserag
        self.max_iterations = max_iterations
        self.use_query_analysis = use_query_analysis

        # 初始化各个模块
        self.ppr_retriever = PPRRetriever(synapserag)
        self.reasoner = Reasoner(synapserag.llm_model)
        self.query_rewriter = QueryRewriter(synapserag.llm_model)

        if use_query_analysis:
            self.query_analyzer = QueryAnalyzer(synapserag.llm_model)
        else:
            self.query_analyzer = None

        logger.info(f"GEAR Agent (PPR版本) 已初始化 (max_iterations={max_iterations})")

    def retrieve(self,
                query: str,
                top_k_docs: int = 50,
                verbose: bool = True) -> RetrievalResult:
        """
        完整的GEAR检索流程

        Args:
            query: 查询文本
            top_k_docs: 每次迭代返回的文档数量
            verbose: 是否打印详细日志

        Returns:
            RetrievalResult对象
        """
        if verbose:
            logger.info("=" * 80)
            logger.info(f"GEAR Agent检索: {query}")
            logger.info("=" * 80)

        # Step 0: 查询分析(可选)
        query_plan = self._analyze_query(query) if self.use_query_analysis else None

        # 初始化Gist Memory
        gist_memory = GistMemory()

        # 迭代检索
        current_query = query
        all_doc_ids = []
        all_doc_scores = []

        for iteration in range(1, self.max_iterations + 1):
            if verbose:
                logger.info("-" * 80)
                logger.info(f"迭代 {iteration}/{self.max_iterations}")
                logger.info(f"当前查询: {current_query}")
                logger.info("-" * 80)

            log_context = AgenticLogContext(
                query=current_query,
                iteration=iteration,
                max_iterations=self.max_iterations,
                extras={
                    "link_top_k": self.ppr_retriever.linking_top_k,
                    "top_k_docs": top_k_docs
                }
            )
            graph_log_options = AgenticLogOptions(
                level=logging.DEBUG,
                log_query=False,
                context=log_context
            )

            # Step 1: PPR检索
            doc_ids, doc_scores, triples = self.ppr_retriever.retrieve(
                query=current_query,
                top_k_docs=top_k_docs,
                return_triples=True,
                log_options=graph_log_options
            )

            # 累积文档
            all_doc_ids.extend(doc_ids.tolist())
            all_doc_scores.extend(doc_scores.tolist())

            # Step 2: 更新Gist Memory
            added_count = gist_memory.add_triples(triples)
            if verbose:
                logger.info(f"Gist Memory: 新增{added_count}个三元组, 总计{gist_memory.size()}个")
                logger.info(f"当前实体数: {len(gist_memory.get_entities())}")
                gist_memory.log_snapshot(
                    logger=logger,
                    include_scores=True,
                    note=f"迭代{iteration} Gist Memory"
                )

            # Step 3: Reasoner - 判断是否终止
            should_stop, reasoning, answer = self.reasoner.should_terminate(
                query=query,  # 注意:用原始查询
                gist_memory=gist_memory
            )

            if verbose:
                logger.info(f"Reasoner: {'可回答' if should_stop else '不可回答'}")
                logger.info(f"推理: {reasoning[:100]}...")

            if should_stop:
                if verbose:
                    logger.info(f"✓ 在第{iteration}轮找到答案,终止迭代")

                # 返回结果
                return self._build_result(
                    all_doc_ids=all_doc_ids,
                    all_doc_scores=all_doc_scores,
                    gist_memory=gist_memory,
                    iterations=iteration,
                    answerable=True,
                    answer=answer,
                    reasoning=reasoning,
                    top_k=top_k_docs
                )

            # Step 4: Query Rewrite - 生成下一轮查询
            if iteration < self.max_iterations:
                current_query = self.query_rewriter.rewrite(
                    original_query=query,
                    gist_memory=gist_memory,
                    reasoning=reasoning
                )

                if verbose:
                    logger.info(f"重写查询: {current_query}")

        # 达到最大迭代次数,仍未找到答案
        if verbose:
            logger.info(f"✗ 达到最大迭代次数({self.max_iterations}),未找到答案")

        return self._build_result(
            all_doc_ids=all_doc_ids,
            all_doc_scores=all_doc_scores,
            gist_memory=gist_memory,
            iterations=self.max_iterations,
            answerable=False,
            answer=None,
            reasoning="达到最大迭代次数",
            top_k=top_k_docs
        )

    def _analyze_query(self, query: str) -> Optional[QueryPlan]:
        """查询分析"""
        if self.query_analyzer is None:
            return None

        try:
            query_plan = self.query_analyzer.analyze(query)
            logger.info(f"查询分析: {query_plan}")
            return query_plan
        except Exception as e:
            logger.warning(f"查询分析失败: {e}")
            return None

    def _build_result(self,
                     all_doc_ids: List[int],
                     all_doc_scores: List[float],
                     gist_memory: GistMemory,
                     iterations: int,
                     answerable: bool,
                     answer: Optional[str],
                     reasoning: str,
                     top_k: int) -> RetrievalResult:
        """
        构建最终结果

        合并所有迭代的文档,去重并排序
        """
        # 去重并排序
        doc_score_map = {}
        for doc_id, score in zip(all_doc_ids, all_doc_scores):
            if doc_id not in doc_score_map:
                doc_score_map[doc_id] = score
            else:
                # 保留更高的分数
                doc_score_map[doc_id] = max(doc_score_map[doc_id], score)

        # 排序
        sorted_items = sorted(doc_score_map.items(), key=lambda x: x[1], reverse=True)

        # 限制数量
        sorted_items = sorted_items[:top_k]

        final_doc_ids = np.array([item[0] for item in sorted_items])
        final_doc_scores = np.array([item[1] for item in sorted_items])

        return RetrievalResult(
            doc_ids=final_doc_ids,
            doc_scores=final_doc_scores,
            gist_memory=gist_memory,
            iterations=iterations,
            answerable=answerable,
            answer=answer,
            reasoning=reasoning
        )

    def retrieve_simple(self, query: str, top_k_docs: int = 50) -> Tuple[np.ndarray, np.ndarray]:
        """
        简化接口:只返回文档ID和分数

        Args:
            query: 查询
            top_k_docs: 返回文档数量

        Returns:
            (doc_ids, doc_scores)
        """
        result = self.retrieve(query=query, top_k_docs=top_k_docs, verbose=False)
        return result.doc_ids, result.doc_scores
