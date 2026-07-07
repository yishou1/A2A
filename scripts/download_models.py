"""下载/初始化真实推理模型与辅助头权重。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CHECKPOINT_DIR = ROOT / "models" / "checkpoints"


def bootstrap_auxiliary_heads() -> None:
    """保存 ODConv/EDL/MOTR/Mamba/SupCon/MARL 等辅助网络初始权重。"""
    import torch

    from agent.inference.models.edl_head import EvidentialHead
    from agent.inference.models.marl_policy import MARLPolicyNetwork
    from agent.inference.models.mamba_fusion import MultimodalMambaBlock
    from agent.inference.models.motr_kalman import MOTRTracker
    from agent.inference.models.odconv import ODConvRefiner
    from agent.inference.models.supcon_meta import SupConMetaNet

    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    heads = {
        "odconv_refiner.pt": ODConvRefiner(),
        "edl_head.pt": EvidentialHead(),
        "motr_tracker.pt": MOTRTracker(),
        "mamba_fusion.pt": MultimodalMambaBlock(1024),
        "supcon_meta.pt": SupConMetaNet(in_dim=1024),
        "marl_policy.pt": MARLPolicyNetwork(),
    }
    for name, module in heads.items():
        path = CHECKPOINT_DIR / name
        torch.save(module.state_dict(), path)
        print(f"[OK] saved {path}")

    # 小/大档 Mamba、SupCon 辅助头（维度与 profiles 对齐）
    variants = {
        "mamba_fusion_s.pt": MultimodalMambaBlock(256),
        "mamba_fusion_l.pt": MultimodalMambaBlock(1536),
        "supcon_meta_s.pt": SupConMetaNet(in_dim=256),
        "supcon_meta_l.pt": SupConMetaNet(in_dim=1536),
    }
    for name, module in variants.items():
        path = CHECKPOINT_DIR / name
        if path.is_file():
            print(f"[SKIP] {path} exists")
            continue
        torch.save(module.state_dict(), path)
        print(f"[OK] saved {path}")


def prefetch_pretrained() -> None:
    """预下载 HuggingFace / Ultralytics 预训练权重。"""
    from ultralytics import RTDETR

    print("[DL] RT-DETR rtdetr-l.pt ...")
    RTDETR("rtdetr-l.pt")

    from transformers import (
        AutoModelForSeq2SeqLM,
        AutoTokenizer,
        Mask2FormerForUniversalSegmentation,
        Mask2FormerImageProcessor,
    )

    mask_id = "facebook/mask2former-swin-tiny-ade-semantic"
    print(f"[DL] Mask2Former {mask_id} ...")
    Mask2FormerImageProcessor.from_pretrained(mask_id)
    Mask2FormerForUniversalSegmentation.from_pretrained(mask_id)

    t5_id = "google/flan-t5-small"
    print(f"[DL] Semantic Comm {t5_id} ...")
    AutoTokenizer.from_pretrained(t5_id)
    AutoModelForSeq2SeqLM.from_pretrained(t5_id)

    from sentence_transformers import SentenceTransformer

    print("[DL] PageIndex encoder ...")
    SentenceTransformer("paraphrase-MiniLM-L6-v2")

    print("[DL] ImageBind (需单独安装 imagebind 包) ...")
    try:
        from imagebind.models import imagebind_model

        imagebind_model.imagebind_huge(pretrained=True)
        print("[OK] ImageBind weights ready")
    except ImportError:
        print("[WARN] 请安装: pip install git+https://github.com/facebookresearch/ImageBind.git")


def main() -> int:
    bootstrap_auxiliary_heads()
    prefetch_pretrained()
    print("\n[DONE] models/checkpoints 与预训练权重已就绪。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
