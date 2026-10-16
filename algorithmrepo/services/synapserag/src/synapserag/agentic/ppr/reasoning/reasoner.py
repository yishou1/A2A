"""
Reasoner - 推理验证模块

职责:
1. 评估当前Gist Memory是否足以回答查询
2. 判断是否应该终止迭代
3. 提供详细的推理解释

参考: GEAR论文的Reasoning for Termination (Section 5.2)
"""

import logging
import re
from typing import Tuple, Optional
from ..memory.gist_memory import GistMemory

logger = logging.getLogger(__name__)


# Prompt模板(参考GEAR论文)
REASONER_PROMPT = """# Task Description:
You are given an input question and a set of known facts.
Use ONLY the original question and the provided facts. Do not rely on world knowledge,
do not speculate, and do not invent entity names or organizations that are not explicitly
mentioned. If the facts do not contain the needed information, clearly state what is missing.

Question: {query}

Facts:
{facts}

Your reply must follow the required format:

1. If the provided facts contain the answer to the question, you should reply as follows:
Answerable: Yes
Answer: ...

2. If not, you should explain why and reply as follows:
Answerable: No
Why: ...

# Your reply:
"""


class Reasoner:
    """
    推理验证器

    评估Gist Memory是否足以回答查询
    """

    def __init__(self, llm_model):
        """
        初始化Reasoner

        Args:
            llm_model: LLM模型实例
        """
        self.llm_model = llm_model
        logger.info("Reasoner已初始化")

    def should_terminate(self,
                        query: str,
                        gist_memory: GistMemory,
                        min_confidence: float = 0.7) -> Tuple[bool, str, Optional[str]]:
        """
        判断是否应该终止迭代

        Args:
            query: 原始查询
            gist_memory: 当前的Gist Memory
            min_confidence: 最小置信度阈值

        Returns:
            (should_stop, reasoning, answer)
            - should_stop: 是否应该终止
            - reasoning: 推理解释(为什么可以/不能回答)
            - answer: 答案(如果可以回答)
        """
        logger.info("评估是否可以回答查询")

        # 检查记忆是否为空
        if gist_memory.is_empty():
            logger.info("Gist Memory为空,无法回答")
            return False, "No facts available yet.", None

        # 调用LLM进行推理
        answerable, reasoning, answer = self._reason_with_llm(query, gist_memory)

        if answerable:
            logger.info(f"查询可回答: {answer}")
            return True, reasoning, answer
        else:
            logger.info(f"查询不可回答: {reasoning}")
            return False, reasoning, None

    def _reason_with_llm(self,
                        query: str,
                        gist_memory: GistMemory) -> Tuple[bool, str, Optional[str]]:
        """
        使用LLM进行推理

        Args:
            query: 查询
            gist_memory: Gist Memory

        Returns:
            (answerable, reasoning, answer)
        """
        # 格式化事实
        facts_str = gist_memory.format_for_prompt(
            include_scores=False,
            max_triples=50  # 限制token数量
        )

        # 构建prompt
        prompt = REASONER_PROMPT.format(
            query=query,
            facts=facts_str
        )

        # 调用LLM
        try:
            messages = [{"role": "user", "content": prompt}]
            response, metadata, _ = self.llm_model.infer(messages)

            logger.debug(f"LLM响应: {response[:200]}...")

            # 解析响应
            answerable, reasoning, answer = self._parse_response(response)

            return answerable, reasoning, answer

        except Exception as e:
            logger.error(f"LLM推理失败: {e}")
            # 返回保守的结果
            return False, f"LLM推理失败: {str(e)}", None

    def _parse_response(self, response: str) -> Tuple[bool, str, Optional[str]]:
        """
        解析LLM响应

        期望格式:
        Answerable: Yes/No
        Answer: ... (如果Yes)
        Why: ... (如果No)

        Args:
            response: LLM的响应文本

        Returns:
            (answerable, reasoning, answer)
        """
        # 检查是否可回答
        answerable = "answerable: yes" in response.lower()

        reasoning = ""
        answer = None

        if answerable:
            # 提取答案
            answer_match = re.search(r'Answer:\s*(.+?)(?:\n|$)', response, re.IGNORECASE | re.DOTALL)
            if answer_match:
                answer = answer_match.group(1).strip()
                reasoning = f"Found answer: {answer}"
            else:
                # 没找到答案,认为不可回答
                answerable = False
                reasoning = "LLM said answerable but no answer found"
        else:
            # 提取原因
            why_match = re.search(r'Why:\s*(.+?)(?:\n|$)', response, re.IGNORECASE | re.DOTALL)
            if why_match:
                reasoning = why_match.group(1).strip()
            else:
                reasoning = "Cannot answer the question with current facts."

        return answerable, reasoning, answer

    def evaluate_completeness(self, gist_memory: GistMemory, query: str) -> float:
        """
        评估Gist Memory的完整性(0-1分数)

        简单启发式:
        - 三元组数量
        - 覆盖的实体数量
        - (可选)与查询的相关性

        Args:
            gist_memory: Gist Memory
            query: 查询

        Returns:
            完整性分数(0-1)
        """
        if gist_memory.is_empty():
            return 0.0

        # 启发式规则
        triple_count = gist_memory.size()
        entity_count = len(gist_memory.get_entities())

        # 归一化
        completeness = min(1.0, (triple_count / 10.0) * 0.5 + (entity_count / 15.0) * 0.5)

        return completeness
