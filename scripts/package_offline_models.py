#!/usr/bin/env python3
"""
在有网环境打包全部 TIA 推理依赖，供内网离线部署。

用法（有网机器，项目根目录）:
  python scripts/package_offline_models.py

产物:
  models/pretrained/<model_name>/     HuggingFace 模型快照
  models/checkpoints/*.pt             辅助头与检测权重
  models/offline_manifest.json        文件清单（用于 U 盘/内网校验）

内网机器启动前:
  set TIA_OFFLINE=1
  set HF_HUB_OFFLINE=1
  set TIA_COMPUTE_PROFILE=offline
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PRETRAINED = ROOT / "models" / "pretrained"
CHECKPOINTS = ROOT / "models" / "checkpoints"
MANIFEST_PATH = ROOT / "models" / "offline_manifest.json"

# repo_id -> 本地目录名（与 config/profiles/offline.yaml 对齐）
HF_MODELS: dict[str, str] = {
    "facebook/mask2former-swin-tiny-ade-semantic": "mask2former-swin-tiny-ade-semantic",
    "google/flan-t5-small": "flan-t5-small",
    "paraphrase-MiniLM-L6-v2": "paraphrase-MiniLM-L6-v2",
    "openai/clip-vit-base-patch32": "clip-vit-base-patch32",
}

ULTRALYTICS_WEIGHTS = ["rtdetr-l.pt"]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def download_hf_snapshots() -> list[dict]:
    from huggingface_hub import snapshot_download

    PRETRAINED.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []
    for repo_id, folder_name in HF_MODELS.items():
        local_dir = PRETRAINED / folder_name
        print(f"[HF] {repo_id} -> {local_dir}")
        snapshot_download(repo_id=repo_id, local_dir=str(local_dir), local_dir_use_symlinks=False)
        records.append({"type": "huggingface", "repo_id": repo_id, "path": str(local_dir.relative_to(ROOT))})
    return records


def download_ultralytics() -> list[dict]:
    from ultralytics import RTDETR

    records: list[dict] = []
    CHECKPOINTS.mkdir(parents=True, exist_ok=True)
    for weight in ULTRALYTICS_WEIGHTS:
        print(f"[UL] RT-DETR {weight}")
        model = RTDETR(weight)
        src = Path(getattr(model, "ckpt_path", None) or weight)
        if not src.is_file():
            src = ROOT / weight
        if src.is_file():
            dst = CHECKPOINTS / weight
            if src.resolve() != dst.resolve():
                shutil.copy2(src, dst)
            records.append({"type": "ultralytics", "name": weight, "path": str(dst.relative_to(ROOT))})
    return records


def bootstrap_checkpoints() -> list[dict]:
    from scripts.download_models import bootstrap_auxiliary_heads

    bootstrap_auxiliary_heads()
    records: list[dict] = []
    if CHECKPOINTS.is_dir():
        for pt in sorted(CHECKPOINTS.glob("*.pt")):
            records.append({"type": "checkpoint", "name": pt.name, "path": str(pt.relative_to(ROOT))})
    return records


def write_manifest(entries: list[dict]) -> None:
    files: list[dict] = []
    for entry in entries:
        rel = entry.get("path")
        if not rel:
            continue
        path = ROOT / rel
        if path.is_file():
            files.append(
                {
                    "path": rel.replace("\\", "/"),
                    "size_bytes": path.stat().st_size,
                    "sha256": _sha256(path),
                }
            )
        elif path.is_dir():
            for f in sorted(path.rglob("*")):
                if f.is_file():
                    files.append(
                        {
                            "path": str(f.relative_to(ROOT)).replace("\\", "/"),
                            "size_bytes": f.stat().st_size,
                            "sha256": _sha256(f),
                        }
                    )

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project_root": str(ROOT),
        "file_count": len(files),
        "files": files,
        "env_vars_offline": {
            "TIA_OFFLINE": "1",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "TIA_COMPUTE_PROFILE": "offline",
        },
        "config_profile": "config/profiles/offline.yaml",
    }
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] manifest -> {MANIFEST_PATH} ({len(files)} files)")


def verify_offline_bundle() -> bool:
    """在内网机器上校验 manifest 中的文件是否齐全。"""
    if not MANIFEST_PATH.is_file():
        print(f"[FAIL] missing {MANIFEST_PATH}")
        return False
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    missing = []
    for item in manifest.get("files", []):
        path = ROOT / item["path"]
        if not path.is_file():
            missing.append(item["path"])
    if missing:
        print(f"[FAIL] missing {len(missing)} files, e.g. {missing[:5]}")
        return False
    print(f"[OK] offline bundle verified ({manifest.get('file_count', 0)} files)")
    return True


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Package TIA models for offline deployment")
    parser.add_argument("--verify-only", action="store_true", help="Only verify local manifest")
    parser.add_argument("--skip-hf", action="store_true", help="Skip HuggingFace download")
    parser.add_argument("--skip-ultralytics", action="store_true", help="Skip Ultralytics download")
    args = parser.parse_args()

    if args.verify_only:
        return 0 if verify_offline_bundle() else 1

    entries: list[dict] = []
    entries.extend(bootstrap_checkpoints())
    if not args.skip_ultralytics:
        entries.extend(download_ultralytics())
    if not args.skip_hf:
        entries.extend(download_hf_snapshots())
    write_manifest(entries)
    print("\n[DONE] Copy the entire project folder (or models/ subtree) to the offline machine.")
    print("       On offline host: set TIA_OFFLINE=1 && set TIA_COMPUTE_PROFILE=offline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
