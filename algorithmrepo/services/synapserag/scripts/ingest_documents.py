#!/usr/bin/env python3
"""Parse files and optionally run the existing SynapseRAG indexing pipeline."""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Iterable, List

# Allow direct execution as `python scripts/ingest_documents.py` from any
# working directory without requiring an editable package install.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.synapserag import SynapseRAG
from src.synapserag.document_ingestion import chunk_document, parse_document
from src.synapserag.document_ingestion.io import save_document_artifacts
from src.synapserag.utils.config_utils import BaseConfig, LLMEndpointConfig


SUPPORTED = {".txt", ".text", ".md", ".markdown", ".pdf", ".docx"}


def collect_paths(inputs: Iterable[str]) -> List[Path]:
    paths: List[Path] = []
    for item in inputs:
        path = Path(item)
        if path.is_dir():
            paths.extend(sorted(p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED))
        elif path.is_file():
            paths.append(path)
        else:
            raise FileNotFoundError(item)
    unique = {str(path.resolve()): path.resolve() for path in paths}
    return list(unique.values())


def make_config(args: argparse.Namespace) -> BaseConfig:
    provider = os.getenv("SYNAPSERAG_OPENIE_PROVIDER", os.getenv("LLM_PROVIDER", "openai_compatible"))
    openie_base = os.getenv("SYNAPSERAG_OPENIE_BASE_URL") or os.getenv("TOOL_LLM_URL") or "https://dashscope.aliyuncs.com/compatible-mode/v1"
    openie_model = os.getenv("SYNAPSERAG_OPENIE_MODEL") or os.getenv("TOOL_LLM_NAME") or "qwen-plus"
    azure = provider == "azure"
    openie = LLMEndpointConfig(
        model_name=openie_model,
        provider=provider,
        base_url=None if azure else openie_base,
        azure_endpoint=openie_base if azure else None,
        api_version=os.getenv("SYNAPSERAG_OPENIE_API_VERSION") or os.getenv("AZURE_OPENAI_API_VERSION") if azure else None,
        api_key_env=os.getenv("SYNAPSERAG_OPENIE_API_KEY_ENV", "API_KEY" if azure else "SYNAPSERAG_OPENIE_API_KEY"),
        timeout_seconds=int(os.getenv("LLM_TIMEOUT_SECONDS", "120")),
        temperature=0.0,
        max_tokens=2048,
    )
    qa = LLMEndpointConfig(
        model_name=os.getenv("SYNAPSERAG_QA_MODEL", "qwen3:1.7b"),
        base_url=os.getenv("SYNAPSERAG_QA_BASE_URL", "http://127.0.0.1:11434/v1"),
        api_key_env="SYNAPSERAG_QA_API_KEY",
        temperature=0.1,
        max_tokens=1024,
        extra_body={"reasoning_effort": "none"},
    )
    return BaseConfig(
        save_dir=args.save_dir,
        index_id=args.index_id,
        runtime_stage=args.stage,
        openie_prompt_version="ner-triple-cn-v2",
        openie_llm=openie,
        qa_llm=qa,
        embedding_model_name=os.getenv("SYNAPSERAG_EMBEDDING_MODEL", "qwen3-embedding:0.6b"),
        embedding_base_url=os.getenv("SYNAPSERAG_EMBEDDING_BASE_URL", "http://127.0.0.1:11434/v1"),
        fact_rerank_mode="hybrid",
        fact_candidate_top_k=20,
        fact_rerank_top_k=5,
        linking_top_k=5,
        retrieval_top_k=8,
        retrieval_fusion_mode="hybrid",
        qa_top_k=5,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="*", help="Files or directories to ingest")
    parser.add_argument("--stage", choices=["parse", "openie", "build-index", "query", "all"], default="parse")
    parser.add_argument("--query", default="", help="Question for query/all stages")
    parser.add_argument("--save-dir", default="outputs/document_ingestion")
    parser.add_argument("--index-id", default="default")
    parser.add_argument("--max-tokens", type=int, default=700)
    parser.add_argument("--overlap-tokens", type=int, default=100)
    parser.add_argument("--force-openie", action="store_true")
    args = parser.parse_args()

    if args.stage == "query":
        if not args.query:
            parser.error("--query is required for --stage query")
        rag = SynapseRAG(global_config=make_config(args))
        print(json.dumps({"stage": "query", "index": rag.load_index(), "result": rag.rag_qa([args.query])[0][0].answer}, ensure_ascii=False, indent=2))
        return
    if not args.inputs:
        parser.error("at least one input file or directory is required")

    paths = collect_paths(args.inputs)
    all_chunks = []
    reports = []
    artifact_root = Path(args.save_dir) / "documents"
    for path in paths:
        document = parse_document(path)
        chunks = chunk_document(document, args.max_tokens, args.overlap_tokens)
        reports.append(save_document_artifacts(document, chunks, artifact_root / document.document_id))
        all_chunks.extend(chunks)

    source_manifest = Path(args.save_dir) / "source_manifest.json"
    source_manifest.parent.mkdir(parents=True, exist_ok=True)
    source_manifest.write_text(json.dumps({"documents": reports, "chunk_count": len(all_chunks)}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"stage": "parse", "documents": reports, "chunk_count": len(all_chunks)}, ensure_ascii=False, indent=2))
    if args.stage == "parse":
        return

    texts = [chunk.text for chunk in all_chunks]
    rag = SynapseRAG(global_config=make_config(args))
    if args.stage in {"openie", "all"}:
        print(rag.extract_openie(texts, force=args.force_openie))
    if args.stage in {"build-index", "all"}:
        print(rag.build_index(texts, allow_openie_calls=args.stage == "all"))
    if args.stage == "query" or (args.stage == "all" and args.query):
        if not args.query:
            parser.error("--query is required for --stage all when querying")
        rag.load_index()
        print(rag.rag_qa([args.query])[0][0].answer)


if __name__ == "__main__":
    main()
