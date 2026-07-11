"""Benchmark a deployed ST-GNN bundle with a 200-node/2000-edge graph."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np

from app.model_runtime.torchscript_st_gnn import TorchScriptBundleRunner


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark ST-GNN TorchScript inference.")
    parser.add_argument("--model-dir", required=True, type=Path)
    parser.add_argument("--iterations", type=int, default=50)
    parser.add_argument("--max-p95-ms", type=float, default=200.0)
    args = parser.parse_args()
    runner = TorchScriptBundleRunner(args.model_dir)
    if not runner.loaded:
        raise RuntimeError(runner.load_error)
    manifest = runner.manifest
    nodes = 200
    edges = 2_000
    history = np.zeros(
        (nodes, int(manifest["history_points"]), len(manifest["node_feature_schema"])),
        dtype=np.float32,
    )
    edge_index = np.vstack(
        [
            np.arange(edges, dtype=np.int64) % nodes,
            (np.arange(edges, dtype=np.int64) * 7 + 1) % nodes,
        ]
    )
    edge_features = np.zeros(
        (edges, len(manifest["edge_feature_schema"])),
        dtype=np.float32,
    )
    baseline = np.zeros(
        (nodes, len(manifest["prediction_horizons_s"]), 2),
        dtype=np.float32,
    )
    for _ in range(5):
        runner.infer(history, edge_index, edge_features, baseline)
    latencies = []
    for _ in range(args.iterations):
        started = time.perf_counter()
        runner.infer(history, edge_index, edge_features, baseline)
        latencies.append((time.perf_counter() - started) * 1000.0)
    p50 = float(np.percentile(latencies, 50))
    p95 = float(np.percentile(latencies, 95))
    report = {
        "model_version": manifest["model_version"],
        "nodes": nodes,
        "edges": edges,
        "iterations": args.iterations,
        "p50_ms": round(p50, 3),
        "p95_ms": round(p95, 3),
        "max_p95_ms": args.max_p95_ms,
        "passed": p95 <= args.max_p95_ms,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
