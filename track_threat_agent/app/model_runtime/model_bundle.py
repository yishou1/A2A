"""Validation for versioned ST-GNN model bundles produced offline."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


SCHEMA_VERSION = "st_gnn_model_bundle/v1"
REQUIRED_MANIFEST_FIELDS = {
    "schema_version",
    "model_type",
    "model_version",
    "object_type",
    "framework",
    "history_points",
    "prediction_horizons_s",
    "node_feature_schema",
    "edge_feature_schema",
    "weights_file",
    "normalization_file",
    "metrics_file",
}


class ModelBundleLoader:
    """Inspect a model bundle without importing PyTorch or loading weights."""

    def __init__(self, bundle_dir: Path | str | None) -> None:
        self.bundle_dir = Path(bundle_dir).expanduser() if bundle_dir else None
        self.manifest: Dict[str, Any] = {}
        self.load_error: str | None = None
        self._inspect()

    @property
    def loaded(self) -> bool:
        return bool(self.manifest) and self.load_error is None

    def status(self) -> Dict[str, Any]:
        return {
            "loaded": self.loaded,
            "schema_version": self.manifest.get("schema_version", SCHEMA_VERSION),
            "bundle_dir": str(self.bundle_dir) if self.bundle_dir else None,
            "model_type": self.manifest.get("model_type"),
            "model_version": self.manifest.get("model_version"),
            "object_type": self.manifest.get("object_type"),
            "framework": self.manifest.get("framework"),
            "history_points": self.manifest.get("history_points"),
            "prediction_horizons_s": self.manifest.get("prediction_horizons_s", []),
            "load_error": self.load_error,
        }

    def _inspect(self) -> None:
        if self.bundle_dir is None:
            self.load_error = "ST_GNN_MODEL_DIR is not configured"
            return
        manifest_path = self.bundle_dir / "model_manifest.json"
        if not manifest_path.is_file():
            self.load_error = f"missing model_manifest.json in {self.bundle_dir}"
            return
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            self.load_error = f"invalid model_manifest.json: {exc}"
            return
        missing_fields = sorted(REQUIRED_MANIFEST_FIELDS - set(payload))
        if missing_fields:
            self.load_error = f"model manifest missing fields: {', '.join(missing_fields)}"
            return
        if payload["schema_version"] != SCHEMA_VERSION:
            self.load_error = (
                f"unsupported model bundle schema {payload['schema_version']}; "
                f"expected {SCHEMA_VERSION}"
            )
            return
        if payload["model_type"] != "st_gnn":
            self.load_error = f"unsupported model_type {payload['model_type']}"
            return
        for field in ("weights_file", "normalization_file", "metrics_file"):
            artifact_path = self._artifact_path(str(payload[field]))
            if artifact_path is None or not artifact_path.is_file():
                self.load_error = f"missing bundle artifact for {field}: {payload[field]}"
                return
        self.manifest = payload

    def _artifact_path(self, relative_path: str) -> Path | None:
        if self.bundle_dir is None:
            return None
        candidate = (self.bundle_dir / relative_path).resolve()
        bundle_root = self.bundle_dir.resolve()
        if candidate != bundle_root and bundle_root not in candidate.parents:
            return None
        return candidate
