"""
Query Rewriter - 查询重写模块

职责:
1. 基于Gist Memory和Reasoner的反馈,重写查询
2. 聚焦仍缺失的信息
3. 生成更具体的下一轮查询

参考: GEAR论文的Query Re-writing (Section 5.3)
"""

import logging
import re
from typing import Optional, Set
from ..memory.gist_memory import GistMemory

logger = logging.getLogger(__name__)


# Prompt模板(参考GEAR论文)
QUERY_REWRITE_PROMPT = """# Task Description:
You will be presented with an input question and a set of known facts.
These facts might be insufficient for answering the question for some reason.
Your task is to analyze the question given the provided facts and determine what else information is needed for the next step.

# Example:
Question: What region of the state where Guy Shepherdson was born, contains SMA Negeri 68?
Facts: ("Guy Shepherdson", "born in", "Jakarta")
Reason: The provided facts only indicate that Guy Shepherdson was born in Jakarta, but they do not provide any information about the region of the state that contains SMA Negeri 68.
Next Question: What region of Jakarta contains SMA Negeri 68?

# Your Task:
Question: {original_query}

Facts:
{facts}

Reason: {reason}

Next Question:
"""

NAMED_ENTITY_STOPWORDS = {
    "when", "what", "why", "who", "which", "how", "where", "is", "it", "the",
    "a", "an", "that", "this"
}


class QueryRewriter:
    """
    查询重写器

    基于当前知识状态,重写查询以聚焦缺失信息
    """

    def __init__(self, llm_model):
        """
        初始化QueryRewriter

        Args:
            llm_model: LLM模型实例
        """
        self.llm_model = llm_model
        logger.info("QueryRewriter已初始化")

    def rewrite(self,
               original_query: str,
               gist_memory: GistMemory,
               reasoning: str) -> str:
        """
        重写查询

        Args:
            original_query: 原始查询
            gist_memory: 当前的Gist Memory
            reasoning: Reasoner给出的"为什么不足"的理由

        Returns:
            重写后的查询
        """
        logger.info("重写查询")

        # 调用LLM进行重写
        rewritten_query = self._rewrite_with_llm(
            original_query=original_query,
            gist_memory=gist_memory,
            reasoning=reasoning
        )

        grounded_query = self._ground_rewrite(
            rewritten_query=rewritten_query,
            original_query=original_query,
            gist_memory=gist_memory
        )

        if grounded_query != rewritten_query:
            logger.warning("重写结果未引用已知实体,回退至原始查询")

        logger.info(f"重写后: {grounded_query}")

        return grounded_query

    def _rewrite_with_llm(self,
                         original_query: str,
                         gist_memory: GistMemory,
                         reasoning: str) -> str:
        """
        使用LLM进行重写

        Args:
            original_query: 原始查询
            gist_memory: Gist Memory
            reasoning: 推理解释

        Returns:
            重写后的查询
        """
        # 格式化事实
        facts_str = gist_memory.format_for_prompt(
            include_scores=False,
            max_triples=30  # 限制token数量
        )

        # 构建prompt
        prompt = QUERY_REWRITE_PROMPT.format(
            original_query=original_query,
            facts=facts_str,
            reason=reasoning
        )

        # 调用LLM
        try:
            messages = [{"role": "user", "content": prompt}]
            response, metadata, _ = self.llm_model.infer(messages)

            logger.debug(f"LLM响应: {response[:200]}...")

            # 解析响应
            rewritten_query = self._parse_response(response)

            # 验证重写结果
            if not rewritten_query or len(rewritten_query.strip()) < 5:
                logger.warning("重写结果无效,使用原查询")
                return original_query

            return rewritten_query

        except Exception as e:
            logger.error(f"LLM重写失败: {e}")
            # 返回原查询
            return original_query

    def _parse_response(self, response: str) -> str:
        """
        解析LLM响应,提取重写后的查询

        Args:
            response: LLM的响应文本

        Returns:
            重写后的查询
        """
        # 方法1: 查找"Next Question:"后面的内容
        match = re.search(r'Next Question:\s*(.+?)(?:\n|$)', response, re.IGNORECASE | re.DOTALL)
        if match:
            rewritten = match.group(1).strip()
            # 移除可能的引号
            rewritten = rewritten.strip('"\'')
            return rewritten

        # 方法2: 如果没有明确标记,取最后一行(可能是查询)
        lines = [line.strip() for line in response.split('\n') if line.strip()]
        if lines:
            last_line = lines[-1]
            # 检查是否像一个查询(以?结尾,或包含疑问词)
            if last_line.endswith('?') or any(word in last_line.lower() for word in ['what', 'where', 'who', 'when', 'which', 'how']):
                return last_line.strip('"\'')

        # 方法3: 返回整个响应(去除多余空白)
        return ' '.join(response.split()).strip('"\'')

    def _ground_rewrite(self,
                        rewritten_query: str,
                        original_query: str,
                        gist_memory: GistMemory) -> str:
        """
        确保重写结果仍然锚定在已有事实上,避免凭空引入新实体
        """
        if not rewritten_query:
            return original_query

        gist_entities = {entity.lower() for entity in gist_memory.get_entities() if entity}
        if not gist_entities:
            # 还没有事实,允许自由重写
            return rewritten_query

        rewritten_entities = self._extract_named_entities(rewritten_query)
        if rewritten_entities and self._has_overlap(rewritten_entities, gist_entities):
            return rewritten_query

        original_entities = self._extract_named_entities(original_query)
        original_entity_set = {entity.lower() for entity in original_entities}
        if rewritten_entities and original_entity_set and self._has_overlap(rewritten_entities, original_entity_set):
            return rewritten_query

        # 不包含任何已知实体,回退原查询以避免幻觉
        return original_query

    @staticmethod
    def _has_overlap(entities: Set[str], reference: Set[str]) -> bool:
        return any(entity.lower() in reference for entity in entities)

    def _extract_named_entities(self, text: str) -> Set[str]:
        """
        基于大写启发式提取候选实体,避免依赖额外的NLP库
        """
        if not text:
            return set()

        matches = re.findall(r"[A-Z][A-Za-z]*(?:\s+[A-Z][A-Za-z]*)*", text)
        cleaned = set()
        for match in matches:
            candidate = match.strip()
            if not candidate:
                continue
            if candidate.lower() in NAMED_ENTITY_STOPWORDS:
                continue
            cleaned.add(candidate)
        return cleaned

    def simple_rewrite(self,
                      original_query: str,
                      gist_memory: GistMemory) -> str:
        """
        简单的查询重写(不使用LLM)

        基于Gist Memory中的实体,生成聚焦性查询

        Args:
            original_query: 原始查询
            gist_memory: Gist Memory

        Returns:
            重写后的查询
        """
        logger.info("使用简单规则重写查询")

        # 提取已知实体
        known_entities = list(gist_memory.get_entities())

        if not known_entities:
            # 没有已知实体,返回原查询
            return original_query

        # 简单策略:在原查询基础上,强调某个已知实体
        # 例如:"What is X?" → "Tell me more about Y related to X"
        # 其中Y是从Gist Memory中提取的实体

        # 取第一个实体(可以更智能地选择)
        focus_entity = known_entities[0]

        # 构建新查询
        rewritten = f"What information is related to {focus_entity} that can help answer: {original_query}"

        logger.info(f"简单重写: {rewritten}")

        return rewritten
