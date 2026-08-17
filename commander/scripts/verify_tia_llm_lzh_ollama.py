"""用 lzh 分支同款小模型配置验证 TIA 动态选算法。

lzh tests/test_llm_client.py 默认：
  TOOL_LLM_URL=http://127.0.0.1:11434/v1
  TOOL_LLM_NAME=qwen3:1.7b
  API_KEY=ollama

用法（PowerShell）::

    # 先启动 Ollama 并拉取模型（与 lzh 一致）
    # ollama serve
    # ollama pull qwen3:1.7b

    .\\.venv\\Scripts\\python.exe scripts/verify_tia_llm_lzh_ollama.py
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# --- 对齐 lzh test_llm_client 默认环境 ---
os.environ.setdefault("LLM_PROVIDER", "openai_compatible")
os.environ.setdefault("TOOL_LLM_URL", "http://127.0.0.1:11434/v1")
os.environ.setdefault("TOOL_LLM_NAME", "qwen3:1.7b")
os.environ.setdefault("API_KEY", "ollama")
os.environ.setdefault("LLM_MAX_TOKENS", "512")
os.environ.setdefault("LLM_TEMPERATURE", "0.1")
os.environ.setdefault("LLM_JSON_MODE", "false")  # Ollama 部分模型对 json_mode 支持一般
os.environ.setdefault("LLM_STRIP_THINKING", "true")
os.environ.setdefault("LLM_JSON_RETRY_COUNT", "1")
os.environ.setdefault("ENABLE_LLM", "true")
os.environ.setdefault("TIA_ALGORITHM_PLANNER", "llm")
os.environ.setdefault("TIA_ALGOLIB_CALL_MODE", "run")
os.environ.setdefault("ALGOLIB_BASE_URL", "http://127.0.0.1:8088")

from agent.algorithm_library.planner_runtime import plan_algorithms  # noqa: E402
from agent.models.schemas import SensorBatch, SensorFrame, SensorModality  # noqa: E402
from tactical_intelligence_agent.llm.client import OpenAICompatibleClient, ToolLLMSettings  # noqa: E402

OUT = ROOT / "data" / "output" / "verify_tia_llm_lzh"


def _get(url: str, timeout: float = 5.0) -> dict | list | str:
    req = Request(url, method="GET")
    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def check_ollama() -> bool:
    base = os.environ["TOOL_LLM_URL"].rstrip("/")
    # TOOL_LLM_URL 形如 http://127.0.0.1:11434/v1 → 探活用 /api/tags
    root = base[:-3] if base.endswith("/v1") else base
    print(f"[check] TOOL_LLM_URL={os.environ['TOOL_LLM_URL']}")
    print(f"[check] TOOL_LLM_NAME={os.environ['TOOL_LLM_NAME']}")
    try:
        tags = _get(f"{root}/api/tags")
    except Exception as exc:
        print(f"[FAIL] Ollama 不可达: {exc}")
        print("  请先安装并启动（与 lzh 一致）:")
        print("    ollama serve")
        print("    ollama pull qwen3:1.7b")
        return False

    models = []
    if isinstance(tags, dict):
        models = [m.get("name", "") for m in tags.get("models") or []]
    print(f"[ok] Ollama models: {models}")
    want = os.environ["TOOL_LLM_NAME"]
    # ollama 名称可能是 qwen3:1.7b 或带 registry 前缀
    if not any(want in m or m.startswith(want.split(":")[0]) for m in models):
        print(f"[WARN] 未看到模型 {want}，尝试继续调用；若失败请执行: ollama pull {want}")
    return True


def check_gateway() -> bool:
    url = os.environ["ALGOLIB_BASE_URL"].rstrip("/") + "/health"
    try:
        body = _get(url)
        print(f"[ok] algolib gateway: {body}")
        return True
    except Exception as exc:
        print(f"[WARN] 网关未启动 ({exc})；规划仍可用本地 catalog 回退")
        return False


def run_planner() -> int:
    settings = ToolLLMSettings.from_config(
        {
            "tool_llm": {"enable": True},
            "algorithm_planner": {"mode": "llm", "fallback_to_fixed": False},
        }
    )
    llm = OpenAICompatibleClient(settings)
    batch = SensorBatch(
        mission_id="wf-verify-lzh-ollama",
        frames=[
            SensorFrame(
                sensor_id="EO-1",
                modality=SensorModality.EO_IR,
                timestamp=datetime.now(timezone.utc),
                payload={"image_uri": "https://example.local/frame.png"},
                metadata={"modality": "eo_ir"},
            )
        ],
        context={
            "command": "process_intelligence",
            "has_reference_frame": False,
            "knowledge_base": [],
            "jamming_level": 0.0,
            "subscriber_agents": ["commander"],
            "recon_report": "UE naval recon; verify lzh ollama planner",
        },
    )
    # batch_context_summary 读 context 字段；上面已够用
    print("[run] calling real LLM for algorithm plan...")
    plan = plan_algorithms(
        batch,
        config={
            "tool_llm": {"enable": True},
            "algorithm_planner": {"mode": "llm", "fallback_to_fixed": False},
            "algorithm_library": {
                "call_mode": "run",
                "base_url": os.environ["ALGOLIB_BASE_URL"],
            },
        },
        llm_client=llm,
    )
    dump = plan.to_dict()
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "llm_plan.json"
    path.write_text(json.dumps(dump, ensure_ascii=False, indent=2), encoding="utf-8")

    selected = [c.algorithm_id for c in plan.algorithm_calls]
    print(f"[plan] mode={plan.mode} intent={plan.intent}")
    print(f"[plan] catalog_source={plan.catalog_source}")
    print(f"[plan] selected={selected}")
    print(f"[plan] explanation={plan.explanation}")
    print(f"[plan] saved -> {path}")

    required = {
        "battlefield_rtdetr_detector",
        "edl_evidential_verifier",
        "motr_neural_kalman_tracker",
    }
    missing = required - set(selected)
    if plan.mode != "llm":
        print("[FAIL] plan.mode is not llm")
        return 1
    if missing:
        print(f"[FAIL] missing required algorithms: {missing}")
        return 1
    # 故意无参考帧/知识库时，期望有机会跳过 optional
    optional_skipped = [
        x
        for x in (
            "siamese_mask2former_damage",
            "synapse_rag_retriever",
        )
        if x not in set(selected)
    ]
    print(f"[info] optional skipped (best-effort): {optional_skipped}")
    print("[OK] lzh-compatible LLM produced a valid TIA algorithm plan")
    return 0


def main() -> int:
    print("=== TIA LLM verify with lzh Ollama settings ===")
    if not check_ollama():
        return 2
    check_gateway()
    try:
        return run_planner()
    except Exception as exc:
        print(f"[FAIL] planner error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
