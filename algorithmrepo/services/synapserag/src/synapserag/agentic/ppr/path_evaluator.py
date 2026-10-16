"""
简化版路径评估器（Simplified PathEvaluator）

## 设计理念：
- 不在检索时使用（保持效率）
- 仅用于离线分析（提供可解释性）
- 提取PPR隐式路径并解释

## 当前版本特点：
✅ 实现了：
1. 从PPR结果提取隐式路径
2. LLM生成路径解释
3. 失败案例分析

❌ 未实现（未来改进方向）：
1. 实时路径质量评估
2. 多路径对比选择
3. 路径连贯性量化评分
4. 与检索流程深度集成

## 使用场景：
- 论文案例分析
- 系统调试
- 用户可解释性

## 注意事项：
- 仅在分析时调用，不集成到retrieve()流程
- LLM调用会产生额外成本
- 适合小批量查询分析
"""

import logging
from typing import List, Dict, Tuple, Optional
import numpy as np
from .data_structures import Path, PathScore
from .prompts.path_reasoning import PATH_EVALUATION_PROMPT

logger = logging.getLogger(__name__)


class SimplifiedPathEvaluator:
    """
    简化版路径评估器
    
    核心功能：
    1. 从PPR结果提取隐式推理路径
    2. 生成路径的自然语言解释
    3. 分析失败案例
    
    设计原则：
    - 离线使用，不影响检索效率
    - 提供必要的可解释性
    - 支持论文案例分析
    """
    
    def __init__(self, llm_model):
        """
        初始化简化版路径评估器
        
        Args:
            llm_model: LLM模型实例
        """
        self.llm_model = llm_model
        logger.info("SimplifiedPathEvaluator已初始化（v0.1 - 基础版）")
    
    def extract_implicit_path_from_ppr(self, 
                                       query: str, 
                                       hop_results: List[Dict],
                                       top_k_per_hop: int = 5) -> Path:
        """
        从PPR多跳结果中提取隐式路径
        
        Args:
            query: 查询文本
            hop_results: 每一跳的结果列表
                格式: [
                    {hop: 1, entities: [(entity, score), ...], docs: [...]},
                    {hop: 2, entities: [(entity, score), ...], docs: [...]},
                ]
            top_k_per_hop: 每一跳提取的top-k实体
            
        Returns:
            Path对象，包含提取的推理路径
        """
        logger.info(f"从{len(hop_results)}跳PPR结果中提取隐式路径")
        
        nodes = []
        relations = []
        
        for hop_result in hop_results:
            hop_num = hop_result.get('hop', 0)
            entities = hop_result.get('entities', [])
            
            # 提取top-k实体
            top_entities = entities[:top_k_per_hop]
            
            for entity, score in top_entities:
                nodes.append(entity)
                # 关系暂时设为简单的hop标识
                if len(relations) < len(nodes) - 1:
                    relations.append(f"hop{hop_num}_link")
        
        path = Path(
            nodes=nodes,
            relations=relations,
            documents=[],  # 文档信息可以后续添加
            score=0.0,
            reasoning="",
            hop_number=len(hop_results)
        )
        
        logger.debug(f"提取路径: {path.to_string()}")
        
        return path
    
    def explain_path(self, query: str, path: Path) -> str:
        """
        生成路径的自然语言解释
        
        使用LLM解释为什么这条路径能够回答查询
        
        Args:
            query: 查询文本
            path: 推理路径
            
        Returns:
            自然语言解释
        """
        logger.info(f"生成路径解释: {query}")
        
        # 构建prompt
        prompt = f"""
You are analyzing a multi-hop reasoning path for a question-answering system.

Question: {query}

Reasoning Path:
{path.to_string(include_relations=True)}

Path nodes (in order):
{' → '.join(path.nodes)}

Please provide:
1. A brief explanation of why this path leads to the answer
2. What each hop in the reasoning chain represents
3. Whether this path seems coherent and complete

Keep your response concise (2-3 sentences).
"""
        
        try:
            # 调用LLM
            messages = [
                {"role": "user", "content": prompt}
            ]
            response, _, _ = self.llm_model.infer(messages)
            
            logger.debug(f"LLM解释: {response[:100]}...")
            
            return response
            
        except Exception as e:
            logger.error(f"LLM调用失败: {e}")
            return f"路径解释生成失败: {e}"
    
    def analyze_failure_case(self, 
                            query: str, 
                            retrieved_docs: List[str],
                            gold_docs: List[str],
                            ppr_results: Optional[Dict] = None) -> Dict:
        """
        分析失败案例
        
        识别为什么没有检索到正确文档
        
        Args:
            query: 查询文本
            retrieved_docs: 实际检索到的文档
            gold_docs: 正确答案文档
            ppr_results: PPR结果（可选）
            
        Returns:
            分析结果字典
        """
        logger.info(f"分析失败案例: {query}")
        
        # 检查是否有overlap
        retrieved_set = set(retrieved_docs)
        gold_set = set(gold_docs)
        overlap = retrieved_set.intersection(gold_set)
        
        analysis = {
            'query': query,
            'retrieved_count': len(retrieved_docs),
            'gold_count': len(gold_docs),
            'overlap_count': len(overlap),
            'recall': len(overlap) / len(gold_set) if gold_set else 0.0,
            'possible_reasons': []
        }
        
        # 分析可能的原因
        if len(overlap) == 0:
            analysis['possible_reasons'].append("完全没有检索到正确文档")
            
            # 简单的启发式分析
            if ppr_results:
                analysis['possible_reasons'].append("PPR可能没有识别正确的起点实体")
            else:
                analysis['possible_reasons'].append("需要查看PPR结果以进一步分析")
        
        elif len(overlap) < len(gold_set):
            analysis['possible_reasons'].append(f"部分召回：检索到{len(overlap)}/{len(gold_set)}个正确文档")
            analysis['possible_reasons'].append("可能需要增加检索数量或调整参数")
        
        logger.info(f"分析完成: recall={analysis['recall']:.2f}")
        
        return analysis
    
    def batch_explain_paths(self, 
                           queries: List[str],
                           paths: List[Path]) -> List[str]:
        """
        批量生成路径解释
        
        Args:
            queries: 查询列表
            paths: 对应的路径列表
            
        Returns:
            解释列表
        """
        logger.info(f"批量生成{len(queries)}个路径解释")
        
        explanations = []
        
        for query, path in zip(queries, paths):
            try:
                explanation = self.explain_path(query, path)
                explanations.append(explanation)
            except Exception as e:
                logger.error(f"路径解释失败: {e}")
                explanations.append(f"Error: {e}")
        
        return explanations


# 向后兼容的别名
PathEvaluator = SimplifiedPathEvaluator

