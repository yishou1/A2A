"""
边权重计算模块 - 支持多种可插拔的权重策略

使用方式：
    from synapserag.edge_weight import get_edge_weight_strategy

    # 获取策略实例
    strategy = get_edge_weight_strategy('probabilistic_mroaw', config)

    # 计算边权重
    new_weights = strategy.compute_weights(
        node_to_node_stats=base_weights,
        ent_node_to_chunk_ids=entity_chunk_mapping,
        entity_embeddings=embeddings,  # 可选，用于语义似然
        graph=graph,  # 可选，用于结构似然
    )

可用策略：
    - 'uniform': 所有边权重 = 1.0
    - 'base': 使用原始共现统计（默认）
    - 'heuristic_mroaw': 启发式多维度加权（线性组合）
    - 'probabilistic_mroaw': 概率化 MROAW（贝叶斯置信度）
"""

from .base import EdgeWeightStrategy
from .uniform import UniformEdgeWeight
from .heuristic_mroaw import HeuristicMROAW
from .probabilistic_mroaw import ProbabilisticMROAW

__all__ = [
    'EdgeWeightStrategy',
    'UniformEdgeWeight',
    'HeuristicMROAW',
    'ProbabilisticMROAW',
    'get_edge_weight_strategy',
]

# 策略注册表
_STRATEGY_REGISTRY = {
    'uniform': UniformEdgeWeight,
    'base': None,  # 特殊标记：不做任何修改
    'heuristic_mroaw': HeuristicMROAW,
    'probabilistic_mroaw': ProbabilisticMROAW,
}


def get_edge_weight_strategy(name: str, config=None) -> EdgeWeightStrategy | None:
    """
    根据名称获取边权重策略实例

    Args:
        name: 策略名称，可选值：'uniform', 'base', 'heuristic_mroaw', 'probabilistic_mroaw'
        config: 配置对象（BaseConfig 实例）

    Returns:
        EdgeWeightStrategy 实例，如果 name='base' 则返回 None（表示使用原始权重）

    Raises:
        ValueError: 如果策略名称不存在
    """
    if name not in _STRATEGY_REGISTRY:
        available = ', '.join(_STRATEGY_REGISTRY.keys())
        raise ValueError(f"未知的边权重策略: '{name}'。可用策略: {available}")

    strategy_class = _STRATEGY_REGISTRY[name]
    if strategy_class is None:
        return None  # base 策略：不做修改

    return strategy_class(config)
