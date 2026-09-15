"""
边权重策略基类
"""

from abc import ABC, abstractmethod
from typing import Dict, Tuple, Set, Optional, Any
import numpy as np
import logging

logger = logging.getLogger(__name__)


class EdgeWeightStrategy(ABC):
    """
    边权重计算策略的抽象基类

    所有边权重策略都需要继承此类并实现 compute_weights 方法
    """

    def __init__(self, config=None):
        """
        Args:
            config: BaseConfig 实例，包含策略相关的超参数
        """
        self.config = config

    @abstractmethod
    def compute_weights(
        self,
        node_to_node_stats: Dict[Tuple[str, str], float],
        ent_node_to_chunk_ids: Dict[str, Set[str]],
        entity_embeddings: Optional[Dict[str, np.ndarray]] = None,
        graph: Optional[Any] = None,
        **kwargs
    ) -> Dict[Tuple[str, str], float]:
        """
        计算边权重

        Args:
            node_to_node_stats: 原始边统计 {(src, dst): base_weight}
            ent_node_to_chunk_ids: 实体到 chunk 的映射 {entity_id: {chunk_id, ...}}
            entity_embeddings: 实体嵌入 {entity_id: embedding_vector}（可选，用于语义计算）
            graph: igraph 图对象（可选，用于结构计算）
            **kwargs: 其他可能需要的参数

        Returns:
            修改后的边权重字典 {(src, dst): new_weight}
        """
        pass

    def get_edge_type(self, src: str, dst: str) -> str:
        """
        判断边的类型

        Args:
            src: 源节点 ID
            dst: 目标节点 ID

        Returns:
            边类型：'passage_entity', 'entity_entity', 'synonymy', 'unknown'
        """
        src_is_chunk = src.startswith('chunk-')
        dst_is_chunk = dst.startswith('chunk-')
        src_is_entity = src.startswith('entity-')
        dst_is_entity = dst.startswith('entity-')

        if src_is_chunk and dst_is_entity:
            return 'passage_entity'
        elif src_is_entity and dst_is_entity:
            # 同义边的权重通常是相似度分数 (0, 1)，而关系边是整数共现次数
            # 这里无法完全区分，但可以通过权重范围大致判断
            return 'entity_entity'
        elif dst_is_chunk and src_is_entity:
            return 'entity_passage'
        else:
            return 'unknown'

    def _safe_log(self, x: float, epsilon: float = 1e-10) -> float:
        """安全的对数计算，避免 log(0)"""
        return np.log(max(x, epsilon))

    def _sigmoid(self, x: float, k: float = 1.0, x0: float = 0.0) -> float:
        """Sigmoid 函数"""
        return 1.0 / (1.0 + np.exp(-k * (x - x0)))

    def _gaussian(self, x: float, mu: float = 0.5, sigma: float = 0.2) -> float:
        """高斯函数，输出归一化到 [0, 1]"""
        return np.exp(-0.5 * ((x - mu) / sigma) ** 2)
