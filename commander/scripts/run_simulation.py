"""
统一战场模拟 + 内置校验 + 导出下游 Agent 情报包。

用法:
  .\\.venv\\Scripts\\python scripts\\run_simulation.py

输出:
  data/output/campaign/<campaign_id>/
    campaign_manifest.json
    validation_report.json
    downstream/latest_for_agents.json
    intelligence/01_recon.json … 04_jammed.json
    situation/00_red_blue_overview.json …
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.models.schemas import SemanticIntelligencePacket
from agent.pipeline import (
    agent_config_from_yaml,
    create_agent,
    load_config,
    process_and_package,
)
from scripts.simulation.battlefield import PHASES, PHASE_TO_SITUATION_KEY, BattlefieldSimulation
from scripts.simulation.situation import DEFAULT_SCENARIO, RedBlueSituation
from scripts.simulation.images import encode_image_b64, load_base_scene_rgb
from scripts.simulation.validate import (
    CheckResult,
    preflight_detection_check,
    validate_phase_packet,
)

DEFAULT_OUT = ROOT / "data" / "output" / "campaign"

_REAL_DEPS = (
    "ultralytics",
    "torch",
    "transformers",
    "cv2",
    "sentence_transformers",
)

_PHASE_EXPECTATIONS: dict[str, tuple[int, bool]] = {
    "01_recon": (1, False),
    "02_contact": (1, False),
    "03_bda": (1, False),
    "04_jammed": (1, True),
}


def _check_real_dependencies() -> None:
    missing = [m for m in _REAL_DEPS if not _try_import(m)]
    if not missing:
        return
    print("真实推理缺少依赖:", ", ".join(missing), file=sys.stderr)
    print("  pip install -r requirements-real.txt", file=sys.stderr)
    print("  或使用: $env:TIA_CONFIG=\"config/mock.yaml\"", file=sys.stderr)
    raise SystemExit(1)


def _try_import(name: str) -> bool:
    try:
        __import__(name)
        return True
    except ImportError:
        return False


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _export_packet_file(packet: SemanticIntelligencePacket, out_dir: Path, filename: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / filename
    path.write_text(
        json.dumps(packet.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def _print_checks(checks: list[CheckResult]) -> int:
    failed = 0
    for c in checks:
        tag = "OK" if c.passed else "FAIL"
        print(f"    [{tag}] {c.name}: {c.detail}")
        if not c.passed:
            failed += 1
    return failed


def _run_preflight(cfg: dict, *, use_mock: bool) -> list[CheckResult]:
    if use_mock:
        return [CheckResult("mock 模式", True, "跳过 YOLO 预检")]

    from agent.inference.edl import verify_detections
    from agent.inference.vision import detect_objects

    inf = cfg.get("inference") or {}
    merged = {**inf, **(agent_config_from_yaml(cfg).get("perception") or {})}
    rgb = load_base_scene_rgb()
    print(f"  预检场景尺寸: {rgb.shape[1]}x{rgb.shape[0]}")
    frame = {
        "sensor_id": "PREFLIGHT-EO",
        "modality": "eo_ir",
        "payload": {"image_base64": encode_image_b64(rgb)},
    }
    raw = detect_objects([frame], merged)
    verified = verify_detections(raw, merged)
    return preflight_detection_check(raw_count=len(raw), verified_count=len(verified))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="统一战场模拟（含内置校验），导出下游 Agent 情报包",
    )
    parser.add_argument("-o", "--output-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--campaign-id", type=str, default="")
    parser.add_argument(
        "--scenario",
        type=Path,
        default=DEFAULT_SCENARIO,
        help="红蓝态势场景 YAML",
    )
    args = parser.parse_args()

    cfg = load_config()
    use_mock = bool(cfg.get("use_mock", True))
    mode = "mock" if use_mock else "real"
    if not use_mock:
        _check_real_dependencies()

    situation = RedBlueSituation(args.scenario)
    sim = BattlefieldSimulation(scenario_path=args.scenario, situation=situation)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    campaign_id = args.campaign_id or f"{situation.mission_id}-{ts}"
    campaign_dir = args.output_dir / campaign_id
    intel_dir = campaign_dir / "intelligence"
    downstream_dir = campaign_dir / "downstream"
    situation_dir = campaign_dir / "situation"

    print("=" * 60)
    print("战术情报智能体 — 红蓝态势模拟与验收")
    print(f"  模式: {mode}")
    print(f"  任务: {sim.config.mission_id} | {situation.operation_name}")
    print(f"  场景: {args.scenario}")
    print(f"  输出: {campaign_dir}")
    print("=" * 60)

    situation_dir.mkdir(parents=True, exist_ok=True)
    _write_json(situation_dir / "00_red_blue_overview.json", situation.master_overview())
    for phase_enum, prefix, _ in PHASES:
        sk = PHASE_TO_SITUATION_KEY[phase_enum]
        _write_json(
            situation_dir / f"{prefix}_situation.json",
            situation.snapshot_for_phase(sk),
        )
    print(f"\n[态势] 红蓝编制已写入: {situation_dir}")

    print("\n[预检] 感知链路 (YOLO → EDL)")
    preflight = _run_preflight(cfg, use_mock=use_mock)
    preflight_failed = _print_checks(preflight)
    if preflight_failed:
        print("\n预检失败：真实模式下无法产生可跟踪目标，已中止。", file=sys.stderr)
        return 1

    agent = create_agent(cfg)
    manifest: dict = {
        "campaign_id": campaign_id,
        "mode": mode,
        "mission_id": sim.config.mission_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "validation_passed": True,
        "phases": [],
        "downstream_entrypoint": str(downstream_dir / "latest_for_agents.json"),
    }
    validation_report: dict = {"preflight": [c.__dict__ for c in preflight], "phases": []}

    total_failed = preflight_failed
    last_packet: SemanticIntelligencePacket | None = None

    for phase, prefix, label, batch in sim.all_phases():
        print(f"\n>>> [{prefix}] {label}")
        packet = process_and_package(batch, agent=agent)
        out_path = _export_packet_file(packet, intel_dir, f"{prefix}.json")
        last_packet = packet

        expect_min, expect_jam = _PHASE_EXPECTATIONS.get(prefix, (1, False))
        checks = validate_phase_packet(
            packet,
            phase_prefix=prefix,
            expect_targets_min=expect_min,
            expect_anti_jam=expect_jam,
        )
        phase_failed = _print_checks(checks)
        total_failed += phase_failed

        step = {
            "order": prefix,
            "phase": phase.value,
            "label": label,
            "output": str(out_path),
            "summary": packet.summary,
            "target_count": len(packet.targets),
            "track_ids": [t.get("track_id") for t in packet.targets],
            "anti_jam_mode": packet.routing.get("anti_jam_mode"),
            "passed": phase_failed == 0,
        }
        manifest["phases"].append(step)
        validation_report["phases"].append(
            {"prefix": prefix, "checks": [c.__dict__ for c in checks], "passed": phase_failed == 0}
        )
        print(f"    摘要: {packet.summary}")

    manifest["validation_passed"] = total_failed == 0
    _write_json(campaign_dir / "campaign_manifest.json", manifest)
    _write_json(campaign_dir / "validation_report.json", validation_report)

    if last_packet is not None:
        payload = json.dumps(
            last_packet.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        )
        (campaign_dir / "latest_intelligence.json").write_text(payload, encoding="utf-8")
        downstream_dir.mkdir(parents=True, exist_ok=True)
        (downstream_dir / "latest_for_agents.json").write_text(payload, encoding="utf-8")

    print("\n" + "=" * 60)
    if total_failed == 0:
        print("验收通过 — 全部阶段满足目标数与路由要求")
    else:
        print(f"验收未通过 — 共 {total_failed} 项失败")
    print(f"  清单: {campaign_dir / 'campaign_manifest.json'}")
    print(f"  验收: {campaign_dir / 'validation_report.json'}")
    print(f"  下游: {downstream_dir / 'latest_for_agents.json'}")
    print("=" * 60)
    return 0 if total_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
