"""
均匀权重策略 - 所有边权重设为 1.0
"""

from typing import Dict, Tuple, Set, Optional, Any
import numpy as np
import logging

from .base import EdgeWeightStrategy

logger = logging.getLogger(__name__)


class UniformEdgeWeight(EdgeWeightStrategy):
    """
    均匀权重策略

    所有边权重统一设为 1.0，作为最简单的基线
    """

    def compute_weights(
        self,
        node_to_node_stats: Dict[Tuple[str, str], float],
        ent_node_to_chunk_ids: Dict[str, Set[str]],
        entity_embeddings: Optional[Dict[str, np.ndarray]] = None,
        graph: Optional[Any] = None,
        **kwargs
    ) -> Dict[Tuple[str, str], float]:
        """
        将所有边权重设为 1.0

        Args:
            node_to_node_stats: 原始边统计
            ent_node_to_chunk_ids: 实体到 chunk 的映射（未使用）
            entity_embeddings: 实体嵌入（未使用）
            graph: 图对象（未使用）

        Returns:
            所有边权重为 1.0 的字典
        """
        logger.info(f"应用均匀权重策略，共 {len(node_to_node_stats)} 条边")

        uniform_weights = {edge: 1.0 for edge in node_to_node_stats.keys()}

        logger.info("均匀权重策略应用完成")
        return uniform_weights
