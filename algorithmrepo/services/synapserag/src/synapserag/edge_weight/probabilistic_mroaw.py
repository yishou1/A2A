"""
概率化 MROAW - 基于贝叶斯框架的边权重计算

核心创新：
    将边权重解释为"边正确的后验概率"，而非启发式分数

数学框架：
    P(edge正确 | E_info, E_struct, E_semantic)
    ∝ P(edge) × P(E_info|edge) × P(E_struct|edge) × P(E_semantic|edge)

    其中：
    - P(edge)：基于共现频率的先验
    - P(E_info|edge)：信息似然（稀有实体共现）
    - P(E_struct|edge)：结构似然（多路径支撑）
    - P(E_semantic|edge)：语义似然（嵌入相似度）

与题目"融合概率知识图谱"的契合：
    1. 输出是真正的概率 ∈ [0, 1]
    2. 使用贝叶斯推理框架
    3. 融合多源证据（信息 + 结构 + 语义）
"""

from typing import Dict, Tuple, Set, Optional, Any
import numpy as np
import logging
from tqdm import tqdm
from dataclasses import dataclass

from .base import EdgeWeightStrategy
from .likelihoods import (
    LikelihoodConfig,
    InformationLikelihood,
    StructuralLikelihood,
    SemanticLikelihood,
    PriorCalculator
)

logger = logging.getLogger(__name__)


@dataclass
class MROAWStats:
    """MROAW 计算的统计信息"""
    total_edges: int = 0
    entity_entity_edges: int = 0
    passage_entity_edges: int = 0
    avg_prior: float = 0.0
    avg_info_likelihood: float = 0.0
    avg_struct_likelihood: float = 0.0
    avg_semantic_likelihood: float = 0.0
    avg_posterior: float = 0.0
    min_posterior: float = 1.0
    max_posterior: float = 0.0


class ProbabilisticMROAW(EdgeWeightStrategy):
    """
    概率化 MROAW（Multi-aspect Relation-Oriented Adaptive Weighting）

    使用贝叶斯框架计算边的置信度，融合三个独立维度的证据：
    1. 信息维度：基于实体稀有度（IDF 风格）
    2. 结构维度：基于图的多路径支撑
    3. 语义维度：基于嵌入相似度

    配置参数（通过 config 传入）：
    - mroaw_use_info_likelihood: 是否使用信息似然（默认 True）
    - mroaw_use_struct_likelihood: 是否使用结构似然（默认 True）
    - mroaw_use_semantic_likelihood: 是否使用语义似然（默认 True）
    - mroaw_info_alpha: 信息似然的敏感度参数
    - mroaw_struct_lambda: 结构似然的衰减参数
    - mroaw_semantic_mu: 语义似然的最优相似度
    - mroaw_semantic_sigma: 语义似然的高斯宽度
    - mroaw_prior_smoothing: 先验的平滑系数
    """

    def __init__(self, config=None):
        super().__init__(config)

        # 初始化似然函数配置
        likelihood_config = LikelihoodConfig(
            info_alpha=getattr(config, 'mroaw_info_alpha', 1.0),
            struct_lambda=getattr(config, 'mroaw_struct_lambda', 2.0),
            struct_max_path_length=getattr(config, 'mroaw_struct_max_path_length', 3),
            semantic_mu=getattr(config, 'mroaw_semantic_mu', 0.5),
            semantic_sigma=getattr(config, 'mroaw_semantic_sigma', 0.25),
        )

        # 初始化各组件
        self.info_likelihood = InformationLikelihood(likelihood_config)
        self.struct_likelihood = StructuralLikelihood(likelihood_config)
        self.semantic_likelihood = SemanticLikelihood(likelihood_config)
        self.prior_calculator = PriorCalculator(
            smoothing=getattr(config, 'mroaw_prior_smoothing', 1.0)
        )

        # 控制使用哪些似然函数
        self.use_info = getattr(config, 'mroaw_use_info_likelihood', True)
        self.use_struct = getattr(config, 'mroaw_use_struct_likelihood', True)
        self.use_semantic = getattr(config, 'mroaw_use_semantic_likelihood', True)

        # 统计信息
        self.stats = MROAWStats()

    def compute_weights(
        self,
        node_to_node_stats: Dict[Tuple[str, str], float],
        ent_node_to_chunk_ids: Dict[str, Set[str]],
        entity_embeddings: Optional[Dict[str, np.ndarray]] = None,
        graph: Optional[Any] = None,
        **kwargs
    ) -> Dict[Tuple[str, str], float]:
        """
        使用贝叶斯框架计算边的置信度

        Args:
            node_to_node_stats: 原始边统计 {(src, dst): base_weight}
            ent_node_to_chunk_ids: 实体到 chunk 的映射
            entity_embeddings: 实体嵌入字典（用于语义似然）
            graph: igraph 图对象（用于结构似然）

        Returns:
            置信度权重字典 {(src, dst): confidence ∈ [0, 1]}
        """
        logger.info("开始计算概率化 MROAW 边权重")
        logger.info(f"  - 使用信息似然: {self.use_info}")
        logger.info(f"  - 使用结构似然: {self.use_struct}")
        logger.info(f"  - 使用语义似然: {self.use_semantic}")

        total_edges = len(node_to_node_stats)
        total_chunks = len(set.union(*ent_node_to_chunk_ids.values())) if ent_node_to_chunk_ids else 0

        logger.info(f"处理 {total_edges} 条边，{total_chunks} 个 chunks")

        # 重置统计
        self.stats = MROAWStats(total_edges=total_edges)

        # 收集用于统计的值
        priors, info_lks, struct_lks, semantic_lks, posteriors = [], [], [], [], []

        new_weights = {}

        for edge, base_weight in tqdm(node_to_node_stats.items(), desc="计算边置信度"):
            src, dst = edge
            edge_type = self.get_edge_type(src, dst)

            # 统计边类型
            if edge_type == 'entity_entity':
                self.stats.entity_entity_edges += 1
            elif edge_type in ('passage_entity', 'entity_passage'):
                self.stats.passage_entity_edges += 1

            # 计算先验
            prior = self.prior_calculator.compute(
                base_weight=base_weight,
                total_edges=total_edges,
                edge_type=edge_type
            )
            priors.append(prior)

            # 计算信息似然
            if self.use_info and edge_type == 'entity_entity':
                info_lk = self.info_likelihood.compute(
                    src_entity=src,
                    dst_entity=dst,
                    ent_node_to_chunk_ids=ent_node_to_chunk_ids,
                    total_chunks=total_chunks
                )
            else:
                info_lk = 0.5  # 中性值
            info_lks.append(info_lk)

            # 计算结构似然
            if self.use_struct and graph is not None and edge_type == 'entity_entity':
                struct_lk = self.struct_likelihood.compute(
                    src_entity=src,
                    dst_entity=dst,
                    graph=graph
                )
            else:
                struct_lk = 0.5
            struct_lks.append(struct_lk)

            # 计算语义似然
            if self.use_semantic and entity_embeddings is not None and edge_type == 'entity_entity':
                semantic_lk = self.semantic_likelihood.compute(
                    src_entity=src,
                    dst_entity=dst,
                    entity_embeddings=entity_embeddings
                )
            else:
                semantic_lk = 0.5
            semantic_lks.append(semantic_lk)

            # 贝叶斯组合：后验 ∝ 先验 × 各似然
            # 使用对数空间避免数值下溢
            log_posterior = (
                np.log(prior + 1e-10) +
                np.log(info_lk + 1e-10) +
                np.log(struct_lk + 1e-10) +
                np.log(semantic_lk + 1e-10)
            )
            posterior = np.exp(log_posterior)
            posteriors.append(posterior)

            new_weights[edge] = posterior

        # 归一化后验到 [0, 1] 范围
        if posteriors:
            min_post = min(posteriors)
            max_post = max(posteriors)
            if max_post > min_post:
                # Min-max 归一化，但保留一定的下限
                new_weights = {
                    edge: 0.1 + 0.9 * (w - min_post) / (max_post - min_post)
                    for edge, w in new_weights.items()
                }
            else:
                # 所有权重相同，设为中性值
                new_weights = {edge: 0.5 for edge in new_weights}

            # 更新统计
            self.stats.avg_prior = float(np.mean(priors))
            self.stats.avg_info_likelihood = float(np.mean(info_lks))
            self.stats.avg_struct_likelihood = float(np.mean(struct_lks))
            self.stats.avg_semantic_likelihood = float(np.mean(semantic_lks))
            normalized_posteriors = list(new_weights.values())
            self.stats.avg_posterior = float(np.mean(normalized_posteriors))
            self.stats.min_posterior = float(min(normalized_posteriors))
            self.stats.max_posterior = float(max(normalized_posteriors))

        # 清理结构似然的缓存
        self.struct_likelihood.clear_cache()

        logger.info("概率化 MROAW 计算完成")
        logger.info(f"  - 实体-实体边: {self.stats.entity_entity_edges}")
        logger.info(f"  - 文档-实体边: {self.stats.passage_entity_edges}")
        logger.info(f"  - 平均先验: {self.stats.avg_prior:.4f}")
        logger.info(f"  - 平均信息似然: {self.stats.avg_info_likelihood:.4f}")
        logger.info(f"  - 平均结构似然: {self.stats.avg_struct_likelihood:.4f}")
        logger.info(f"  - 平均语义似然: {self.stats.avg_semantic_likelihood:.4f}")
        logger.info(f"  - 后验范围: [{self.stats.min_posterior:.4f}, {self.stats.max_posterior:.4f}]")

        return new_weights

    def get_stats(self) -> MROAWStats:
        """获取最近一次计算的统计信息"""
        return self.stats
