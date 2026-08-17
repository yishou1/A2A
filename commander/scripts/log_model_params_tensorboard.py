"""
统计战术情报 Agent 各算法：参数量、推理耗时、峰值显存，并写入 TensorBoard。

用法（项目根目录）:
  pip install torch tensorboard ultralytics transformers sentence-transformers
  python scripts/log_model_params_tensorboard.py
  python scripts/log_model_params_tensorboard.py --skip-heavy --runs 5
  tensorboard --logdir runs/model_profile

浏览器 http://localhost:6006 查看:
  - params/millions/{skill}/{model}
  - perf/latency_ms/{skill}/{model}
  - perf/peak_gpu_mb/{skill}/{model}
  - TEXT: summary/profile_table
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@dataclass
class ProfileReport:
    name: str
    skill: str
    source: str = ""
    params_total: int | None = None
    params_millions: float | None = None
    latency_ms: float | None = None
    peak_gpu_mb: float | None = None
    status: str = "ok"
    note: str = ""

    @property
    def tag(self) -> str:
        return f"{self.skill}/{self.name}".replace(" ", "_").replace("(", "").replace(")", "")


def _count_module(module: Any) -> int:
    import torch

    if not isinstance(module, torch.nn.Module):
        raise TypeError(f"expected nn.Module, got {type(module)}")
    return sum(p.numel() for p in module.parameters())


def _count_state_dict(path: Path) -> int:
    import torch

    obj = torch.load(path, map_location="cpu", weights_only=True)
    if isinstance(obj, dict):
        if "model" in obj and isinstance(obj["model"], dict):
            obj = obj["model"]
        elif "state_dict" in obj and isinstance(obj["state_dict"], dict):
            obj = obj["state_dict"]
    if not isinstance(obj, dict):
        raise ValueError(f"unsupported checkpoint format: {path}")
    return sum(v.numel() for v in obj.values() if hasattr(v, "numel"))


def _resolve_path(config: dict[str, Any], key: str, default: str) -> Path:
    raw = str(config.get(key, default))
    p = Path(raw)
    if p.is_file():
        return p.resolve()
    for candidate in (ROOT / raw, ROOT / "models" / "checkpoints" / Path(raw).name):
        if candidate.is_file():
            return candidate.resolve()
    return (ROOT / raw).resolve()


def _resolve_device(config: dict[str, Any]) -> str:
    from agent.inference.utils import resolve_device

    return resolve_device(config.get("inference") or config)


def _measure_perf(
    fn: Callable[[], None],
    *,
    device: str,
    warmup: int,
    runs: int,
    use_profiler: bool,
) -> tuple[float, float]:
    """返回 (latency_ms, peak_gpu_mb)。"""
    import torch

    is_cuda = device.startswith("cuda") and torch.cuda.is_available()

    def _sync() -> None:
        if is_cuda:
            torch.cuda.synchronize()

    if is_cuda:
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats(device)

    for _ in range(warmup):
        fn()
    _sync()

    if use_profiler and is_cuda:
        from torch.profiler import ProfilerActivity, profile

        with profile(
            activities=[ProfilerActivity.CPU, ProfilerActivity.CUDA],
            profile_memory=True,
            record_shapes=False,
        ):
            fn()
        _sync()
        if is_cuda:
            torch.cuda.reset_peak_memory_stats(device)

    t0 = time.perf_counter()
    for _ in range(runs):
        fn()
    _sync()
    latency_ms = (time.perf_counter() - t0) / max(runs, 1) * 1000.0

    peak_mb = 0.0
    if is_cuda:
        peak_mb = torch.cuda.max_memory_allocated(device) / (1024 * 1024)

    return latency_ms, peak_mb


def _dummy_rgb(size: int = 640):
    import numpy as np

    return np.random.randint(0, 255, (size, size, 3), dtype=np.uint8)


def _run_benchmark(
    reports: list[ProfileReport],
    *,
    name: str,
    skill: str,
    source: str,
    count_loader: Callable[[], int],
    bench_loader: Callable[[str], Callable[[], None]] | None,
    device: str,
    warmup: int,
    runs: int,
    use_profiler: bool,
    params_only: bool,
) -> None:
    try:
        params = count_loader()
        millions = params / 1e6
        latency_ms: float | None = None
        peak_gpu_mb: float | None = None

        if not params_only and bench_loader is not None:
            bench_fn = bench_loader(device)
            latency_ms, peak_gpu_mb = _measure_perf(
                bench_fn,
                device=device,
                warmup=warmup,
                runs=runs,
                use_profiler=use_profiler,
            )

        reports.append(
            ProfileReport(
                name=name,
                skill=skill,
                source=source,
                params_total=params,
                params_millions=millions,
                latency_ms=latency_ms,
                peak_gpu_mb=peak_gpu_mb,
            )
        )
        extra = ""
        if latency_ms is not None:
            extra = f", {latency_ms:.2f} ms, peak {peak_gpu_mb:.1f} MB GPU"
        print(f"[OK] {name}: {params:,} params ({millions:.3f} M){extra}")
    except Exception as exc:
        reports.append(
            ProfileReport(
                name=name,
                skill=skill,
                source=source,
                status="error",
                note=str(exc),
            )
        )
        print(f"[SKIP] {name}: {exc}")


def collect_reports(
    config: dict[str, Any],
    *,
    device: str,
    skip_heavy: bool,
    warmup: int,
    runs: int,
    use_profiler: bool,
    params_only: bool,
) -> list[ProfileReport]:
    inf = config.get("inference") or {}
    embed_dim = int(inf.get("embed_dim", 1024))
    reports: list[ProfileReport] = []

    from agent.inference.models.edl_head import EvidentialHead
    from agent.inference.models.marl_policy import MARLPolicyNetwork
    from agent.inference.models.mamba_fusion import MultimodalMambaBlock
    from agent.inference.models.motr_kalman import MOTRTracker
    from agent.inference.models.odconv import ODConvRefiner
    from agent.inference.models.supcon_meta import SupConMetaNet

    def _odconv_bench(dev: str):
        model = ODConvRefiner().eval().to(dev)
        import torch

        crops = torch.rand(1, 3, 128, 128, device=dev)
        conf = torch.rand(1, 1, device=dev)

        def _fn():
            with torch.inference_mode():
                model(crops, conf)

        return _fn

    _run_benchmark(
        reports,
        name="ODConvRefiner",
        skill="perception",
        source="agent/inference/models/odconv.py",
        count_loader=lambda: _count_module(ODConvRefiner()),
        bench_loader=_odconv_bench,
        device=device,
        warmup=warmup,
        runs=runs,
        use_profiler=use_profiler,
        params_only=params_only,
    )

    def _edl_bench(dev: str):
        head = EvidentialHead().eval().to(dev)
        import torch

        x = torch.tensor([[0.8, 0.2, 0.15, 0.5, 0.5, 0.1]], device=dev)

        def _fn():
            with torch.inference_mode():
                head(x)

        return _fn

    _run_benchmark(
        reports,
        name="EvidentialHead (EDL)",
        skill="perception",
        source="agent/inference/models/edl_head.py",
        count_loader=lambda: _count_module(EvidentialHead()),
        bench_loader=_edl_bench,
        device=device,
        warmup=warmup,
        runs=runs,
        use_profiler=use_profiler,
        params_only=params_only,
    )

    def _motr_bench(dev: str):
        tracker = MOTRTracker().eval().to(dev)
        import torch

        crops = torch.rand(2, 3, 96, 96, device=dev)

        def _fn():
            with torch.inference_mode():
                tracker.cost_net.embed_crops(crops)

        return _fn

    _run_benchmark(
        reports,
        name="MOTRTracker (cost_net)",
        skill="perception",
        source="agent/inference/models/motr_kalman.py",
        count_loader=lambda: _count_module(MOTRTracker()),
        bench_loader=_motr_bench,
        device=device,
        warmup=warmup,
        runs=runs,
        use_profiler=use_profiler,
        params_only=params_only,
    )

    det_path = _resolve_path(inf, "detection_model", "rtdetr-l.pt")

    def _rtdetr_count() -> int:
        from ultralytics import RTDETR

        if not det_path.is_file():
            raise FileNotFoundError(det_path)
        return _count_module(RTDETR(str(det_path)).model)

    def _rtdetr_bench(dev: str):
        from ultralytics import RTDETR

        model = RTDETR(str(det_path))
        img = _dummy_rgb(640)
        dev_arg = None if inf.get("device", "auto") == "auto" else dev

        def _fn():
            model.predict(source=img, conf=0.25, verbose=False, device=dev_arg)

        return _fn

    _run_benchmark(
        reports,
        name="RT-DETR (detector)",
        skill="perception",
        source=str(det_path.relative_to(ROOT) if det_path.is_file() else det_path),
        count_loader=_rtdetr_count,
        bench_loader=_rtdetr_bench if det_path.is_file() else None,
        device=device,
        warmup=warmup,
        runs=runs,
        use_profiler=use_profiler,
        params_only=params_only,
    )

    if not skip_heavy:
        mask_id = str(inf.get("mask2former_model", "facebook/mask2former-swin-tiny-ade-semantic"))

        def _mask_count() -> int:
            from transformers import Mask2FormerForUniversalSegmentation

            return _count_module(Mask2FormerForUniversalSegmentation.from_pretrained(mask_id))

        def _mask_bench(dev: str):
            import torch
            from PIL import Image
            from transformers import Mask2FormerForUniversalSegmentation, Mask2FormerImageProcessor

            processor = Mask2FormerImageProcessor.from_pretrained(mask_id)
            model = Mask2FormerForUniversalSegmentation.from_pretrained(mask_id).eval().to(dev)
            pil = Image.fromarray(_dummy_rgb(512))
            inputs = processor(images=pil, return_tensors="pt")
            inputs = {k: v.to(dev) for k, v in inputs.items()}

            def _fn():
                with torch.inference_mode():
                    model(**inputs)

            return _fn

        _run_benchmark(
            reports,
            name="Siamese Mask2Former",
            skill="perception",
            source=mask_id,
            count_loader=_mask_count,
            bench_loader=_mask_bench,
            device=device,
            warmup=warmup,
            runs=runs,
            use_profiler=use_profiler,
            params_only=params_only,
        )

        def _embed_count() -> int:
            from agent.inference.registry import get_imagebind

            embedder = get_imagebind(inf)
            if embedder.model is not None:
                return _count_module(embedder.model)
            if embedder._clip is not None:
                return _count_module(embedder._clip.model)
            raise RuntimeError("ImageBind / CLIP 均未加载")

        def _embed_bench(dev: str):
            from agent.inference.registry import get_imagebind

            embedder = get_imagebind(inf)
            frames = [
                {
                    "sensor_id": "bench",
                    "modality": "eo_ir",
                    "payload": {"image_base64": _rgb_to_b64(_dummy_rgb(224))},
                }
            ]

            def _fn():
                embedder.embed_frames(frames)

            return _fn

        _run_benchmark(
            reports,
            name="ImageBind (or CLIP fallback)",
            skill="cognition",
            source="agent/inference/models/imagebind_model.py",
            count_loader=_embed_count,
            bench_loader=_embed_bench,
            device=device,
            warmup=warmup,
            runs=runs,
            use_profiler=use_profiler,
            params_only=params_only,
        )

    def _mamba_bench(dev: str):
        block = MultimodalMambaBlock(embed_dim).eval().to(dev)
        import torch

        seq = torch.randn(1, 4, embed_dim, device=dev)

        def _fn():
            with torch.inference_mode():
                block(seq)

        return _fn

    _run_benchmark(
        reports,
        name=f"MultimodalMambaBlock ({embed_dim})",
        skill="cognition",
        source="agent/inference/models/mamba_fusion.py",
        count_loader=lambda: _count_module(MultimodalMambaBlock(embed_dim)),
        bench_loader=_mamba_bench,
        device=device,
        warmup=warmup,
        runs=runs,
        use_profiler=use_profiler,
        params_only=params_only,
    )

    def _supcon_bench(dev: str):
        net = SupConMetaNet(in_dim=embed_dim).eval().to(dev)
        import torch

        fused = {f"T-{i:04d}": [0.1] * embed_dim for i in range(3)}

        def _fn():
            with torch.inference_mode():
                net.classify(fused, device=dev, support_shots=[], temperature=0.07)

        return _fn

    _run_benchmark(
        reports,
        name=f"SupConMetaNet ({embed_dim})",
        skill="cognition",
        source="agent/inference/models/supcon_meta.py",
        count_loader=lambda: _count_module(SupConMetaNet(in_dim=embed_dim)),
        bench_loader=_supcon_bench,
        device=device,
        warmup=warmup,
        runs=runs,
        use_profiler=use_profiler,
        params_only=params_only,
    )

    if not skip_heavy:
        page_id = str(inf.get("page_index_model", "paraphrase-MiniLM-L6-v2"))

        def _page_count() -> int:
            from sentence_transformers import SentenceTransformer

            return _count_module(SentenceTransformer(page_id))

        def _page_bench(_dev: str):
            from sentence_transformers import SentenceTransformer

            enc = SentenceTransformer(page_id)

            def _fn():
                enc.encode(["战场目标实体与威胁关联"], normalize_embeddings=True)

            return _fn

        _run_benchmark(
            reports,
            name="SynapseRAG PageEncoder",
            skill="cognition",
            source=page_id,
            count_loader=_page_count,
            bench_loader=_page_bench,
            device=device,
            warmup=warmup,
            runs=runs,
            use_profiler=use_profiler,
            params_only=params_only,
        )

        t5_id = str(inf.get("semantic_comm_model", "google/flan-t5-small"))

        def _t5_count() -> int:
            from transformers import AutoModelForSeq2SeqLM

            return _count_module(AutoModelForSeq2SeqLM.from_pretrained(t5_id))

        def _t5_bench(dev: str):
            from agent.inference.models.semantic_comm_net import KnowledgeSemanticCommNet

            net = KnowledgeSemanticCommNet(t5_id).to_device(dev)
            perception = {"detections": [{"track_id": "T-0001", "class_name": "ship", "confidence": 0.9}]}
            cognition = {
                "classifications": [{"target_id": "T-0001", "label": "hostile"}],
                "threats": [{"target_id": "T-0001", "threat_level": "high"}],
                "entities": [],
            }

            def _fn():
                net.compress(perception, cognition)

            return _fn

        _run_benchmark(
            reports,
            name="Knowledge Semantic Comm (T5)",
            skill="communication",
            source=t5_id,
            count_loader=_t5_count,
            bench_loader=_t5_bench,
            device=device,
            warmup=warmup,
            runs=runs,
            use_profiler=use_profiler,
            params_only=params_only,
        )

    def _marl_bench(dev: str):
        policy = MARLPolicyNetwork().eval().to(dev)
        import torch

        state = torch.rand(1, 8, device=dev)

        def _fn():
            with torch.inference_mode():
                policy(state)

        return _fn

    _run_benchmark(
        reports,
        name="MARLPolicyNetwork",
        skill="communication",
        source="agent/inference/models/marl_policy.py",
        count_loader=lambda: _count_module(MARLPolicyNetwork()),
        bench_loader=_marl_bench,
        device=device,
        warmup=warmup,
        runs=runs,
        use_profiler=use_profiler,
        params_only=params_only,
    )

    return reports


def _rgb_to_b64(rgb) -> str:
    import base64
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.fromarray(rgb).save(buf, format="JPEG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _markdown_table(reports: list[ProfileReport]) -> str:
    lines = [
        "| Skill | Model | Params (M) | Latency (ms) | Peak GPU (MB) | Status | Source |",
        "|-------|-------|----------:|-------------:|--------------:|--------|--------|",
    ]
    for r in reports:
        pm = f"{r.params_millions:.3f}" if r.params_millions is not None else "-"
        lat = f"{r.latency_ms:.2f}" if r.latency_ms is not None else "-"
        mem = f"{r.peak_gpu_mb:.1f}" if r.peak_gpu_mb is not None else "-"
        source = (r.source or "").replace("|", "\\|")
        note = r.note.replace("|", "\\|") if r.note else r.status
        lines.append(
            f"| {r.skill} | {r.name} | {pm} | {lat} | {mem} | {note} | {source} |"
        )
    return "\n".join(lines)


def write_tensorboard(reports: list[ProfileReport], logdir: Path) -> None:
    from torch.utils.tensorboard import SummaryWriter

    logdir.mkdir(parents=True, exist_ok=True)
    writer = SummaryWriter(log_dir=str(logdir))

    total_params = 0
    total_latency = 0.0
    latency_count = 0

    for r in reports:
        if r.status != "ok":
            continue
        tag = r.tag
        if r.params_total is not None:
            writer.add_scalar(f"params/total/{tag}", r.params_total, 0)
            writer.add_scalar(f"params/millions/{tag}", r.params_millions, 0)
            total_params += r.params_total
        if r.latency_ms is not None:
            writer.add_scalar(f"perf/latency_ms/{tag}", r.latency_ms, 0)
            total_latency += r.latency_ms
            latency_count += 1
        if r.peak_gpu_mb is not None:
            writer.add_scalar(f"perf/peak_gpu_mb/{tag}", r.peak_gpu_mb, 0)

    if total_params:
        writer.add_scalar("params/pipeline_millions", total_params / 1e6, 0)
    if latency_count:
        writer.add_scalar("perf/pipeline_latency_ms_sum", total_latency, 0)

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    writer.add_text("summary/profile_table", f"Generated at {ts}\n\n" + _markdown_table(reports), 0)
    writer.flush()
    writer.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Log params / latency / GPU memory for TIA algorithms to TensorBoard"
    )
    parser.add_argument("--config", type=Path, default=Path("config/default.yaml"))
    parser.add_argument(
        "--profile",
        choices=("small", "medium", "large"),
        help="覆盖 inference.compute_profile / TIA_COMPUTE_PROFILE",
    )
    parser.add_argument("--logdir", type=Path, default=Path("runs/model_profile"))
    parser.add_argument(
        "--skip-heavy",
        action="store_true",
        help="跳过 Mask2Former / ImageBind / T5 / MiniLM",
    )
    parser.add_argument(
        "--params-only",
        action="store_true",
        help="仅统计参数量，不测耗时与显存",
    )
    parser.add_argument("--warmup", type=int, default=1, help="计时的预热次数")
    parser.add_argument("--runs", type=int, default=3, help="计时重复次数（取平均）")
    parser.add_argument(
        "--use-profiler",
        action="store_true",
        help="使用 PyTorch Profiler（profile_memory=True）；默认用 perf_counter + max_memory_allocated",
    )
    parser.add_argument("--device", default="", help="覆盖 config 中的 device，如 cuda:0 或 cpu")
    args = parser.parse_args()

    try:
        import torch  # noqa: F401
        from torch.utils.tensorboard import SummaryWriter  # noqa: F401
    except ImportError as exc:
        print("请先安装: pip install torch tensorboard")
        raise SystemExit(1) from exc

    import os

    import yaml

    from agent.config_profiles import apply_compute_profile

    cfg_path = args.config if args.config.is_absolute() else ROOT / args.config
    if cfg_path.is_file():
        config = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    else:
        alt = os.environ.get("TIA_CONFIG", "config/default.yaml")
        config = yaml.safe_load(Path(alt).read_text(encoding="utf-8")) if Path(alt).is_file() else {}
    if args.profile:
        os.environ["TIA_COMPUTE_PROFILE"] = args.profile
    config = apply_compute_profile(config)
    profile = (config.get("inference") or {}).get("compute_profile", "medium")
    device = args.device or _resolve_device(config)
    logdir = args.logdir if args.logdir.is_absolute() else ROOT / args.logdir
    if args.profile:
        logdir = logdir / profile

    print(f"Config:   {cfg_path if cfg_path.is_file() else 'TIA_CONFIG / default'}")
    print(f"Profile:  {profile}")
    print(f"Device:   {device}")
    print(f"Logdir:   {logdir}")
    print(f"Warmup:   {args.warmup}  Runs: {args.runs}")
    print(f"Skip heavy: {args.skip_heavy}  Params only: {args.params_only}\n")

    reports = collect_reports(
        config,
        device=device,
        skip_heavy=args.skip_heavy,
        warmup=args.warmup,
        runs=args.runs,
        use_profiler=args.use_profiler,
        params_only=args.params_only,
    )
    write_tensorboard(reports, logdir)

    ok = sum(1 for r in reports if r.status == "ok")
    print(f"\n[DONE] {ok}/{len(reports)} algorithms profiled.")
    print(f"tensorboard --logdir {logdir.relative_to(ROOT)}")
    print("浏览器: http://localhost:6006")
    print("  SCALARS → params/millions/*  perf/latency_ms/*  perf/peak_gpu_mb/*")
    print("  TEXT    → summary/profile_table")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
