"""
启发式 MROAW - 基于线性加权的边权重计算

这是概率化 MROAW 的对比基线，使用传统的启发式方法：

    W_final = base_weight × (α × W_info + β × W_struct + γ × W_semantic)

其中 α + β + γ = 1

与概率化 MROAW 的区别：
    - 启发式：线性组合，权重需要人工调参
    - 概率化：贝叶斯组合，有理论依据

用于消融实验：
    - 概率化 MROAW vs 启发式 MROAW
    - 验证贝叶斯框架的优势
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
    SemanticLikelihood
)

logger = logging.getLogger(__name__)


@dataclass
class HeuristicMROAWStats:
    """启发式 MROAW 计算的统计信息"""
    total_edges: int = 0
    entity_entity_edges: int = 0
    passage_entity_edges: int = 0
    avg_info_weight: float = 0.0
    avg_struct_weight: float = 0.0
    avg_semantic_weight: float = 0.0
    avg_final_weight: float = 0.0
    min_weight: float = float('inf')
    max_weight: float = 0.0


class HeuristicMROAW(EdgeWeightStrategy):
    """
    启发式 MROAW（Multi-aspect Relation-Oriented Adaptive Weighting）

    使用线性加权组合三个维度：
    W_final = base × (α × info + β × struct + γ × semantic)

    配置参数：
    - mroaw_info_weight: 信息维度权重 α（默认 0.4）
    - mroaw_struct_weight: 结构维度权重 β（默认 0.3）
    - mroaw_semantic_weight: 语义维度权重 γ（默认 0.3）

    注意：α + β + γ 应该等于 1.0
    """

    def __init__(self, config=None):
        super().__init__(config)

        # 线性组合权重
        self.alpha = getattr(config, 'mroaw_info_weight', 0.4)      # 信息
        self.beta = getattr(config, 'mroaw_struct_weight', 0.3)     # 结构
        self.gamma = getattr(config, 'mroaw_semantic_weight', 0.3)  # 语义

        # 归一化确保和为 1
        total = self.alpha + self.beta + self.gamma
        if total > 0:
            self.alpha /= total
            self.beta /= total
            self.gamma /= total

        # 初始化似然函数（复用概率版本的计算逻辑）
        likelihood_config = LikelihoodConfig(
            info_alpha=getattr(config, 'mroaw_info_alpha', 1.0),
            struct_lambda=getattr(config, 'mroaw_struct_lambda', 2.0),
            struct_max_path_length=getattr(config, 'mroaw_struct_max_path_length', 3),
            semantic_mu=getattr(config, 'mroaw_semantic_mu', 0.5),
            semantic_sigma=getattr(config, 'mroaw_semantic_sigma', 0.25),
        )

        self.info_calculator = InformationLikelihood(likelihood_config)
        self.struct_calculator = StructuralLikelihood(likelihood_config)
        self.semantic_calculator = SemanticLikelihood(likelihood_config)

        # 统计信息
        self.stats = HeuristicMROAWStats()

    def compute_weights(
        self,
        node_to_node_stats: Dict[Tuple[str, str], float],
        ent_node_to_chunk_ids: Dict[str, Set[str]],
        entity_embeddings: Optional[Dict[str, np.ndarray]] = None,
        graph: Optional[Any] = None,
        **kwargs
    ) -> Dict[Tuple[str, str], float]:
        """
        使用启发式线性加权计算边权重

        Args:
            node_to_node_stats: 原始边统计
            ent_node_to_chunk_ids: 实体到 chunk 的映射
            entity_embeddings: 实体嵌入字典
            graph: igraph 图对象

        Returns:
            加权后的边权重字典
        """
        logger.info("开始计算启发式 MROAW 边权重")
        logger.info(f"  - 信息权重 α: {self.alpha:.2f}")
        logger.info(f"  - 结构权重 β: {self.beta:.2f}")
        logger.info(f"  - 语义权重 γ: {self.gamma:.2f}")

        total_edges = len(node_to_node_stats)
        total_chunks = len(set.union(*ent_node_to_chunk_ids.values())) if ent_node_to_chunk_ids else 0

        logger.info(f"处理 {total_edges} 条边，{total_chunks} 个 chunks")

        # 重置统计
        self.stats = HeuristicMROAWStats(total_edges=total_edges)

        # 收集统计值
        info_weights, struct_weights, semantic_weights, final_weights = [], [], [], []

        new_weights = {}

        for edge, base_weight in tqdm(node_to_node_stats.items(), desc="计算边权重"):
            src, dst = edge
            edge_type = self.get_edge_type(src, dst)

            # 统计边类型
            if edge_type == 'entity_entity':
                self.stats.entity_entity_edges += 1
            elif edge_type in ('passage_entity', 'entity_passage'):
                self.stats.passage_entity_edges += 1

            # 对于非实体-实体边，保持原始权重
            if edge_type != 'entity_entity':
                new_weights[edge] = base_weight
                continue

            # 计算信息分数
            info_score = self.info_calculator.compute(
                src_entity=src,
                dst_entity=dst,
                ent_node_to_chunk_ids=ent_node_to_chunk_ids,
                total_chunks=total_chunks
            )
            info_weights.append(info_score)

            # 计算结构分数
            if graph is not None:
                struct_score = self.struct_calculator.compute(
                    src_entity=src,
                    dst_entity=dst,
                    graph=graph
                )
            else:
                struct_score = 0.5
            struct_weights.append(struct_score)

            # 计算语义分数
            if entity_embeddings is not None:
                semantic_score = self.semantic_calculator.compute(
                    src_entity=src,
                    dst_entity=dst,
                    entity_embeddings=entity_embeddings
                )
            else:
                semantic_score = 0.5
            semantic_weights.append(semantic_score)

            # 线性组合
            combined_score = (
                self.alpha * info_score +
                self.beta * struct_score +
                self.gamma * semantic_score
            )

            # 最终权重 = 基础权重 × 组合分数
            final_weight = base_weight * combined_score
            final_weights.append(final_weight)

            new_weights[edge] = final_weight

        # 更新统计
        if info_weights:
            self.stats.avg_info_weight = float(np.mean(info_weights))
            self.stats.avg_struct_weight = float(np.mean(struct_weights))
            self.stats.avg_semantic_weight = float(np.mean(semantic_weights))
            self.stats.avg_final_weight = float(np.mean(final_weights))
            self.stats.min_weight = float(min(new_weights.values()))
            self.stats.max_weight = float(max(new_weights.values()))

        # 清理缓存
        self.struct_calculator.clear_cache()

        logger.info("启发式 MROAW 计算完成")
        logger.info(f"  - 实体-实体边: {self.stats.entity_entity_edges}")
        logger.info(f"  - 文档-实体边: {self.stats.passage_entity_edges}")
        logger.info(f"  - 平均信息分数: {self.stats.avg_info_weight:.4f}")
        logger.info(f"  - 平均结构分数: {self.stats.avg_struct_weight:.4f}")
        logger.info(f"  - 平均语义分数: {self.stats.avg_semantic_weight:.4f}")
        logger.info(f"  - 权重范围: [{self.stats.min_weight:.4f}, {self.stats.max_weight:.4f}]")

        return new_weights

    def get_stats(self) -> HeuristicMROAWStats:
        """获取最近一次计算的统计信息"""
        return self.stats
