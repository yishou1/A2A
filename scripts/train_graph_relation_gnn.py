#!/usr/bin/env python3
"""Train the M20 graph relation GNN on deterministic formation graphs."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sys
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CHECKPOINT = ROOT / "models" / "checkpoints" / "graph_relation_gnn_s.safetensors"
DATA_DIR = ROOT / "data" / "graph_relation_gnn"
TRAIN_SEED = 20260831
HOLDOUT_SEED = 20260901
MODEL_SEED = 20260902
MAX_NODES = 10
NODE_FEATURES = 7
EDGE_FEATURES = 6


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _angle_difference(left: float, right: float) -> float:
    return abs((left - right + math.pi) % (2.0 * math.pi) - math.pi)


def generate_dataset(count: int, seed: int) -> tuple[np.ndarray, ...]:
    rng = np.random.default_rng(seed)
    nodes = np.zeros((count, MAX_NODES, NODE_FEATURES), dtype=np.float32)
    edges = np.zeros((count, MAX_NODES, MAX_NODES, EDGE_FEATURES), dtype=np.float32)
    masks = np.zeros((count, MAX_NODES), dtype=np.bool_)
    labels = np.zeros((count, MAX_NODES, MAX_NODES), dtype=np.float32)

    for graph_index in range(count):
        node_count = int(rng.integers(5, MAX_NODES + 1))
        group_count = int(rng.integers(1, min(4, node_count // 2 + 1)))
        assignments = np.full(node_count, -1, dtype=np.int64)
        order = rng.permutation(node_count)
        cursor = 0
        records: list[dict] = [{} for _ in range(node_count)]

        centers: list[tuple[float, float, float, float, int]] = []
        for group_id in range(group_count):
            size = 2 if group_id == group_count - 1 else int(rng.integers(2, 4))
            size = min(size, node_count - cursor)
            if size < 2:
                break
            center_x = float(rng.uniform(-0.65, 0.65))
            center_y = float(rng.uniform(-0.65, 0.65))
            speed = float(rng.uniform(0.18, 0.90))
            heading = float(rng.uniform(-math.pi, math.pi))
            object_type = int(rng.integers(0, 3))
            centers.append((center_x, center_y, speed, heading, object_type))
            for node_index in order[cursor : cursor + size]:
                assignments[node_index] = group_id
                records[node_index] = {
                    "x": center_x + float(rng.normal(0.0, 0.055)),
                    "y": center_y + float(rng.normal(0.0, 0.055)),
                    "speed": np.clip(speed + rng.normal(0.0, 0.025), 0.0, 1.0),
                    "heading": heading + float(rng.normal(0.0, 0.09)),
                    "type": object_type,
                    "altitude": float(rng.uniform(0.08, 0.95)),
                    "confidence": float(rng.uniform(0.72, 1.0)),
                }
            cursor += size
            if cursor >= node_count:
                break

        for node_index in range(node_count):
            if records[node_index]:
                continue
            # Hard negatives are sometimes spatially close to a formation but differ
            # in motion or object type, which a distance-only rule cannot resolve.
            if centers and rng.random() < 0.55:
                cx, cy, speed, heading, object_type = centers[int(rng.integers(0, len(centers)))]
                x = cx + float(rng.normal(0.0, 0.075))
                y = cy + float(rng.normal(0.0, 0.075))
                speed = float(np.clip(speed + rng.choice([-1.0, 1.0]) * rng.uniform(0.16, 0.34), 0.0, 1.0))
                heading = heading + float(rng.choice([-1.0, 1.0]) * rng.uniform(0.42, 1.0))
                object_type = (object_type + int(rng.integers(1, 3))) % 3
            else:
                x = float(rng.uniform(-0.9, 0.9))
                y = float(rng.uniform(-0.9, 0.9))
                speed = float(rng.uniform(0.05, 1.0))
                heading = float(rng.uniform(-math.pi, math.pi))
                object_type = int(rng.integers(0, 3))
            records[node_index] = {
                "x": x,
                "y": y,
                "speed": speed,
                "heading": heading,
                "type": object_type,
                "altitude": float(rng.uniform(0.05, 1.0)),
                "confidence": float(rng.uniform(0.6, 1.0)),
            }

        masks[graph_index, :node_count] = True
        for index, record in enumerate(records):
            type_code = float(record["type"]) / 2.0
            nodes[graph_index, index] = [
                record["x"],
                record["y"],
                record["speed"],
                math.sin(record["heading"]),
                math.cos(record["heading"]),
                record["altitude"],
                0.8 * record["confidence"] + 0.2 * type_code,
            ]
        for left in range(node_count):
            for right in range(node_count):
                dx = float(records[right]["x"] - records[left]["x"])
                dy = float(records[right]["y"] - records[left]["y"])
                distance = math.sqrt(dx * dx + dy * dy)
                heading_delta = _angle_difference(
                    float(records[left]["heading"]), float(records[right]["heading"])
                )
                speed_delta = abs(float(records[left]["speed"] - records[right]["speed"]))
                same_type = float(records[left]["type"] == records[right]["type"])
                edges[graph_index, left, right] = [
                    dx,
                    dy,
                    distance,
                    math.cos(heading_delta),
                    speed_delta,
                    same_type,
                ]
                labels[graph_index, left, right] = float(
                    left != right
                    and assignments[left] >= 0
                    and assignments[left] == assignments[right]
                )
    return nodes, edges, masks, labels


def _pair_values(values: np.ndarray, masks: np.ndarray) -> np.ndarray:
    count = values.shape[1]
    upper = np.triu(np.ones((count, count), dtype=np.bool_), k=1)
    valid = masks[:, :, None] & masks[:, None, :] & upper[None, :, :]
    return values[valid]


def classification_metrics(
    probabilities: np.ndarray, labels: np.ndarray, masks: np.ndarray
) -> dict[str, float]:
    from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score, roc_auc_score

    scores = _pair_values(probabilities, masks)
    targets = _pair_values(labels, masks).astype(np.int64)
    predicted = (scores >= 0.5).astype(np.int64)
    return {
        "accuracy": round(float(accuracy_score(targets, predicted)), 6),
        "precision": round(float(precision_score(targets, predicted, zero_division=0)), 6),
        "recall": round(float(recall_score(targets, predicted, zero_division=0)), 6),
        "f1": round(float(f1_score(targets, predicted, zero_division=0)), 6),
        "roc_auc": round(float(roc_auc_score(targets, scores)), 6),
    }


def heuristic_probabilities(edges: np.ndarray) -> np.ndarray:
    distance = edges[..., 2]
    heading_cos = edges[..., 3]
    speed_delta = edges[..., 4]
    same_type = edges[..., 5]
    predicted = (
        (distance <= 0.16)
        & (heading_cos >= math.cos(math.radians(25.0)))
        & (speed_delta <= 0.12)
        & (same_type > 0.5)
    )
    return predicted.astype(np.float32)


def evaluate(model, nodes, edges, masks, labels) -> dict[str, dict[str, float]]:
    import torch

    with torch.inference_mode():
        logits = model(
            torch.from_numpy(nodes), torch.from_numpy(edges), torch.from_numpy(masks)
        )
        probabilities = torch.sigmoid(logits).cpu().numpy()
    return {
        "trained_gnn": classification_metrics(probabilities, labels, masks),
        "distance_motion_rule_baseline": classification_metrics(
            heuristic_probabilities(edges), labels, masks
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-count", type=int, default=2400)
    parser.add_argument("--holdout-count", type=int, default=600)
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--learning-rate", type=float, default=0.0015)
    parser.add_argument("--save", type=Path, default=CHECKPOINT)
    args = parser.parse_args()

    import sklearn
    import torch
    import torch.nn.functional as functional
    from safetensors.torch import save_file

    from agent.inference.models.graph_relation_gnn import DenseGraphRelationGNN

    random.seed(MODEL_SEED)
    np.random.seed(MODEL_SEED)
    torch.manual_seed(MODEL_SEED)
    torch.use_deterministic_algorithms(True)
    torch.set_num_threads(1)

    train_nodes, train_edges, train_masks, train_labels = generate_dataset(
        args.train_count, TRAIN_SEED
    )
    holdout_nodes, holdout_edges, holdout_masks, holdout_labels = generate_dataset(
        args.holdout_count, HOLDOUT_SEED
    )
    model = DenseGraphRelationGNN()
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=args.learning_rate, weight_decay=1e-4
    )
    positive_count = float(_pair_values(train_labels, train_masks).sum())
    pair_count = float(len(_pair_values(train_labels, train_masks)))
    positive_weight = torch.tensor((pair_count - positive_count) / max(positive_count, 1.0))
    generator = torch.Generator().manual_seed(MODEL_SEED + 1)
    upper = torch.triu(torch.ones(MAX_NODES, MAX_NODES, dtype=torch.bool), diagonal=1)

    tensors = tuple(
        torch.from_numpy(value)
        for value in (train_nodes, train_edges, train_masks, train_labels)
    )
    for epoch in range(1, args.epochs + 1):
        permutation = torch.randperm(args.train_count, generator=generator)
        losses = []
        for start in range(0, args.train_count, args.batch_size):
            indices = permutation[start : start + args.batch_size]
            node_batch, edge_batch, mask_batch, label_batch = (
                value[indices] for value in tensors
            )
            logits = model(node_batch, edge_batch, mask_batch)
            valid = mask_batch[:, :, None] & mask_batch[:, None, :] & upper[None, :, :]
            loss = functional.binary_cross_entropy_with_logits(
                logits[valid], label_batch[valid], pos_weight=positive_weight
            )
            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            losses.append(float(loss.item()))
        if epoch == 1 or epoch % 20 == 0 or epoch == args.epochs:
            print(f"epoch={epoch:03d} loss={np.mean(losses):.6f}")

    model.eval()
    evaluation = evaluate(
        model, holdout_nodes, holdout_edges, holdout_masks, holdout_labels
    )
    trained = evaluation["trained_gnn"]
    baseline = evaluation["distance_motion_rule_baseline"]
    if trained["f1"] <= baseline["f1"]:
        raise RuntimeError(f"trained GNN did not beat rule baseline: {evaluation}")
    if trained["roc_auc"] < 0.95:
        raise RuntimeError(f"trained GNN holdout ROC AUC below 0.95: {evaluation}")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    artifacts = {
        "holdout_nodes": DATA_DIR / "reference_holdout_nodes.npy",
        "holdout_edges": DATA_DIR / "reference_holdout_edges.npy",
        "holdout_masks": DATA_DIR / "reference_holdout_masks.npy",
        "holdout_labels": DATA_DIR / "reference_holdout_labels.npy",
    }
    for name, path in artifacts.items():
        np.save(path, locals()[name], allow_pickle=False)

    args.save.parent.mkdir(parents=True, exist_ok=True)
    state = {
        key: value.detach().cpu().contiguous()
        for key, value in model.state_dict().items()
    }
    save_file(state, str(args.save))
    script_path = Path(__file__).resolve()
    metadata = {
        "model_id": "graph_relation_reasoner",
        "model_version": "1.0.0",
        "model_family": "dense_message_passing_graph_neural_network",
        "compute_profile": "small",
        "artifact_path": args.save.relative_to(ROOT).as_posix(),
        "artifact_sha256": sha256(args.save),
        "training_script": script_path.relative_to(ROOT).as_posix(),
        "training_script_sha256": sha256(script_path),
        "dataset": {
            "source": "deterministic_synthetic_multi_formation_graph_generator",
            "train_count": args.train_count,
            "holdout_count": args.holdout_count,
            "training_seed": TRAIN_SEED,
            "holdout_seed": HOLDOUT_SEED,
            "maximum_nodes": MAX_NODES,
            "artifacts": {
                name: {
                    "path": path.relative_to(ROOT).as_posix(),
                    "sha256": sha256(path),
                }
                for name, path in artifacts.items()
            },
            "limitation": "Synthetic formation membership only; not an operational group-intelligence benchmark.",
        },
        "architecture": {
            "node_feature_count": NODE_FEATURES,
            "edge_feature_count": EDGE_FEATURES,
            "hidden_size": 32,
            "message_passing_layers": 2,
            "parameter_count": sum(parameter.numel() for parameter in model.parameters()),
            "complexity": "O(N^2) dense pairwise message passing",
        },
        "training": {
            "objective": "class_weighted_binary_cross_entropy",
            "optimizer": "AdamW",
            "epochs": args.epochs,
            "batch_size": args.batch_size,
            "learning_rate": args.learning_rate,
            "weight_decay": 0.0001,
            "random_state": MODEL_SEED,
        },
        "evaluation": {
            "method": "independent_deterministic_graph_holdout",
            **evaluation,
        },
        "runtime_versions": {
            "numpy": np.__version__,
            "scikit_learn": sklearn.__version__,
            "torch": torch.__version__,
        },
    }
    metadata_path = args.save.with_suffix(".metadata.json")
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(evaluation, ensure_ascii=False, indent=2))
    print(f"saved={args.save} sha256={metadata['artifact_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
