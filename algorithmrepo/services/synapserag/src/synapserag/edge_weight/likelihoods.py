"""
似然函数模块 - 为贝叶斯边权重计算提供三个独立的似然函数

理论基础：
    P(edge正确 | 证据) ∝ P(edge) × P(E_info|edge) × P(E_struct|edge) × P(E_semantic|edge)

三个似然函数分别衡量：
    1. 信息似然：稀有实体共现 → 更有信息价值 → 边更可信
    2. 结构似然：多路径支撑 → 边更可信
    3. 语义似然：适度语义相似 → 边更可信（太高可能是同义词）
"""

from typing import Dict, Tuple, Set, Optional, List
import numpy as np
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class LikelihoodConfig:
    """似然函数的超参数配置"""
    # 信息似然参数
    info_alpha: float = 1.0  # IDF 的平滑系数
    info_min_freq: int = 1   # 最小频率（避免除零）

    # 结构似然参数
    struct_lambda: float = 2.0  # 路径数量的衰减系数
    struct_max_path_length: int = 3  # 最大路径长度

    # 语义似然参数
    semantic_mu: float = 0.5   # 最优相似度中心
    semantic_sigma: float = 0.25  # 高斯宽度
    semantic_low_threshold: float = 0.1  # 低于此值视为无关
    semantic_high_threshold: float = 0.95  # 高于此值视为同义词（惩罚）


class InformationLikelihood:
    """
    信息似然函数

    基于信息论原理：稀有事件的共现包含更多信息
    使用逆文档频率 (IDF) 风格的计算

    P(E_info | edge正确) = exp(-α × log(freq / total))
                        = (total / freq)^α

    直觉：
    - 高频实体（如 "the", "is"）出现在很多文档中，共现不稀奇
    - 稀有实体（如 "Albert Einstein"）共现更有意义
    """

    def __init__(self, config: LikelihoodConfig = None):
        self.config = config or LikelihoodConfig()

    def compute(
        self,
        src_entity: str,
        dst_entity: str,
        ent_node_to_chunk_ids: Dict[str, Set[str]],
        total_chunks: int
    ) -> float:
        """
        计算信息似然

        Args:
            src_entity: 源实体 ID
            dst_entity: 目标实体 ID
            ent_node_to_chunk_ids: 实体到 chunk 的映射
            total_chunks: 总 chunk 数量

        Returns:
            信息似然值 ∈ (0, 1]
        """
        if total_chunks <= 0:
            return 0.5  # 无法计算，返回中性值

        # 获取两个实体的文档频率
        src_freq = len(ent_node_to_chunk_ids.get(src_entity, set()))
        dst_freq = len(ent_node_to_chunk_ids.get(dst_entity, set()))

        # 避免除零
        src_freq = max(src_freq, self.config.info_min_freq)
        dst_freq = max(dst_freq, self.config.info_min_freq)

        # 计算 IDF 风格的稀有度分数
        # IDF = log(N / df)，我们用 (N / df)^α 的归一化版本
        src_idf = np.log(total_chunks / src_freq + 1)
        dst_idf = np.log(total_chunks / dst_freq + 1)

        # 组合两个实体的 IDF（取几何平均）
        combined_idf = np.sqrt(src_idf * dst_idf)

        # 归一化到 (0, 1]
        # 使用 sigmoid 变换，使极端值不会太极端
        max_idf = np.log(total_chunks + 1)  # 最大可能的 IDF
        normalized = combined_idf / max_idf if max_idf > 0 else 0.5

        # 应用 alpha 参数调节敏感度
        likelihood = np.power(normalized, self.config.info_alpha)

        # 平滑：确保不会太极端
        likelihood = 0.1 + 0.8 * likelihood  # 映射到 [0.1, 0.9]

        return float(likelihood)


class StructuralLikelihood:
    """
    结构似然函数

    基于图论原理：如果两个节点间有多条独立路径，它们的关系更可信

    P(E_struct | edge正确) = 1 - exp(-num_paths / λ)

    直觉：
    - 单一路径可能是噪声
    - 多条路径意味着多个独立来源都支持这个关系
    """

    def __init__(self, config: LikelihoodConfig = None):
        self.config = config or LikelihoodConfig()
        self._path_cache = {}  # 缓存路径计算结果

    def compute(
        self,
        src_entity: str,
        dst_entity: str,
        graph,
        exclude_direct: bool = True
    ) -> float:
        """
        计算结构似然

        Args:
            src_entity: 源实体 ID
            dst_entity: 目标实体 ID
            graph: igraph 图对象
            exclude_direct: 是否排除直接边（只计算间接路径）

        Returns:
            结构似然值 ∈ (0, 1]
        """
        if graph is None:
            return 0.5  # 无图信息，返回中性值

        # 检查缓存
        cache_key = (src_entity, dst_entity)
        if cache_key in self._path_cache:
            return self._path_cache[cache_key]

        try:
            # 获取节点索引
            node_names = graph.vs['name'] if 'name' in graph.vs.attributes() else []
            name_to_idx = {name: idx for idx, name in enumerate(node_names)}

            if src_entity not in name_to_idx or dst_entity not in name_to_idx:
                return 0.5  # 节点不在图中

            src_idx = name_to_idx[src_entity]
            dst_idx = name_to_idx[dst_entity]

            # 计算路径数量（使用 BFS 近似，限制深度）
            # 注意：精确计算路径数量是 NP-hard，这里用近似方法
            num_paths = self._count_paths_bfs(
                graph, src_idx, dst_idx,
                max_length=self.config.struct_max_path_length,
                exclude_direct=exclude_direct
            )

            # 指数衰减公式
            likelihood = 1.0 - np.exp(-num_paths / self.config.struct_lambda)

            # 平滑
            likelihood = 0.1 + 0.8 * likelihood

            # 缓存结果
            self._path_cache[cache_key] = likelihood

            return float(likelihood)

        except Exception as e:
            logger.debug(f"结构似然计算异常: {e}")
            return 0.5

    def _count_paths_bfs(
        self,
        graph,
        src_idx: int,
        dst_idx: int,
        max_length: int,
        exclude_direct: bool
    ) -> int:
        """
        使用 BFS 计算路径数量（近似）

        为了效率，我们不计算所有路径，而是计算可达性的"宽度"
        """
        if src_idx == dst_idx:
            return 0

        # 简化实现：计算两个节点的共同邻居数量
        # 这是 2 跳路径数量的近似
        try:
            src_neighbors = set(graph.neighbors(src_idx))
            dst_neighbors = set(graph.neighbors(dst_idx))

            # 排除直接边
            if exclude_direct:
                src_neighbors.discard(dst_idx)
                dst_neighbors.discard(src_idx)

            # 共同邻居 = 2 跳路径数量
            common_neighbors = len(src_neighbors & dst_neighbors)

            # 如果需要更长的路径，可以扩展
            if max_length >= 3:
                # 3 跳路径：通过 2 度邻居
                # 这里只做粗略估计
                path_estimate = common_neighbors
                for neighbor in list(src_neighbors)[:20]:  # 限制计算量
                    neighbor_neighbors = set(graph.neighbors(neighbor))
                    path_estimate += len(neighbor_neighbors & dst_neighbors) * 0.5

                return int(path_estimate)

            return common_neighbors

        except Exception:
            return 0

    def clear_cache(self):
        """清空路径缓存"""
        self._path_cache.clear()


class SemanticLikelihood:
    """
    语义似然函数

    基于语义相似性：如果两个实体语义相关（但不是同义词），它们的关系更可信

    P(E_semantic | edge正确) = Gaussian(similarity, μ, σ) × penalty(if too_similar)

    直觉：
    - 完全不相关的实体（相似度 < 0.1）：关系不可信
    - 适度相关（相似度 ~ 0.5）：最可能是真实关系
    - 高度相似（相似度 > 0.95）：可能是同义词，不是真正的关系
    """

    def __init__(self, config: LikelihoodConfig = None):
        self.config = config or LikelihoodConfig()

    def compute(
        self,
        src_entity: str,
        dst_entity: str,
        entity_embeddings: Dict[str, np.ndarray]
    ) -> float:
        """
        计算语义似然

        Args:
            src_entity: 源实体 ID
            dst_entity: 目标实体 ID
            entity_embeddings: 实体嵌入字典

        Returns:
            语义似然值 ∈ (0, 1]
        """
        if entity_embeddings is None:
            return 0.5  # 无嵌入信息，返回中性值

        src_emb = entity_embeddings.get(src_entity)
        dst_emb = entity_embeddings.get(dst_entity)

        if src_emb is None or dst_emb is None:
            return 0.5

        # 计算余弦相似度
        similarity = self._cosine_similarity(src_emb, dst_emb)

        # 低相似度惩罚
        if similarity < self.config.semantic_low_threshold:
            return 0.1  # 不太可能是真实关系

        # 高相似度惩罚（可能是同义词）
        if similarity > self.config.semantic_high_threshold:
            # 同义词边应该用其他方式处理，这里降低置信度
            return 0.3

        # 高斯似然：中等相似度最优
        gaussian_value = np.exp(
            -0.5 * ((similarity - self.config.semantic_mu) / self.config.semantic_sigma) ** 2
        )

        # 平滑
        likelihood = 0.1 + 0.8 * gaussian_value

        return float(likelihood)

    def _cosine_similarity(self, a: np.ndarray, b: np.ndarray) -> float:
        """计算余弦相似度"""
        a = np.asarray(a).flatten()
        b = np.asarray(b).flatten()

        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)

        if norm_a == 0 or norm_b == 0:
            return 0.0

        return float(np.dot(a, b) / (norm_a * norm_b))


class PriorCalculator:
    """
    先验概率计算器

    P(edge) 基于共现频率的经验先验
    """

    def __init__(self, smoothing: float = 1.0):
        """
        Args:
            smoothing: Laplace 平滑系数
        """
        self.smoothing = smoothing

    def compute(
        self,
        base_weight: float,
        total_edges: int,
        edge_type: str = 'entity_entity'
    ) -> float:
        """
        计算先验概率

        Args:
            base_weight: 基础权重（共现次数）
            total_edges: 总边数
            edge_type: 边类型

        Returns:
            先验概率 ∈ (0, 1)
        """
        if total_edges <= 0:
            return 0.5

        # 基于共现频率的先验
        # 高频共现 → 更可能是真实关系
        freq_prior = (base_weight + self.smoothing) / (total_edges + self.smoothing * 2)

        # 根据边类型调整
        type_multiplier = {
            'passage_entity': 0.8,    # passage 边稍微降权
            'entity_entity': 1.0,     # 关系边保持
            'entity_passage': 0.8,
            'synonymy': 0.9,          # 同义边
            'unknown': 0.7,
        }

        prior = freq_prior * type_multiplier.get(edge_type, 1.0)

        # 确保在合理范围内
        prior = max(0.01, min(0.99, prior))

        return float(prior)
