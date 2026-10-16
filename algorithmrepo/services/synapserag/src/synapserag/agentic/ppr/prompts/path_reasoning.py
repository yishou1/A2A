"""
路径评估Prompt模板

用于LLM评估推理路径的质量。
"""

PATH_EVALUATION_PROMPT = """You are evaluating a reasoning path for answering a multi-hop question.

Question: {query}

Reasoning Path:
{path}

Documents reached by this path:
{documents}

Please evaluate this path on the following dimensions:

1. **Completeness** (0.0-1.0): Does this path form a complete reasoning chain from the question to a potential answer?
   - 1.0: Perfect chain, all steps covered
   - 0.5: Partial chain, missing some steps
   - 0.0: Incomplete or broken chain

2. **Coherence** (0.0-1.0): Are the relationships between nodes in the path logically sound and relevant?
   - 1.0: All relationships make sense and are relevant
   - 0.5: Some relationships are weak or questionable
   - 0.0: Relationships don't make sense

3. **Relevance** (0.0-1.0): Does the final node/document directly answer or strongly relate to the question?
   - 1.0: Directly answers the question
   - 0.5: Related but not a direct answer
   - 0.0: Unrelated to the question

Output as JSON:
{{
    "completeness": <0.0-1.0>,
    "coherence": <0.0-1.0>,
    "relevance": <0.0-1.0>,
    "overall_score": <weighted average>,
    "explanation": "Brief explanation of your evaluation"
}}

Example:

Question: "Which university did the 44th president attend?"

Path: "44th president" --[is]--> "Barack Obama" --[attended]--> "Harvard Law School"

Documents: ["Barack Obama attended Harvard Law School...", "Obama graduated from Harvard..."]

Output:
{{
    "completeness": 0.95,
    "coherence": 1.0,
    "relevance": 1.0,
    "overall_score": 0.98,
    "explanation": "This path forms a complete and logical chain. It identifies the 44th president as Barack Obama and correctly traces his attendance at Harvard Law School. The documents confirm this information. Minor deduction in completeness because there's no explicit '44th' relation in the graph."
}}

Now evaluate the given path. Output ONLY the JSON object.
"""

# 批量路径评估（用于并行处理）
BATCH_PATH_EVALUATION_PROMPT = """Evaluate multiple reasoning paths for the question: {query}

Paths:
{paths}

For each path, provide scores for completeness, coherence, and relevance.

Output as JSON list:
[
    {{
        "path_id": 0,
        "completeness": <score>,
        "coherence": <score>,
        "relevance": <score>,
        "overall_score": <score>
    }},
    ...
]
"""

# 路径比较Prompt（选择最佳路径）
PATH_COMPARISON_PROMPT = """Compare these reasoning paths and select the best one for answering the question.

Question: {query}

Paths:
{paths}

Which path is most likely to lead to the correct answer? Consider:
1. Completeness of the reasoning chain
2. Logical coherence
3. Relevance to the question

Output as JSON:
{{
    "best_path_id": <id>,
    "reasoning": "why this path is best",
    "confidence": <0.0-1.0>
}}
"""


