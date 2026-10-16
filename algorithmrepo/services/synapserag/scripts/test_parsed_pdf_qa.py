#!/usr/bin/env python3
"""Build a focused SynapseRAG index from parsed PDF chunks and test one question."""

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.synapserag import SynapseRAG
from src.synapserag.utils.config_utils import BaseConfig, LLMEndpointConfig


def load_chunks(path: Path, selected_indices: set[int]) -> list[dict]:
    chunks = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            chunk = json.loads(line)
            if chunk["chunk_index"] in selected_indices:
                chunks.append(chunk)
    chunks.sort(key=lambda item: item["chunk_index"])
    missing = selected_indices.difference(item["chunk_index"] for item in chunks)
    if missing:
        raise ValueError(f"chunk indices not found: {sorted(missing)}")
    return chunks


def make_config(args: argparse.Namespace) -> BaseConfig:
    local_llm = LLMEndpointConfig(
        model_name=args.llm_model,
        base_url=args.llm_base_url,
        api_key_env="SYNAPSERAG_LOCAL_API_KEY",
        timeout_seconds=300,
        max_retries=1,
        temperature=0.0,
        max_tokens=2048,
        extra_body={"reasoning_effort": "none"},
    )
    return BaseConfig(
        save_dir=args.save_dir,
        index_id=args.index_id,
        runtime_stage="all",
        openie_prompt_version="ner-triple-en-v1",
        openie_llm=local_llm,
        qa_llm=local_llm,
        rerank_llm=local_llm,
        embedding_model_name=args.embedding_model,
        embedding_base_url=args.embedding_base_url,
        fact_rerank_mode="hybrid",
        fact_candidate_top_k=20,
        fact_rerank_top_k=5,
        linking_top_k=5,
        retrieval_top_k=8,
        qa_top_k=4,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("chunks_jsonl", type=Path)
    parser.add_argument("--chunk-indices", default="74,75,76,77")
    parser.add_argument(
        "--question",
        default=(
            "According to the handbook, how are hostile intent, observed indirect "
            "fire, and unobserved indirect fire defined?"
        ),
    )
    parser.add_argument("--save-dir", default="outputs/roe_handbook_focused_qa")
    parser.add_argument("--index-id", default="roe-handbook-focused-v1")
    parser.add_argument("--llm-model", default="qwen3:1.7b")
    parser.add_argument("--llm-base-url", default="http://127.0.0.1:11434/v1")
    parser.add_argument("--embedding-model", default="qwen3-embedding:0.6b")
    parser.add_argument("--embedding-base-url", default="http://127.0.0.1:11434/v1")
    args = parser.parse_args()

    selected_indices = {int(value.strip()) for value in args.chunk_indices.split(",")}
    chunks = load_chunks(args.chunks_jsonl, selected_indices)
    texts = [chunk["text"] for chunk in chunks]
    metadata_by_text = {chunk["text"]: chunk for chunk in chunks}

    rag = SynapseRAG(global_config=make_config(args))
    openie_report = rag.extract_openie(texts)
    build_report = rag.build_index(texts)
    rag.load_index()
    solution = rag.rag_qa([args.question])[0][0]

    citations = []
    for rank, text in enumerate(solution.docs, start=1):
        source = metadata_by_text.get(text, {})
        citations.append(
            {
                "rank": rank,
                "chunk_index": source.get("chunk_index"),
                "page_start": source.get("page_start"),
                "page_end": source.get("page_end"),
                "score": (
                    float(solution.doc_scores[rank - 1])
                    if solution.doc_scores is not None and rank <= len(solution.doc_scores)
                    else None
                ),
                "text_preview": text[:240].replace("\n", " "),
            }
        )

    report = {
        "source": chunks[0]["source_filename"],
        "selected_chunks": sorted(selected_indices),
        "selected_pages": sorted(
            {page for chunk in chunks for page in range(chunk["page_start"], chunk["page_end"] + 1)}
        ),
        "openie": openie_report,
        "build": build_report,
        "graph": rag.get_graph_info(),
        "question": args.question,
        "answer": solution.answer,
        "citations": citations,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
