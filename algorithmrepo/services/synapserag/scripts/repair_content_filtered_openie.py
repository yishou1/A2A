#!/usr/bin/env python3
"""Repair one content-filtered OpenIE chunk while preserving its original passage."""

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.ingest_documents import make_config
from src.synapserag import SynapseRAG


def find_chunk(path: Path, chunk_index: int) -> dict:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            chunk = json.loads(line)
            if chunk["chunk_index"] == chunk_index:
                return chunk
    raise ValueError(f"chunk index {chunk_index} was not found in {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("chunks_jsonl", type=Path)
    parser.add_argument("--chunk-index", type=int, required=True)
    parser.add_argument(
        "--sanitize-before",
        required=True,
        help="Keep text before the first occurrence of this marker for OpenIE only.",
    )
    parser.add_argument("--save-dir", required=True)
    parser.add_argument("--index-id", required=True)
    args = parser.parse_args()

    chunk = find_chunk(args.chunks_jsonl, args.chunk_index)
    original_text = chunk["text"]
    if args.sanitize_before not in original_text:
        raise ValueError(f"sanitize marker not found: {args.sanitize_before!r}")
    extraction_text = original_text.split(args.sanitize_before, 1)[0].rstrip()
    if not extraction_text:
        raise ValueError("sanitization removed the entire chunk")

    config_args = argparse.Namespace(
        save_dir=args.save_dir,
        index_id=args.index_id,
        stage="openie",
    )
    rag = SynapseRAG(global_config=make_config(config_args))
    original_rows = rag._make_chunk_rows([original_text])
    all_openie_info, missing_keys = rag.load_existing_openie(original_rows.keys())
    chunk_key = next(iter(original_rows))
    if chunk_key not in missing_keys:
        raise RuntimeError(f"chunk {args.chunk_index} is already present in the OpenIE cache")

    extraction_rows = {
        chunk_key: {
            "hash_id": chunk_key,
            "content": extraction_text,
        }
    }
    openie = rag._ensure_openie_runtime()
    ner_results, triple_results = openie.batch_openie(extraction_rows)

    # Merge with the original passage row. Only the text sent to the provider is
    # shortened; embedding, retrieval, citation, and persisted passage stay exact.
    rag._merge_cache_and_raise_openie_failures(
        all_openie_info,
        original_rows,
        ner_results,
        triple_results,
    )

    report = {
        "stage": "repair-content-filtered-openie",
        "chunk_index": args.chunk_index,
        "chunk_key": chunk_key,
        "page_start": chunk["page_start"],
        "page_end": chunk["page_end"],
        "original_characters": len(original_text),
        "openie_characters": len(extraction_text),
        "entities": len(ner_results[chunk_key].unique_entities),
        "triples": len(triple_results[chunk_key].triples),
        "openie_results_path": rag.openie_results_path,
        "cached_documents": len(all_openie_info),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
