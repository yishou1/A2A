#!/usr/bin/env python3
"""
中文多跳推理展示脚本。

示例：
    conda activate synapserag
    export SYNAPSERAG_OPENAI_API_KEY=<DeepSeek API Key>
    export OPENAI_API_KEY=<Embedding API Key>

    python demo_multihop_showcase.py \
      --llm_base_url https://api.deepseek.com \
      --llm_name deepseek-v4-pro \
      --embedding_base_url https://api.siliconflow.cn/v1 \
      --embedding_name BAAI/bge-m3
"""

from __future__ import annotations

import argparse
import ast
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import igraph as ig

from scripts.analysis.graph_pickle_to_html import (
    build_payload,
    load_relation_labels,
    render_html,
    select_vertices,
)
from src.synapserag import SynapseRAG
from src.synapserag.utils.config_utils import BaseConfig
from src.synapserag.utils.logging_utils import setup_logging
from src.synapserag.utils.misc_utils import QuerySolution, string_to_bool


logger = logging.getLogger(__name__)


DOCS = [
    "人物档案\n顾明澈是青岚研究所的创办人。",
    "研究项目记录\n青岚研究所研发了星桥系统。",
    "并购公告\n星桥系统在2024年被北辰机器人公司收购。",
    "企业信息\n北辰机器人公司的总部位于苏州。",
    "人物档案\n周若安是云杉科技的创办人。",
    "研究项目记录\n云杉科技研发了月衡平台。",
    "并购公告\n月衡平台被南湖智能收购。",
    "企业信息\n南湖智能的总部位于杭州。",
    "企业信息\n北辰食品公司的总部位于成都。",
    "校园信息\n星桥学校位于南京。",
    "投资新闻\n青岚研究所曾获得海棠基金投资。",
]


QUESTIONS = [
    {
        "question": "收购顾明澈创办机构所研发系统的公司，总部在哪里？",
        "answer": ["苏州"],
        "gold_docs": DOCS[:4],
        "chain": [
            "顾明澈 -> 青岚研究所",
            "青岚研究所 -> 星桥系统",
            "星桥系统 -> 北辰机器人公司",
            "北辰机器人公司 -> 苏州",
        ],
        "focus": "星桥系统",
    },
    {
        "question": "收购周若安创办机构所研发平台的公司，总部在哪里？",
        "answer": ["杭州"],
        "gold_docs": DOCS[4:8],
        "chain": [
            "周若安 -> 云杉科技",
            "云杉科技 -> 月衡平台",
            "月衡平台 -> 南湖智能",
            "南湖智能 -> 杭州",
        ],
        "focus": "月衡平台",
    },
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SynapseRAG 中文多跳推理展示")
    parser.add_argument("--llm_base_url", type=str, default="https://api.deepseek.com", help="OpenAI-compatible LLM base URL")
    parser.add_argument("--llm_name", type=str, default="deepseek-v4-pro", help="LLM model name")
    parser.add_argument("--embedding_base_url", type=str, default="https://api.siliconflow.cn/v1", help="OpenAI-compatible embedding base URL")
    parser.add_argument("--embedding_name", type=str, default="BAAI/bge-m3", help="Embedding model name")
    parser.add_argument("--save_dir", type=str, default="outputs/multihop_showcase_cn", help="输出目录")
    parser.add_argument("--log_dir", type=str, default=None, help="日志目录，默认 <save_dir>/logs")
    parser.add_argument("--log_level", type=str, default=os.getenv("LOG_LEVEL", "INFO"), help="日志级别")
    parser.add_argument("--force_index_from_scratch", type=str, default="true", help="是否忽略已有 graph.pickle")
    parser.add_argument("--force_openie_from_scratch", type=str, default="true", help="是否重新抽取 OpenIE")
    parser.add_argument("--force_rebuild_graph", type=str, default="true", help="是否重新构建图拓扑")
    parser.add_argument("--retrieval_top_k", type=int, default=8, help="展示检索 Top K")
    parser.add_argument("--qa_top_k", type=int, default=8, help="QA 使用的 Top K 文档")
    parser.add_argument("--linking_top_k", type=int, default=8, help="Fact/entity linking Top K")
    parser.add_argument("--skip_dpr", action="store_true", help="跳过 DPR baseline 对比")
    parser.add_argument("--skip_qa", action="store_true", help="只展示检索，不调用 QA")
    parser.add_argument("--skip_visualization", action="store_true", help="不生成 graph.html")
    parser.add_argument("--disable_fact_fallback", action="store_true", help="禁用中文 rerank 失败时的 embedding facts 回退")
    parser.add_argument("--graph_max_nodes", type=int, default=180, help="graph.html 最多展示节点数")
    return parser.parse_args()


def build_config(args: argparse.Namespace) -> BaseConfig:
    return BaseConfig(
        save_dir=args.save_dir,
        llm_base_url=args.llm_base_url,
        llm_name=args.llm_name,
        embedding_base_url=args.embedding_base_url,
        embedding_model_name=args.embedding_name,
        dataset="multihop_showcase",
        force_index_from_scratch=string_to_bool(args.force_index_from_scratch),
        force_openie_from_scratch=string_to_bool(args.force_openie_from_scratch),
        force_rebuild_graph=string_to_bool(args.force_rebuild_graph),
        rerank_dspy_file_path="src/synapserag/prompts/dspy_prompts/filter_llama3.3-70B-Instruct.json",
        retrieval_top_k=args.retrieval_top_k,
        linking_top_k=args.linking_top_k,
        qa_top_k=args.qa_top_k,
        max_qa_steps=3,
        graph_type="facts_and_sim_passage_node_unidirectional",
        embedding_batch_size=8,
        max_new_tokens=None,
        corpus_len=len(DOCS),
        openie_mode="online",
        use_agentic_ppr_reset=True,
        use_softmax_fusion=True,
        use_masked_softmax=True,
        dpr_topP_for_reset=20,
        lambda_mix=0.55,
    )


def format_doc(doc: str, limit: int = 120) -> str:
    doc = " ".join(doc.split())
    if len(doc) <= limit:
        return doc
    return doc[: limit - 1] + "..."


def print_case_header(case: Dict[str, Any], idx: int) -> None:
    print("\n" + "=" * 88)
    print(f"问题 {idx}: {case['question']}")
    print(f"预期答案: {', '.join(case['answer'])}")
    print("预期推理链:")
    for step in case["chain"]:
        print(f"  - {step}")
    print("=" * 88)


def print_retrieval_results(title: str, results: List[QuerySolution], top_k: int) -> None:
    print(f"\n{title}")
    for q_idx, result in enumerate(results, start=1):
        print(f"\n[{q_idx}] {result.question}")
        for rank, doc in enumerate(result.docs[:top_k], start=1):
            score = ""
            if result.doc_scores is not None and len(result.doc_scores) >= rank:
                score = f" | score={float(result.doc_scores[rank - 1]):.4f}"
            print(f"  {rank}. {format_doc(doc)}{score}")


def print_qa_results(results: List[QuerySolution], raw_responses: List[str]) -> None:
    print("\nQA 最终答案")
    for idx, result in enumerate(results, start=1):
        print(f"\n[{idx}] {result.question}")
        print(f"  预测答案: {result.answer}")
        if idx <= len(raw_responses):
            print(f"  原始输出: {format_doc(raw_responses[idx - 1], limit=220)}")


def parse_showcase_answer(raw_response: str) -> str:
    patterns = [
        r"(?:答案|Answer)\s*[:：]\s*([^\n\r]+)",
        r"(?:最终答案|Final Answer)\s*[:：]\s*([^\n\r]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, raw_response, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip().strip("。.;；")

    non_empty_lines = [line.strip() for line in raw_response.splitlines() if line.strip()]
    if not non_empty_lines:
        return ""
    return non_empty_lines[-1].strip().strip("。.;；")


def run_chinese_showcase_qa(
    rag: SynapseRAG,
    retrieval_results: List[QuerySolution],
    qa_top_k: int,
) -> Tuple[List[QuerySolution], List[str]]:
    messages_list = []
    for result in retrieval_results:
        docs_text = "\n".join(
            f"[{idx}] {doc}"
            for idx, doc in enumerate(result.docs[:qa_top_k], start=1)
        )
        messages_list.append(
            [
                {
                    "role": "system",
                    "content": (
                        "你是一个严谨的中文多跳问答助手。只能依据用户给出的文档回答，"
                        "不要使用外部知识。问题通常需要把多篇文档中的线索串起来。"
                        "最终必须输出单独一行：答案：<简短答案>。"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "请根据下列文档回答问题。\n\n"
                        f"{docs_text}\n\n"
                        f"问题：{result.question}\n\n"
                        "请按下面格式输出：\n"
                        "推理：<逐步说明证据链>\n"
                        "答案：<只写最终地点或实体名>"
                    ),
                },
            ]
        )

    qa_results = []
    raw_responses = []
    for result, messages in zip(retrieval_results, messages_list):
        raw_response, metadata, cache_hit = rag.llm_model.infer(
            messages=messages,
            max_completion_tokens=512,
        )
        answer = parse_showcase_answer(raw_response)
        result.answer = answer
        qa_results.append(result)
        raw_responses.append(raw_response)

    return qa_results, raw_responses


def calculate_showcase_qa_metrics(
    qa_results: List[QuerySolution],
    gold_answers: List[List[str]],
) -> Dict[str, float]:
    if not qa_results:
        return {}
    correct = 0
    for result, gold_list in zip(qa_results, gold_answers):
        predicted = (result.answer or "").strip()
        if any(gold.strip() in predicted or predicted in gold.strip() for gold in gold_list):
            correct += 1
    exact = round(correct / len(qa_results), 4)
    return {"ExactMatch": exact, "F1": exact}


def install_fact_rerank_fallback(rag: SynapseRAG) -> None:
    """
    DeepSeek/中文场景里，DSPy 英文 few-shot reranker 偶尔会返回空 fact。
    展示脚本中用 embedding top-k facts 作为保底，让后续 PPR 图传播仍然发生。
    """
    original_rerank_facts = rag.rerank_facts

    def rerank_facts_with_fallback(query: str, query_fact_scores):
        top_k_indices, top_k_facts, rerank_log = original_rerank_facts(query, query_fact_scores)
        if top_k_facts:
            return top_k_indices, top_k_facts, rerank_log

        if len(query_fact_scores) == 0 or len(rag.fact_node_keys) == 0:
            return top_k_indices, top_k_facts, rerank_log

        link_top_k = min(rag.global_config.linking_top_k, len(query_fact_scores))
        candidate_indices = query_fact_scores.argsort()[-link_top_k:][::-1].tolist()
        candidate_fact_ids = [rag.fact_node_keys[idx] for idx in candidate_indices]
        fact_rows = rag.fact_embedding_store.get_rows(candidate_fact_ids)

        fallback_facts = []
        fallback_indices = []
        for fact_idx, fact_id in zip(candidate_indices, candidate_fact_ids):
            try:
                fallback_facts.append(ast.literal_eval(fact_rows[fact_id]["content"]))
                fallback_indices.append(fact_idx)
            except (ValueError, SyntaxError, KeyError):
                logger.exception("无法解析 fallback fact: %s", fact_id)

        if fallback_facts:
            logger.info("Fact reranker returned empty; using embedding top-%d facts as fallback.", len(fallback_facts))
            return fallback_indices, fallback_facts, {
                **rerank_log,
                "fallback": "embedding_top_k_facts",
                "facts_after_rerank": fallback_facts,
            }

        return top_k_indices, top_k_facts, rerank_log

    rag.rerank_facts = rerank_facts_with_fallback


def write_graph_html(rag: SynapseRAG, args: argparse.Namespace) -> Path:
    graph_path = Path(rag.working_dir) / "graph.pickle"
    openie_path = Path(rag.openie_results_path)
    output_path = Path(rag.working_dir) / "graph.html"

    graph = ig.Graph.Read_Pickle(str(graph_path))
    relation_labels = load_relation_labels(openie_path if openie_path.exists() else None)
    focus_terms = [case["focus"] for case in QUESTIONS]
    selected = select_vertices(
        graph=graph,
        max_nodes=args.graph_max_nodes,
        focus_terms=focus_terms,
        focus_hops=3,
        include_chunks=True,
    )
    payload = build_payload(
        graph=graph,
        selected=selected,
        min_edge_weight=0.0,
        relation_labels=relation_labels,
    )

    output_path.write_text(render_html(payload, title="SynapseRAG 中文多跳推理图"), encoding="utf-8")
    return output_path


def save_summary(
    path: Path,
    graph_results: List[QuerySolution],
    qa_results: List[QuerySolution] | None,
    raw_responses: List[str] | None,
    retrieval_metrics: Dict[str, float],
    qa_metrics: Dict[str, float],
    graph_html_path: Path | None,
) -> None:
    payload = {
        "created_at": datetime.now().isoformat(),
        "docs": DOCS,
        "questions": QUESTIONS,
        "retrieval_metrics": retrieval_metrics,
        "qa_metrics": qa_metrics,
        "graph_html": str(graph_html_path) if graph_html_path else None,
        "retrieval_results": [result.to_dict() for result in graph_results],
        "qa_results": [result.to_dict() for result in qa_results] if qa_results else [],
        "qa_raw_responses": raw_responses or [],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    args = parse_args()
    os.makedirs(args.save_dir, exist_ok=True)
    log_path = setup_logging(
        log_dir=args.log_dir or os.path.join(args.save_dir, "logs"),
        run_name=f"multihop-showcase-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
        level=args.log_level,
    )

    print("SynapseRAG 中文多跳推理展示")
    print(f"日志文件: {log_path}")
    print(f"LLM: {args.llm_name} ({args.llm_base_url})")
    print(f"Embedding: {args.embedding_name} ({args.embedding_base_url or 'default OpenAI endpoint'})")
    print(f"输出目录: {args.save_dir}")

    for idx, case in enumerate(QUESTIONS, start=1):
        print_case_header(case, idx)

    config = build_config(args)
    rag = SynapseRAG(global_config=config)

    print("\n开始索引中文演示文档...")
    rag.index(DOCS)

    if not args.disable_fact_fallback:
        install_fact_rerank_fallback(rag)

    graph_html_path = None
    if not args.skip_visualization:
        graph_html_path = write_graph_html(rag, args)
        print(f"\n图结构 HTML 已生成: {graph_html_path}")

    queries = [case["question"] for case in QUESTIONS]
    gold_docs = [case["gold_docs"] for case in QUESTIONS]
    gold_answers = [case["answer"] for case in QUESTIONS]

    print("\n开始图增强多跳检索...")
    graph_retrieve_output = rag.retrieve(
        queries=queries,
        num_to_retrieve=args.retrieval_top_k,
        gold_docs=gold_docs,
    )
    graph_results, retrieval_metrics = graph_retrieve_output
    print_retrieval_results("SynapseRAG 图增强检索 Top 文档", graph_results, args.retrieval_top_k)
    print(f"\n图增强检索指标: {retrieval_metrics}")

    if not args.skip_dpr:
        print("\n开始 DPR baseline 检索...")
        dpr_retrieve_output = rag.retrieve_dpr(
            queries=queries,
            num_to_retrieve=args.retrieval_top_k,
            gold_docs=gold_docs,
        )
        dpr_results, dpr_metrics = dpr_retrieve_output
        print_retrieval_results("DPR baseline Top 文档", dpr_results, args.retrieval_top_k)
        print(f"\nDPR baseline 检索指标: {dpr_metrics}")

    qa_results = None
    raw_responses = None
    qa_metrics: Dict[str, float] = {}
    if not args.skip_qa:
        print("\n开始基于图增强检索结果进行中文 QA...")
        qa_results, raw_responses = run_chinese_showcase_qa(
            rag=rag,
            retrieval_results=graph_results,
            qa_top_k=args.qa_top_k,
        )
        qa_metrics = calculate_showcase_qa_metrics(qa_results, gold_answers)
        print_qa_results(qa_results, raw_responses)
        print(f"\nQA 指标: {qa_metrics}")

    summary_path = Path(rag.working_dir) / "showcase_summary.json"
    save_summary(
        path=summary_path,
        graph_results=graph_results,
        qa_results=qa_results,
        raw_responses=raw_responses,
        retrieval_metrics=retrieval_metrics,
        qa_metrics=qa_metrics,
        graph_html_path=graph_html_path,
    )
    print(f"\n展示摘要已保存: {summary_path}")

    if graph_html_path is not None:
        print("\n浏览器查看图结构：")
        print(f"  python -m http.server 7860 -d {graph_html_path.parent}")
        print("  然后打开 http://localhost:7860/graph.html")


if __name__ == "__main__":
    main()
