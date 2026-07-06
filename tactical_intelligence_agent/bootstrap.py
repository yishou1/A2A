"""加载配置并构建 TacticalIntelligenceAgent 引擎。"""

from __future__ import annotations

import os
from typing import Any

from agent.orchestrator import TacticalIntelligenceAgent
from agent.pipeline import agent_config_from_yaml, create_agent, load_config


def create_engine(config: dict[str, Any] | None = None) -> TacticalIntelligenceAgent:
    if config is None:
        config = load_config()
    inf = config.get("inference") or {}
    profile = inf.get("compute_profile", "medium")
    requested = inf.get("compute_profile_requested", profile)
    if requested == "auto" or inf.get("compute_profile_auto_reason"):
        reason = inf.get("compute_profile_auto_reason", "")
        print(f"[TIA] compute profile: {profile} (requested={requested}, {reason})")
    else:
        print(f"[TIA] compute profile: {profile}")
    return create_agent(config)


def warmup_inference(config: dict[str, Any] | None = None) -> None:
    """启动时预加载重型模型，避免 SSE 流式响应中途因首次下载/推理失败而断连。"""
    if os.environ.get("TIA_SKIP_WARMUP", "0") == "1":
        return
    cfg = config if config is not None else load_config()
    if cfg.get("use_mock"):
        return
    inf_cfg = agent_config_from_yaml(cfg)
    inference = inf_cfg.get("inference") or {}
    try:
        from agent.inference.registry import get_detector, get_imagebind

        get_detector(inference)
        get_imagebind(inference)
        print("[TIA] inference warmup OK (detector + embedder)")
    except Exception as exc:
        print(f"[TIA] inference warmup skipped: {exc}")


def default_role() -> str:
    return os.environ.get("TIA_A2A_ROLE", "tactical_intelligence")
