"""
查询分析器

使用LLM分析多跳查询，生成结构化的推理计划。
"""

import json
import logging
import re
from typing import Optional, Dict, Any
from .data_structures import QueryPlan
from .prompts.query_analysis import QUERY_ANALYSIS_PROMPT, QUERY_ANALYSIS_SYSTEM

logger = logging.getLogger(__name__)


class QueryAnalyzer:
    """
    查询分析器
    
    使用LLM分析查询，生成推理计划，包括：
    - 推理步骤分解
    - 关键实体识别
    - 预期跳数估计
    - 复杂度评估
    """
    
    def __init__(self, llm_model, use_system_prompt: bool = True):
        """
        初始化查询分析器
        
        Args:
            llm_model: LLM模型实例（SynapseRAG的llm_model）
            use_system_prompt: 是否使用system prompt
        """
        self.llm_model = llm_model
        self.use_system_prompt = use_system_prompt
        
        # 缓存查询分析结果
        self.cache = {}
    
    def analyze(self, query: str, use_cache: bool = True) -> QueryPlan:
        """
        分析查询，生成推理计划
        
        Args:
            query: 查询文本
            use_cache: 是否使用缓存
            
        Returns:
            QueryPlan对象
        """
        # 检查缓存
        if use_cache and query in self.cache:
            logger.debug(f"使用缓存的查询分析结果: {query[:50]}...")
            return self.cache[query]
        
        logger.info(f"分析查询: {query}")
        
        try:
            # 构建消息
            messages = self._build_messages(query)
            
            # 调用LLM
            response, metadata, _ = self.llm_model.infer(messages)
            
            logger.debug(f"LLM响应: {response[:200]}...")
            
            # 解析响应
            query_plan = self._parse_response(query, response)
            
            # 缓存结果
            if use_cache:
                self.cache[query] = query_plan
            
            logger.info(f"查询分析完成: {query_plan}")
            
            return query_plan
            
        except Exception as e:
            logger.error(f"查询分析失败: {e}")
            # 返回默认的简单计划
            return self._create_fallback_plan(query)
    
    def _build_messages(self, query: str) -> list:
        """构建LLM消息"""
        messages = []
        
        if self.use_system_prompt:
            messages.append({
                "role": "system",
                "content": QUERY_ANALYSIS_SYSTEM
            })
        
        user_content = QUERY_ANALYSIS_PROMPT.format(query=query)
        messages.append({
            "role": "user",
            "content": user_content
        })
        
        return messages
    
    def _parse_response(self, query: str, response: str) -> QueryPlan:
        """
        解析LLM响应，提取QueryPlan
        
        Args:
            query: 原始查询
            response: LLM响应文本
            
        Returns:
            QueryPlan对象
        """
        try:
            # 尝试提取JSON
            json_str = self._extract_json(response)
            
            if json_str:
                data = json.loads(json_str)
                
                # 构建QueryPlan
                query_plan = QueryPlan(
                    query=query,
                    reasoning_steps=data.get('reasoning_steps', []),
                    key_entities=data.get('key_entities', []),
                    relation_types=data.get('relation_types', []),
                    expected_hops=int(data.get('expected_hops', 1)),
                    complexity=data.get('complexity', 'simple'),
                    confidence=float(data.get('confidence', 1.0)),
                    raw_response=response
                )
                
                return query_plan
            else:
                logger.warning("无法从响应中提取JSON，使用备用解析")
                return self._parse_response_fallback(query, response)
                
        except Exception as e:
            logger.error(f"解析响应失败: {e}")
            return self._create_fallback_plan(query)
    
    def _extract_json(self, text: str) -> Optional[str]:
        """
        从文本中提取JSON字符串
        
        尝试多种方法提取JSON：
        1. 查找被```json包围的JSON
        2. 查找第一个完整的JSON对象
        3. 使用正则表达式提取
        """
        # 方法1: 查找markdown代码块
        json_pattern = r'```json\s*(.*?)\s*```'
        match = re.search(json_pattern, text, re.DOTALL)
        if match:
            return match.group(1).strip()
        
        # 方法2: 查找纯JSON代码块
        json_pattern = r'```\s*(.*?)\s*```'
        match = re.search(json_pattern, text, re.DOTALL)
        if match:
            return match.group(1).strip()
        
        # 方法3: 查找完整的JSON对象
        try:
            # 找到第一个 { 和最后一个 }
            start = text.find('{')
            end = text.rfind('}')
            if start != -1 and end != -1 and end > start:
                json_str = text[start:end+1]
                # 验证是否为有效JSON
                json.loads(json_str)
                return json_str
        except:
            pass
        
        return None
    
    def _parse_response_fallback(self, query: str, response: str) -> QueryPlan:
        """
        备用解析方法
        
        当无法提取JSON时，尝试从文本中提取信息
        """
        logger.info("使用备用解析方法")
        
        # 简单的启发式规则
        reasoning_steps = []
        key_entities = []
        relation_types = []
        expected_hops = 1
        complexity = "simple"
        
        # 尝试从响应中提取信息
        lines = response.split('\n')
        
        for line in lines:
            line = line.strip()
            
            # 查找推理步骤
            if any(keyword in line.lower() for keyword in ['step', '步骤', 'first', 'then', 'finally']):
                if line and not line.startswith('#'):
                    reasoning_steps.append(line)
            
            # 查找跳数
            if 'hop' in line.lower():
                hop_match = re.search(r'(\d+)\s*hop', line, re.IGNORECASE)
                if hop_match:
                    expected_hops = int(hop_match.group(1))
        
        # 根据问题词判断复杂度
        question_words = query.lower().split()
        if len(reasoning_steps) >= 3 or expected_hops >= 3:
            complexity = "complex"
        elif len(reasoning_steps) == 2 or expected_hops == 2:
            complexity = "moderate"
        
        return QueryPlan(
            query=query,
            reasoning_steps=reasoning_steps if reasoning_steps else ["Retrieve relevant information"],
            key_entities=key_entities,
            relation_types=relation_types,
            expected_hops=max(expected_hops, 1),
            complexity=complexity,
            confidence=0.5,  # 低置信度
            raw_response=response
        )
    
    def _create_fallback_plan(self, query: str) -> QueryPlan:
        """
        创建备用的简单计划
        
        当LLM调用失败时使用
        """
        logger.warning(f"创建备用查询计划: {query[:50]}...")
        
        # 简单的启发式规则估计跳数
        expected_hops = 1
        complexity = "simple"
        
        # 检查多跳指标词
        multi_hop_indicators = [
            'where', 'which', 'whose', 'what', 'who',
            'born', 'attended', 'studied', 'worked',
            'capital', 'president', 'author'
        ]
        
        query_lower = query.lower()
        indicator_count = sum(1 for indicator in multi_hop_indicators if indicator in query_lower)
        
        if indicator_count >= 3:
            expected_hops = 2
            complexity = "moderate"
        
        return QueryPlan(
            query=query,
            reasoning_steps=["Retrieve relevant information to answer the question"],
            key_entities=[],
            relation_types=[],
            expected_hops=expected_hops,
            complexity=complexity,
            confidence=0.3,  # 很低的置信度
            raw_response=""
        )
    
    def batch_analyze(self, queries: list) -> list:
        """
        批量分析查询
        
        Args:
            queries: 查询列表
            
        Returns:
            QueryPlan列表
        """
        logger.info(f"批量分析 {len(queries)} 个查询")
        
        plans = []
        for query in queries:
            plan = self.analyze(query)
            plans.append(plan)
        
        return plans
    
    def clear_cache(self):
        """清空缓存"""
        self.cache.clear()
        logger.info("查询分析缓存已清空")

