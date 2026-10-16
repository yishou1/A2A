"""
PPR Retriever - 基于PersonalizedPageRank的检索器

职责:
1. 运行单次PPR检索
2. 返回文档和相关三元组
3. 不负责迭代逻辑

与旧版 iterative_ppr 的区别:
- 更简单:只负责单次检索
- 更纯粹:不包含迭代、早停等Agent逻辑
- 更灵活:Agent可以自由组合多次检索
"""

import logging
import numpy as np
from typing import List, Tuple, Optional, Dict, TYPE_CHECKING
from ..memory.gist_memory import Triple
from ..logging_utils import AgenticLogOptions

if TYPE_CHECKING:
    from ...SynapseRAG import SynapseRAG

logger = logging.getLogger(__name__)


class PPRRetriever:
    """
    PPR检索器

    封装SynapseRAG的PPR能力,提供简洁的检索接口
    """

    def __init__(self, synapserag: "SynapseRAG"):
        """
        初始化PPR检索器

        Args:
            synapserag: SynapseRAG实例
        """
        self.synapserag = synapserag
        self.llm_model = synapserag.llm_model

        # PPR参数(从config读取)
        config = synapserag.global_config
        self.damping = getattr(config, 'damping', 0.85)
        self.passage_node_weight = getattr(config, 'passage_node_weight', 0.05)
        self.linking_top_k = getattr(config, 'linking_top_k', 10)

        logger.info(f"PPRRetriever初始化: damping={self.damping}")

    def retrieve(self,
                 query: str,
                 top_k_docs: int = 50,
                 return_triples: bool = True,
                 log_options: Optional[AgenticLogOptions] = None) -> Tuple[np.ndarray, np.ndarray, List[Triple]]:
        """
        单次PPR检索

        Args:
            query: 查询文本
            top_k_docs: 返回多少个文档
            return_triples: 是否返回相关三元组

        Returns:
            (doc_ids, doc_scores, triples)
            - doc_ids: 文档ID数组
            - doc_scores: 文档分数数组
            - triples: 相关三元组列表(如果return_triples=True)
        """
        logger.info(f"PPR检索: {query[:50]}...")

        # Step 1: 检索top-k facts (使用SynapseRAG的linking功能)
        query_fact_scores, top_k_facts, top_k_fact_indices = self._link_query_to_facts(query)

        # Step 2: 运行PPR
        doc_ids, doc_scores = self._run_ppr_search(
            query=query,
            query_fact_scores=query_fact_scores,
            top_k_facts=top_k_facts,
            top_k_fact_indices=top_k_fact_indices,
            log_options=log_options,
        )

        # 限制返回的文档数量
        doc_ids = doc_ids[:top_k_docs]
        doc_scores = doc_scores[:top_k_docs]

        # Step 3: 提取三元组(如果需要)
        triples = []
        if return_triples:
            triples = self._extract_triples_from_facts(
                top_k_facts=top_k_facts,
                query_fact_scores=query_fact_scores,
                top_k_fact_indices=top_k_fact_indices,
                hop_number=1
            )

        logger.info(f"检索完成: {len(doc_ids)}个文档, {len(triples)}个三元组")

        return doc_ids, doc_scores, triples

    def _link_query_to_facts(self, query: str) -> Tuple[np.ndarray, List[Tuple], List[int]]:
        """
        将查询链接到事实(三元组)

        直接复用SynapseRAG提供的底层API,避免多余的核心文件改动

        Returns:
            (query_fact_scores, top_k_facts, top_k_fact_indices)
        """
        logger.debug("链接查询到事实")

        if not self.synapserag.ready_to_retrieve:
            self.synapserag.prepare_retrieval_objects()

        query_fact_scores = self.synapserag.get_fact_scores(query)
        top_k_fact_indices, top_k_facts, rerank_log = self.synapserag.rerank_facts(query, query_fact_scores)

        if self.linking_top_k and self.linking_top_k > 0:
            top_k_fact_indices = top_k_fact_indices[:self.linking_top_k]
            top_k_facts = top_k_facts[:self.linking_top_k]

        if isinstance(rerank_log, dict):
            facts_before = len(rerank_log.get('facts_before_rerank', []))
            logger.debug(f"link_query_to_facts: {facts_before} candidates -> {len(top_k_facts)} facts")
        else:
            logger.debug(f"找到{len(top_k_facts)}个相关事实")

        return query_fact_scores, top_k_facts, top_k_fact_indices

    def _run_ppr_search(self,
                       query: str,
                       query_fact_scores: np.ndarray,
                       top_k_facts: List[Tuple],
                       top_k_fact_indices: List[int],
                       log_options: Optional[AgenticLogOptions]) -> Tuple[np.ndarray, np.ndarray]:
        """
        运行PPR搜索

        使用SynapseRAG的graph_search方法

        Returns:
            (doc_ids, doc_scores)
        """
        logger.debug("运行PPR搜索")

        # 调用SynapseRAG的graph_search方法
        result = self.synapserag.graph_search_with_fact_entities(
            query=query,
            link_top_k=self.linking_top_k,
            query_fact_scores=query_fact_scores,
            top_k_facts=top_k_facts,
            top_k_fact_indices=top_k_fact_indices,
            passage_node_weight=self.passage_node_weight,
            log_options=log_options,
        )

        # 处理返回值(可能是2个或4个值,取决于是否启用GoT过滤器)
        if len(result) == 4:
            sorted_doc_ids, sorted_doc_scores, _, _ = result
        elif len(result) == 2:
            sorted_doc_ids, sorted_doc_scores = result
        else:
            raise ValueError(f"意外的返回值数量: {len(result)}")

        logger.debug(f"PPR搜索完成,找到{len(sorted_doc_ids)}个文档")

        return sorted_doc_ids, sorted_doc_scores

    def _extract_triples_from_facts(self,
                                    top_k_facts: List[Tuple],
                                    query_fact_scores: np.ndarray,
                                    top_k_fact_indices: List[int],
                                    hop_number: int = 1) -> List[Triple]:
        """
        从facts中提取三元组

        Args:
            top_k_facts: Top-k事实列表
            query_fact_scores: 事实分数
            top_k_fact_indices: 事实索引
            hop_number: 跳数标记

        Returns:
            Triple对象列表
        """
        logger.debug("从facts提取三元组")

        triples = []

        for rank, fact in enumerate(top_k_facts):
            subject, predicate, obj = fact

            # 获取分数
            if query_fact_scores.ndim > 0:
                score = float(query_fact_scores[top_k_fact_indices[rank]])
            else:
                score = float(query_fact_scores)

            # 创建Triple对象
            triple = Triple(
                subject=subject,
                predicate=predicate,
                object=obj,
                score=score,
                hop_number=hop_number
            )

            triples.append(triple)

        logger.debug(f"提取了{len(triples)}个三元组")

        return triples

    def retrieve_from_entities(self,
                              entities: List[str],
                              query: str,
                              top_k_docs: int = 50) -> Tuple[np.ndarray, np.ndarray]:
        """
        基于给定实体列表进行检索

        用于多跳场景:基于上一跳的高分实体进行下一跳检索

        Args:
            entities: 实体列表(实体名称)
            query: 原始查询(用于计算相关性)
            top_k_docs: 返回多少个文档

        Returns:
            (doc_ids, doc_scores)
        """
        logger.info(f"基于{len(entities)}个实体进行检索")

        # 创建reset权重:给这些实体高权重
        reset_prob = self._create_reset_from_entities(entities)

        # 运行PPR
        ppr_scores = self._run_ppr_with_reset(reset_prob)

        # 提取文档分数
        doc_scores = np.array([ppr_scores[idx] for idx in self.synapserag.passage_node_idxs])
        sorted_doc_ids = np.argsort(doc_scores)[::-1]
        sorted_doc_scores = doc_scores[sorted_doc_ids]

        # 限制返回数量
        sorted_doc_ids = sorted_doc_ids[:top_k_docs]
        sorted_doc_scores = sorted_doc_scores[:top_k_docs]

        logger.info(f"检索完成: {len(sorted_doc_ids)}个文档")

        return sorted_doc_ids, sorted_doc_scores

    def _create_reset_from_entities(self, entities: List[str]) -> np.ndarray:
        """
        基于实体列表创建reset权重

        Args:
            entities: 实体名称列表

        Returns:
            reset权重数组
        """
        from ..utils.misc_utils import compute_mdhash_id

        reset_prob = np.zeros(len(self.synapserag.graph.vs['name']))

        for entity_name in entities:
            # 查找实体节点
            entity_key = compute_mdhash_id(content=entity_name.lower(), prefix="entity-")

            if entity_key in self.synapserag.node_name_to_vertex_idx:
                entity_idx = self.synapserag.node_name_to_vertex_idx[entity_key]
                reset_prob[entity_idx] = 1.0

        # 归一化
        if np.sum(reset_prob) > 0:
            reset_prob = reset_prob / np.sum(reset_prob)
        else:
            # 如果没有匹配的实体,使用均匀分布(fallback)
            logger.warning("没有找到匹配的实体,使用均匀分布")
            reset_prob = np.ones_like(reset_prob) / len(reset_prob)

        return reset_prob

    def _run_ppr_with_reset(self, reset_prob: np.ndarray) -> np.ndarray:
        """
        使用指定的reset权重运行PPR

        Args:
            reset_prob: Reset权重数组

        Returns:
            所有节点的PPR分数数组
        """
        # 规范化reset_prob
        rp = np.asarray(reset_prob, dtype=float)
        rp = np.where(np.isnan(rp) | (rp < 0), 0, rp)
        s = float(np.sum(rp))

        if s <= 0:
            rp = np.ones_like(rp, dtype=float)
            rp /= float(len(rp))
        else:
            rp /= s

        # 调用igraph的PPR
        pagerank_scores = self.synapserag.graph.personalized_pagerank(
            vertices=range(len(self.synapserag.node_name_to_vertex_idx)),
            damping=self.damping,
            directed=False,
            weights='weight',
            reset=rp,
            implementation='prpack'
        )

        return np.array(pagerank_scores)
