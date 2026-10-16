#!/usr/bin/env python3
"""Copy a legacy SynapseRAG index into the role-independent index layout."""

import argparse
import hashlib
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import igraph as ig


STORES = {
    "chunk": "chunk_embeddings/vdb_chunk.parquet",
    "entity": "entity_embeddings/vdb_entity.parquet",
    "fact": "fact_embeddings/vdb_fact.parquet",
}
DEFAULT_QUERY_TO_FACT_INSTRUCTION = (
    "Given a question, retrieve relevant triplet facts that matches this question."
)
DEFAULT_QUERY_TO_PASSAGE_INSTRUCTION = (
    "Given a question, retrieve relevant documents that best answer the question."
)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--save-dir", type=Path, required=True)
    parser.add_argument("--index-id", required=True)
    parser.add_argument("--embedding-model", required=True)
    parser.add_argument("--normalized", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--instruction-mode", choices=["none", "prefix", "provider"], default="prefix")
    parser.add_argument("--document-instruction", default="")
    parser.add_argument(
        "--query-to-fact-instruction",
        default=DEFAULT_QUERY_TO_FACT_INSTRUCTION,
    )
    parser.add_argument(
        "--query-to-passage-instruction",
        default=DEFAULT_QUERY_TO_PASSAGE_INSTRUCTION,
    )
    parser.add_argument("--openie-source-model", default="legacy-unknown")
    parser.add_argument("--openie-prompt-version", default="legacy-unknown")
    return parser.parse_args()


def stable_hash(value):
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def main():
    args = parse_args()
    if not args.source.is_dir():
        raise FileNotFoundError(args.source)

    dimensions = set()
    document_ids = []
    for namespace, relative_path in STORES.items():
        path = args.source / relative_path
        if not path.is_file():
            raise FileNotFoundError(path)
        frame = pd.read_parquet(path)
        row_dimensions = {int(np.asarray(value).size) for value in frame["embedding"]}
        if len(row_dimensions) > 1:
            raise ValueError(f"{path} contains mixed vector dimensions: {row_dimensions}")
        dimensions.update(row_dimensions)
        metadata_path = path.with_name(f"vdb_{namespace}.metadata.json")
        if metadata_path.is_file():
            with open(metadata_path, "r", encoding="utf-8") as file:
                metadata = json.load(file)
            stored_model = metadata.get("embedding_model")
            if stored_model and stored_model != args.embedding_model:
                raise ValueError(
                    f"{metadata_path} records {stored_model}, not {args.embedding_model}")
            stored_normalized = metadata.get("embedding_normalized")
            if stored_normalized is not None and stored_normalized != args.normalized:
                raise ValueError(
                    f"{metadata_path} normalization does not match --normalized")
            stored_mode = metadata.get("embedding_instruction_mode")
            if stored_mode and stored_mode != args.instruction_mode:
                raise ValueError(
                    f"{metadata_path} instruction mode does not match --instruction-mode")
        if namespace == "chunk":
            document_ids = sorted(frame["hash_id"].tolist())

    if len(dimensions) != 1:
        raise ValueError(f"Stores do not share one embedding dimension: {dimensions}")

    graph_path = args.source / "graph.pickle"
    if not graph_path.is_file():
        raise FileNotFoundError(graph_path)
    graph = ig.Graph.Read_Pickle(graph_path)
    if "name" not in graph.vs.attributes():
        raise ValueError(f"Graph has no named vertices: {graph_path}")
    missing_passages = set(document_ids) - set(graph.vs["name"])
    if missing_passages:
        raise ValueError(
            f"Graph is missing {len(missing_passages)} chunk nodes; rebuild instead of migrating")

    embedding_slug = args.embedding_model.replace("/", "_")
    destination = args.save_dir / "indexes" / args.index_id / embedding_slug
    if destination.exists():
        raise FileExistsError(f"Destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(args.source, destination)

    now = datetime.now().isoformat()
    manifest = {
        "schema_version": 1,
        "index_id": args.index_id,
        "embedding_model": args.embedding_model,
        "embedding_dimension": next(iter(dimensions)),
        "embedding_normalized": args.normalized,
        "embedding_instruction_mode": args.instruction_mode,
        "document_instruction_hash": stable_hash(args.document_instruction),
        "query_to_fact_instruction_hash": stable_hash(args.query_to_fact_instruction),
        "query_to_passage_instruction_hash": stable_hash(args.query_to_passage_instruction),
        "openie_source_model": args.openie_source_model,
        "openie_prompt_version": args.openie_prompt_version,
        "document_count": len(document_ids),
        "document_ids_hash": stable_hash(document_ids),
        "created_at": now,
        "updated_at": now,
        "migration_source": str(args.source.resolve()),
        "migration_parameters_reviewed": True,
    }
    with open(destination / "index_manifest.json", "w", encoding="utf-8") as file:
        json.dump(manifest, file, ensure_ascii=False, indent=2)
    print(destination)


if __name__ == "__main__":
    main()
