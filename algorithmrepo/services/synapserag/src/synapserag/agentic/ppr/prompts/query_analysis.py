"""
查询分析Prompt模板

用于LLM分析多跳查询，生成推理计划。
"""

QUERY_ANALYSIS_SYSTEM = """You are an expert at analyzing multi-hop reasoning questions. Your task is to decompose complex questions into atomic reasoning steps and identify the key information needed to answer them."""

QUERY_ANALYSIS_PROMPT = """Analyze the following question and create a detailed reasoning plan.

Question: {query}

Please provide a comprehensive analysis including:

1. **Reasoning Steps**: Break down the question into sequential atomic steps needed to find the answer.
2. **Key Entities**: Identify the main entities mentioned or implied in the question.
3. **Relation Types**: Predict what types of relationships you'll need to traverse (e.g., "is_president_of", "attended_university", "capital_of").
4. **Expected Hops**: Estimate how many reasoning steps (graph hops) are needed.
5. **Complexity**: Classify the question as "simple" (1 hop), "moderate" (2 hops), or "complex" (3+ hops).

Output your analysis as a JSON object with this exact structure:
{{
    "reasoning_steps": ["step1", "step2", ...],
    "key_entities": ["entity1", "entity2", ...],
    "relation_types": ["relation1", "relation2", ...],
    "expected_hops": <number>,
    "complexity": "simple|moderate|complex",
    "confidence": <0.0-1.0>
}}

Examples:

Example 1:
Question: "What is the capital of France?"
Analysis:
{{
    "reasoning_steps": ["Look up the capital city of France"],
    "key_entities": ["France", "capital"],
    "relation_types": ["capital_of"],
    "expected_hops": 1,
    "complexity": "simple",
    "confidence": 1.0
}}

Example 2:
Question: "Which university did the 44th president of the United States attend?"
Analysis:
{{
    "reasoning_steps": [
        "Identify who is the 44th president of the United States",
        "Find which university this person attended"
    ],
    "key_entities": ["44th president", "United States", "university"],
    "relation_types": ["is_president_number", "attended_university"],
    "expected_hops": 2,
    "complexity": "moderate",
    "confidence": 0.95
}}

Example 3:
Question: "What is the population of the city where the author of '1984' was born?"
Analysis:
{{
    "reasoning_steps": [
        "Identify who wrote the book '1984'",
        "Find the city where this author was born",
        "Look up the population of that city"
    ],
    "key_entities": ["1984", "author", "city", "born", "population"],
    "relation_types": ["author_of", "born_in", "population_of"],
    "expected_hops": 3,
    "complexity": "complex",
    "confidence": 0.9
}}

Now analyze the given question. Output ONLY the JSON object, no other text.
"""

# 简化版本的Prompt（用于快速测试）
QUERY_ANALYSIS_PROMPT_SIMPLE = """Analyze this question: {query}

How many reasoning steps (hops) are needed? What are the key entities?

Output JSON:
{{
    "reasoning_steps": ["..."],
    "key_entities": ["..."],
    "expected_hops": <number>,
    "complexity": "simple|moderate|complex"
}}
"""


