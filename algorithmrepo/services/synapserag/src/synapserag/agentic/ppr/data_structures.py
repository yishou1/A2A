"""
GeAR Agent的核心数据结构

定义Path、QueryPlan等数据结构，用于表示推理路径和查询计划。
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Tuple
import json


@dataclass
class Path:
    """
    表示一条推理路径
    
    推理路径由一系列节点和关系组成，例如：
    "44th president" --[is]--> "Barack Obama" --[attended]--> "Harvard Law School"
    """
    nodes: List[str] = field(default_factory=list)  # 节点ID/名称序列
    relations: List[str] = field(default_factory=list)  # 关系序列
    documents: List[str] = field(default_factory=list)  # 相关文档ID
    score: float = 0.0  # 路径得分
    reasoning: str = ""  # LLM生成的推理解释
    hop_number: int = 0  # 当前跳数
    
    def extend(self, next_node: str, relation: Optional[str] = None, document: Optional[str] = None) -> 'Path':
        """
        扩展路径，添加下一个节点
        
        Args:
            next_node: 下一个节点的ID/名称
            relation: 从当前节点到下一个节点的关系
            document: 相关文档ID
            
        Returns:
            新的Path对象（不修改原对象）
        """
        new_path = Path(
            nodes=self.nodes + [next_node],
            relations=self.relations + [relation] if relation else self.relations,
            documents=self.documents + [document] if document else self.documents,
            score=self.score,  # 保持原分数，后续会重新评估
            reasoning=self.reasoning,
            hop_number=self.hop_number + 1
        )
        return new_path
    
    def to_string(self, include_relations: bool = True) -> str:
        """
        转换为可读字符串
        
        Args:
            include_relations: 是否包含关系信息
            
        Returns:
            格式化的路径字符串
        """
        if not self.nodes:
            return "[Empty Path]"
        
        if not include_relations or not self.relations:
            return " -> ".join(self.nodes)
        
        # 构建包含关系的路径字符串
        path_str = self.nodes[0]
        for i in range(len(self.relations)):
            if i < len(self.relations):
                path_str += f" --[{self.relations[i]}]--> "
            if i + 1 < len(self.nodes):
                path_str += self.nodes[i + 1]
        
        return path_str
    
    def get_end_node(self) -> Optional[str]:
        """获取路径的终点节点"""
        return self.nodes[-1] if self.nodes else None
    
    def get_start_node(self) -> Optional[str]:
        """获取路径的起点节点"""
        return self.nodes[0] if self.nodes else None
    
    def contains_node(self, node: str) -> bool:
        """检查路径是否包含某个节点"""
        return node in self.nodes
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            'nodes': self.nodes,
            'relations': self.relations,
            'documents': self.documents,
            'score': self.score,
            'reasoning': self.reasoning,
            'hop_number': self.hop_number,
            'path_string': self.to_string()
        }
    
    def __repr__(self) -> str:
        return f"Path(hops={self.hop_number}, score={self.score:.3f}, path={self.to_string()})"


@dataclass
class QueryPlan:
    """
    查询分析结果，包含推理计划
    
    LLM分析查询后生成的结构化推理计划
    """
    query: str  # 原始查询
    reasoning_steps: List[str] = field(default_factory=list)  # 推理步骤
    key_entities: List[str] = field(default_factory=list)  # 关键实体
    relation_types: List[str] = field(default_factory=list)  # 预期的关系类型
    expected_hops: int = 1  # 预期跳数
    complexity: str = "simple"  # 复杂度: simple, moderate, complex
    confidence: float = 1.0  # 分析的置信度
    raw_response: str = ""  # LLM的原始响应
    
    def is_multi_hop(self) -> bool:
        """判断是否为多跳问题"""
        return self.expected_hops >= 2
    
    def get_first_step_entities(self) -> List[str]:
        """
        获取第一步应该查找的实体
        
        用于初始节点选择
        """
        # 简单策略：返回前几个关键实体
        return self.key_entities[:3] if self.key_entities else []
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            'query': self.query,
            'reasoning_steps': self.reasoning_steps,
            'key_entities': self.key_entities,
            'relation_types': self.relation_types,
            'expected_hops': self.expected_hops,
            'complexity': self.complexity,
            'confidence': self.confidence
        }
    
    def __repr__(self) -> str:
        return f"QueryPlan(query='{self.query[:50]}...', hops={self.expected_hops}, complexity={self.complexity})"


@dataclass
class InitialNode:
    """
    初始节点，作为路径探索的起点
    """
    node_id: str  # 节点ID
    node_content: str  # 节点内容（实体名称等）
    relevance_score: float = 0.0  # 相关性得分
    reasoning: str = ""  # 为什么选择这个节点作为起点
    node_type: str = "entity"  # 节点类型: entity, passage, etc.
    
    def to_path(self) -> Path:
        """将初始节点转换为Path对象"""
        return Path(
            nodes=[self.node_id],
            relations=[],
            documents=[],
            score=self.relevance_score,
            reasoning=self.reasoning,
            hop_number=0
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            'node_id': self.node_id,
            'node_content': self.node_content,
            'relevance_score': self.relevance_score,
            'reasoning': self.reasoning,
            'node_type': self.node_type
        }
    
    def __repr__(self) -> str:
        return f"InitialNode(id={self.node_id}, content='{self.node_content[:30]}...', score={self.relevance_score:.3f})"


@dataclass
class PathScore:
    """
    路径评分详细信息
    """
    path: Path
    completeness: float = 0.0  # 完整性得分 (0-1)
    coherence: float = 0.0  # 连贯性得分 (0-1)
    relevance: float = 0.0  # 相关性得分 (0-1)
    overall_score: float = 0.0  # 总分
    explanation: str = ""  # 评分解释
    
    # 权重配置（可在初始化时自定义）
    weight_completeness: float = 0.3
    weight_coherence: float = 0.3
    weight_relevance: float = 0.4
    
    def compute_overall_score(self) -> float:
        """计算总分"""
        self.overall_score = (
            self.completeness * self.weight_completeness +
            self.coherence * self.weight_coherence +
            self.relevance * self.weight_relevance
        )
        return self.overall_score
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            'path': self.path.to_dict(),
            'completeness': self.completeness,
            'coherence': self.coherence,
            'relevance': self.relevance,
            'overall_score': self.overall_score,
            'explanation': self.explanation
        }
    
    def __repr__(self) -> str:
        return f"PathScore(overall={self.overall_score:.3f}, C={self.completeness:.2f}, Co={self.coherence:.2f}, R={self.relevance:.2f})"


@dataclass
class NodeCandidate:
    """
    路径扩展时的候选节点
    """
    node_id: str  # 节点ID
    node_content: str  # 节点内容
    relation_from_parent: Optional[str] = None  # 从父节点到此节点的关系
    score: float = 0.0  # 候选得分
    reasoning: str = ""  # 选择理由
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            'node_id': self.node_id,
            'node_content': self.node_content,
            'relation': self.relation_from_parent,
            'score': self.score,
            'reasoning': self.reasoning
        }
    
    def __repr__(self) -> str:
        return f"NodeCandidate(id={self.node_id}, score={self.score:.3f})"


# 辅助函数

def paths_to_json(paths: List[Path], indent: int = 2) -> str:
    """将路径列表转换为JSON字符串"""
    return json.dumps([p.to_dict() for p in paths], indent=indent, ensure_ascii=False)


def load_paths_from_json(json_str: str) -> List[Path]:
    """从JSON字符串加载路径列表"""
    data = json.loads(json_str)
    paths = []
    for item in data:
        path = Path(
            nodes=item.get('nodes', []),
            relations=item.get('relations', []),
            documents=item.get('documents', []),
            score=item.get('score', 0.0),
            reasoning=item.get('reasoning', ''),
            hop_number=item.get('hop_number', 0)
        )
        paths.append(path)
    return paths


