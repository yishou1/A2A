#!/usr/bin/env python3
"""Train and persist the frozen xBD damage classifier."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICES = ROOT / "services"
sys.path.insert(0, str(SERVICES))

from a2a_algorithms_common.xbd_damage_classifier import train_damage_classifier  # noqa: E402


def main() -> None:
    default_csv = Path(__file__).resolve().parents[2] / "A2A" / "data" / "xbd" / "processed" / "xbd_damage_features_train.csv"
    parser = argparse.ArgumentParser(description="Train frozen xBD damage classifier (handcrafted + optional CNN).")
    parser.add_argument("--feature-csv", default=str(default_csv))
    parser.add_argument("--cnn-npz", default="", help="Optional CNN embedding npz; defaults to sibling of feature csv.")
    parser.add_argument("--seed", type=int, default=20260623)
    parser.add_argument("--max-fit-samples", type=int, default=100000)
    parser.add_argument("--model-path", default=str(ROOT / "models" / "xbd_damage_classifier.pkl"))
    parser.add_argument("--metadata-path", default=str(ROOT / "models" / "xbd_damage_classifier.metadata.json"))
    args = parser.parse_args()

    report = train_damage_classifier(
        args.feature_csv,
        cnn_npz=args.cnn_npz,
        seed=args.seed,
        max_fit_samples=max(1000, int(args.max_fit_samples)),
        model_path=args.model_path,
        metadata_path=args.metadata_path,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
