"""
节点选择Prompt模板

用于LLM选择初始节点和下一跳节点。
"""

INITIAL_NODE_SELECTION_PROMPT = """You are helping to select the best starting points for multi-hop reasoning.

Question: {query}

Reasoning Plan:
{query_plan}

Available candidate entities (with their relevance scores):
{candidates}

Please select the top {k} entities that are most likely to be good starting points for answering this question.

Consider:
1. Which entities are mentioned or implied in the FIRST reasoning step?
2. Which entities are most directly related to the question's subject?
3. Which entities can serve as bridges to reach the final answer?

For each selected entity, briefly explain WHY it's a good starting point.

Output as JSON:
{{
    "selected_entities": [
        {{
            "entity": "entity_name_1",
            "reasoning": "why this is a good starting point"
        }},
        {{
            "entity": "entity_name_2",
            "reasoning": "why this is a good starting point"
        }}
    ]
}}

Example:

Question: "Which university did the 44th president of the United States attend?"

Reasoning Plan:
- Step 1: Identify the 44th president
- Step 2: Find their university

Candidates:
- "United States" (score: 0.92)
- "president" (score: 0.88)
- "Barack Obama" (score: 0.85)
- "44th" (score: 0.75)
- "university" (score: 0.70)

Output:
{{
    "selected_entities": [
        {{
            "entity": "Barack Obama",
            "reasoning": "Barack Obama is the 44th president, which is exactly what we need to identify in step 1. Starting here allows us to directly proceed to step 2 (finding his university)."
        }},
        {{
            "entity": "44th",
            "reasoning": "This numeric identifier can help us locate entities related to the 44th president, serving as a bridge to the answer."
        }}
    ]
}}

Now select the best starting entities. Output ONLY the JSON object.
"""

NEXT_HOP_SELECTION_PROMPT = """You are navigating a knowledge graph to answer a multi-hop question.

Question: {query}

Current reasoning path (Hop {hop_number}):
{current_path}

Next hop candidates (neighboring nodes from the graph):
{candidates}

Based on the question and the current path, which candidates should we explore next?

Consider:
1. Does this candidate move us closer to answering the question?
2. Is this candidate logically connected to the current path?
3. Does this candidate align with the expected reasoning steps?

Rank the candidates and select the top {k}. For each selection, explain your reasoning.

Output as JSON:
{{
    "selected_candidates": [
        {{
            "candidate": "node_id_or_name",
            "reasoning": "why explore this node",
            "confidence": <0.0-1.0>
        }}
    ],
    "overall_reasoning": "brief explanation of the selection strategy"
}}

Example:

Question: "Which university did the 44th president attend?"

Current path (Hop 1):
Barack Obama

Candidates:
1. "Harvard Law School" (relation: attended_university)
2. "Michelle Obama" (relation: married_to)
3. "Illinois" (relation: senator_of)
4. "2008" (relation: elected_in)
5. "Columbia University" (relation: attended_undergraduate)

Output:
{{
    "selected_candidates": [
        {{
            "candidate": "Harvard Law School",
            "reasoning": "This directly answers the question about which university he attended. It's a university and there's a clear 'attended' relationship.",
            "confidence": 0.95
        }},
        {{
            "candidate": "Columbia University",
            "reasoning": "Also a university he attended (undergraduate). Relevant to the question, though the question might be asking about graduate school.",
            "confidence": 0.75
        }}
    ],
    "overall_reasoning": "Focus on university-related nodes with 'attended' relationships, as they directly address the question."
}}

Now evaluate the candidates. Output ONLY the JSON object.
"""

# 批量节点选择（用于并行处理）
BATCH_NODE_SELECTION_PROMPT = """You are evaluating multiple candidate nodes for multi-hop reasoning.

Question: {query}
Current path: {current_path}

Candidates:
{candidates}

For each candidate, assign a relevance score (0.0-1.0) indicating how likely it is to help answer the question.

Output as JSON list:
[
    {{"candidate": "node1", "score": 0.9, "reasoning": "..."}},
    {{"candidate": "node2", "score": 0.7, "reasoning": "..."}}
]
"""


