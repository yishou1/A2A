"""
Gist Memory - 摘要记忆

存储累积的近邻三元组(proximal triples),作为多跳推理的"工作记忆"。

灵感来源: GEAR论文中的海马体-新皮层交互模型
"""

from typing import List, Tuple, Optional, Set
from dataclasses import dataclass, field
import json
import logging


@dataclass
class Triple:
    """三元组数据结构"""
    subject: str
    predicate: str
    object: str
    score: float = 0.0  # 相关性分数
    source_doc: Optional[str] = None  # 来源文档ID
    hop_number: int = 1  # 在第几跳获得

    def to_tuple(self) -> Tuple[str, str, str]:
        """转换为元组格式"""
        return (self.subject, self.predicate, self.object)

    def to_string(self) -> str:
        """格式化为字符串"""
        return f"({self.subject}, {self.predicate}, {self.object})"

    def __hash__(self):
        """支持去重"""
        return hash(self.to_tuple())

    def __eq__(self, other):
        if not isinstance(other, Triple):
            return False
        return self.to_tuple() == other.to_tuple()


class GistMemory:
    """
    Gist Memory - 摘要记忆

    核心功能:
    1. 存储累积的三元组
    2. 去重
    3. 格式化为prompt
    4. 统计信息
    """

    def __init__(self):
        self.triples: List[Triple] = []
        self._triple_set: Set[Tuple] = set()  # 用于快速去重
        self._logger = logging.getLogger(__name__)

    def add_triple(self, triple: Triple) -> bool:
        """
        添加单个三元组

        Returns:
            是否成功添加(False表示重复)
        """
        triple_tuple = triple.to_tuple()

        if triple_tuple in self._triple_set:
            return False  # 重复,不添加

        self.triples.append(triple)
        self._triple_set.add(triple_tuple)
        return True

    def add_triples(self, triples: List[Triple]) -> int:
        """
        批量添加三元组

        Returns:
            成功添加的数量
        """
        added_count = 0
        for triple in triples:
            if self.add_triple(triple):
                added_count += 1
        return added_count

    def get_all_triples(self) -> List[Triple]:
        """获取所有三元组"""
        return self.triples.copy()

    def get_triples_by_hop(self, hop_number: int) -> List[Triple]:
        """获取特定跳数的三元组"""
        return [t for t in self.triples if t.hop_number == hop_number]

    def format_for_prompt(self,
                         include_scores: bool = False,
                         max_triples: Optional[int] = None) -> str:
        """
        格式化为prompt字符串

        Args:
            include_scores: 是否包含分数
            max_triples: 最多包含多少个三元组(None表示全部)

        Returns:
            格式化的字符串,每行一个三元组
        """
        triples_to_format = self.triples

        # 限制数量(如果指定)
        if max_triples is not None:
            # 按分数排序,取top-k
            sorted_triples = sorted(
                triples_to_format,
                key=lambda t: t.score,
                reverse=True
            )
            triples_to_format = sorted_triples[:max_triples]

        # 格式化
        lines = []
        for triple in triples_to_format:
            if include_scores:
                line = f"{triple.to_string()} [score={triple.score:.3f}]"
            else:
                line = triple.to_string()
            lines.append(line)

        return "\n".join(lines)

    def get_entities(self) -> Set[str]:
        """获取所有出现的实体(subject和object)"""
        entities = set()
        for triple in self.triples:
            entities.add(triple.subject)
            entities.add(triple.object)
        return entities

    def get_relations(self) -> Set[str]:
        """获取所有出现的关系(predicate)"""
        return set(triple.predicate for triple in self.triples)

    def is_empty(self) -> bool:
        """是否为空"""
        return len(self.triples) == 0

    def size(self) -> int:
        """三元组数量"""
        return len(self.triples)

    def log_snapshot(self,
                     logger: Optional[logging.Logger] = None,
                     include_scores: bool = True,
                     note: Optional[str] = None) -> None:
        """
        方便在Agent循环中打印当前Gist Memory摘要。

        Args:
            logger: 可选的logger,不提供则使用模块默认logger
            include_scores: 是否打印分数
            note: 自定义标题,便于区分回合
        """
        log = logger or self._logger
        if not log.isEnabledFor(logging.INFO):
            return

        header = note or f"GistMemory snapshot ({self.size()} triples)"
        formatted = self.format_for_prompt(include_scores=include_scores, max_triples=None)
        if not formatted:
            log.info("%s: (empty)", header)
            return
        log.info("%s:\n%s", header, formatted)

    def clear(self):
        """清空记忆"""
        self.triples.clear()
        self._triple_set.clear()

    def to_dict(self) -> dict:
        """转换为字典(用于序列化)"""
        return {
            'triples': [
                {
                    'subject': t.subject,
                    'predicate': t.predicate,
                    'object': t.object,
                    'score': t.score,
                    'source_doc': t.source_doc,
                    'hop_number': t.hop_number
                }
                for t in self.triples
            ],
            'size': self.size()
        }

    def to_json(self, indent: int = 2) -> str:
        """转换为JSON字符串"""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    def __repr__(self) -> str:
        return f"GistMemory(size={self.size()}, entities={len(self.get_entities())})"

    def __str__(self) -> str:
        """打印友好的字符串"""
        if self.is_empty():
            return "GistMemory(empty)"

        preview = self.format_for_prompt(max_triples=3)
        more_info = f"\n... ({self.size() - 3} more)" if self.size() > 3 else ""

        return f"GistMemory({self.size()} triples):\n{preview}{more_info}"
