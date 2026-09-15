#!/usr/bin/env python3
"""Validate an OpenAI-compatible local embedding endpoint."""

import argparse
import os
import time

import numpy as np
from openai import OpenAI


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", default="qwen3-embedding:0.6b")
    parser.add_argument("--api-key-env", default="SYNAPSERAG_EMBEDDING_API_KEY")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument(
        "--min-repeat-cosine",
        type=float,
        default=0.999,
        help="Minimum cosine similarity for the same text across repeated requests",
    )
    parser.add_argument(
        "--max-repeat-delta",
        type=float,
        default=None,
        help="Optional strict maximum element-wise delta for repeated requests",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    api_key = os.getenv(args.api_key_env, "sk-local")
    client = OpenAI(base_url=args.base_url, api_key=api_key, timeout=args.timeout)
    listed_models = [item.id for item in client.models.list().data]
    if listed_models and args.model not in listed_models:
        raise RuntimeError(
            f"Model {args.model!r} is not listed by /v1/models: {listed_models}")

    base_samples = ["顾明澈创办了青岚研究所。", "The company is based in Suzhou.", " "]
    samples = [base_samples[index % len(base_samples)] for index in range(args.batch_size)]

    single = client.embeddings.create(model=args.model, input=[base_samples[0]])
    single_vector = np.asarray(single.data[0].embedding, dtype=np.float32)
    if single_vector.ndim != 1 or not single_vector.size:
        raise RuntimeError(f"Unexpected single embedding shape: {single_vector.shape}")

    started_at = time.perf_counter()
    first = client.embeddings.create(model=args.model, input=samples)
    first_latency = time.perf_counter() - started_at
    started_at = time.perf_counter()
    second = client.embeddings.create(model=args.model, input=samples)
    second_latency = time.perf_counter() - started_at
    first_vectors = np.asarray([item.embedding for item in first.data], dtype=np.float32)
    second_vectors = np.asarray([item.embedding for item in second.data], dtype=np.float32)

    if first_vectors.ndim != 2 or first_vectors.shape[0] != len(samples):
        raise RuntimeError(f"Unexpected embedding shape: {first_vectors.shape}")
    if first_vectors.shape != second_vectors.shape:
        raise RuntimeError(
            f"Embedding shape changed between requests: {first_vectors.shape} -> {second_vectors.shape}")
    if not np.isfinite(first_vectors).all() or not np.isfinite(second_vectors).all():
        raise RuntimeError("Embedding response contains NaN or infinite values")
    first_norms = np.linalg.norm(first_vectors, axis=1, keepdims=True)
    second_norms = np.linalg.norm(second_vectors, axis=1, keepdims=True)
    if np.any(first_norms == 0) or np.any(second_norms == 0):
        raise RuntimeError("Embedding response contains a zero vector")
    if first_vectors.shape[1] != single_vector.size:
        raise RuntimeError(
            f"Single/batch dimension mismatch: {single_vector.size} != {first_vectors.shape[1]}")

    normalized = first_vectors / first_norms
    if not np.isfinite(normalized).all():
        raise RuntimeError("Normalized embeddings contain NaN or infinite values")

    second_normalized = second_vectors / second_norms
    repeat_cosines = np.sum(normalized * second_normalized, axis=1)
    min_repeat_cosine = float(np.min(repeat_cosines))
    if min_repeat_cosine < args.min_repeat_cosine:
        raise RuntimeError(
            "Repeated embeddings are semantically unstable: "
            f"cosine {min_repeat_cosine} < {args.min_repeat_cosine}")

    max_delta = float(np.max(np.abs(first_vectors - second_vectors)))
    if args.max_repeat_delta is not None and max_delta > args.max_repeat_delta:
        raise RuntimeError(
            f"Repeated embeddings exceed strict delta: {max_delta} > "
            f"{args.max_repeat_delta}")
    print(f"model={args.model}")
    print(f"dimension={first_vectors.shape[1]}")
    print(f"batch_size={first_vectors.shape[0]}")
    print(f"first_batch_latency_seconds={first_latency:.4f}")
    print(f"second_batch_latency_seconds={second_latency:.4f}")
    print(f"repeat_min_cosine={min_repeat_cosine:.8f}")
    print(f"repeat_max_abs_delta={max_delta:.8f}")
    print("status=ok")


if __name__ == "__main__":
    main()
