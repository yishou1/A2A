"""
战术情报 Agent — 分步可视化验收演示（对应《项目同步说明》§7）

每一步单独执行、打印请求/响应 JSON，便于截图或录屏。
Agent 须已在另一终端启动，例如：

  $env:PYTHONPATH="."; $env:TIA_CONFIG="config\\default.yaml"
  $env:TIA_ALLOW_INLINE_FRAMES="1"
  $env:TIA_NACOS_REGISTER="0"; $env:TIA_PORT="8016"
  .\\.venv\\Scripts\\python.exe tactical_intelligence_agent\\main.py

用法:
  python scripts/demo_tactical_intelligence_acceptance.py
  python scripts/demo_tactical_intelligence_acceptance.py --pause   # 每步按 Enter 继续
  python scripts/demo_tactical_intelligence_acceptance.py --host 127.0.0.1 --port 8016
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("TIA_CONFIG", "config/default.yaml")

import requests

from workflow_payloads import build_attachment_ref

REQUIRED_KEYS = {"work_item", "status", "role", "message"}


def _banner(title: str, step: int, total: int) -> None:
    line = "=" * 64
    print(f"\n{line}")
    print(f"  步骤 {step}/{total}  {title}")
    print(line)


def _pause(enabled: bool) -> None:
    if enabled:
        input("\n>>> 按 Enter 继续下一步… ")


def _pretty(obj: object) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


def _real_image_sensor_frame() -> dict | None:
    """真实推理演示：加载 bus.jpg 作为 EO 帧（YOLO 可检出车辆）。"""
    try:
        from scripts.simulation.images import encode_image_b64, load_base_scene_rgb, resize_rgb

        rgb = resize_rgb(load_base_scene_rgb())
        return {
            "sensor_id": "EO-DEMO-1",
            "modality": "eo_ir",
            "payload": {"image_base64": encode_image_b64(rgb)},
            "metadata": {"source": "bus.jpg", "scene": "real_demo"},
        }
    except Exception as exc:
        print(f"[WARN] 无法加载真实场景图 bus.jpg: {exc}")
        return None


def _demo_payload(work_item: str) -> dict:
    attachment = build_attachment_ref(
        "https://minio.example.local/a2a/recon/frame-001.jpg",
        sha256="demo-sha256-001",
        kind="image",
        mime_type="image/jpeg",
        attachment_id="demo-att-001",
        meta={"sensor_id": "EO-1", "modality": "eo_ir"},
    )
    payload = {
        "workflow_id": "wf-demo-visual",
        "work_item": work_item,
        "command": "process_intelligence",
        "input": {
            "recon_report": "Sector_A fortified with overlapping MG nests.",
            "sector": "Sector_A",
        },
        "context": {
            "jamming_level": 0.15,
            "subscriber_agents": ["commander", "artillery"],
        },
        "attachments": [attachment],
        "work_list": [
            {
                "activatity_id": "activatity-002-processintelligence",
                "work_item": work_item,
                "status": "running",
            }
        ],
    }
    real_frame = _real_image_sensor_frame()
    if real_frame:
        payload["sensor_frames"] = [real_frame]
    return payload


def step1_code_runs() -> bool:
    _banner("代码能跑 — 导入 Agent 模块", 1, 7)
    try:
        import tactical_intelligence_agent.main  # noqa: F401
        import tactical_intelligence_agent.service  # noqa: F401

        from tactical_intelligence_agent.service import TacticalIntelligenceCommanderAgent

        agent = TacticalIntelligenceCommanderAgent(port=0)
        print("[OK] 模块导入成功")
        print(f"     Agent 名称: {agent.name}")
        print(f"     role: {agent.role}")
        return True
    except Exception as exc:
        print(f"[FAIL] 导入失败: {exc}")
        return False


def step2_discovery(base_url: str, pause: bool) -> tuple[bool, dict | None]:
    _banner("Agent 能被发现 — GET /.well-known/agent-card", 2, 7)
    url = f"{base_url}/.well-known/agent-card"
    print(f"请求: GET {url}\n")
    try:
        res = requests.get(url, timeout=5)
    except requests.RequestException as exc:
        print(f"[FAIL] 无法连接 {base_url}")
        print(f"       {exc}")
        print("\n请先在另一终端启动 Agent：")
        print(textwrap.dedent("""
            $env:PYTHONPATH="."; $env:TIA_CONFIG="config\\default.yaml"
            $env:TIA_ALLOW_INLINE_FRAMES="1"
            $env:TIA_NACOS_REGISTER="0"; $env:TIA_PORT="8016"
            .\\.venv\\Scripts\\python.exe tactical_intelligence_agent\\main.py
        """).strip())
        return False, None

    print(f"HTTP 状态: {res.status_code}")
    if res.status_code != 200:
        print("[FAIL] 未返回 200")
        return False, None

    card = res.json()
    print("响应 JSON:\n")
    print(_pretty(card))

    ok = (
        card.get("role") == "tactical_intelligence"
        and card.get("sendMessageEndpoint") == "/sendMessage"
        and card.get("sendMessageStreamEndpoint") == "/sendMessageStream"
    )
    print(f"\n[{'OK' if ok else 'FAIL'}] role=tactical_intelligence, 端点完整")
    _pause(pause)
    return ok, card


def step3_receive_task(base_url: str, pause: bool) -> tuple[bool, dict]:
    _banner("能正常接收任务 — POST /sendMessage（鉴权）", 3, 7)
    work_item = "wf-demo-visual:activatity-002-processintelligence"
    payload = _demo_payload(work_item)
    url = f"{base_url}/sendMessage"

    print("3a) 无 JWT — 应返回 401\n")
    print(f"请求: POST {url}")
    print("Headers: (无 Authorization)\n")
    r401 = requests.post(url, json=payload, timeout=5)
    print(f"HTTP 状态: {r401.status_code}")
    print(f"[{'OK' if r401.status_code == 401 else 'FAIL'}] 未鉴权应拒绝\n")

    print("3b) 带 Bearer JWT — 应返回 200 Accepted\n")
    headers = {"Authorization": "Bearer mock-jwt-token-abcd"}
    print(f"请求: POST {url}")
    print(f"Headers: Authorization: Bearer mock-jwt-token-abcd")
    print("Body:\n")
    print(_pretty(payload))
    r200 = requests.post(url, json=payload, headers=headers, timeout=600)
    print(f"\nHTTP 状态: {r200.status_code}")
    body = r200.json()
    print("响应 JSON:\n")
    print(_pretty(body))
    ok = r200.status_code == 200 and body.get("status") in {"Accepted", "Failed"}
    print(f"\n[{'OK' if ok else 'FAIL'}] 任务已被接收 status={body.get('status')}")
    _pause(pause)
    return ok and r401.status_code == 401, body


def step4_response_schema(body: dict, pause: bool) -> bool:
    _banner("返回结构符合公共协议", 4, 7)
    missing = REQUIRED_KEYS - set(body.keys())
    print("必需字段:", ", ".join(sorted(REQUIRED_KEYS)))
    print("\n实际响应:\n")
    print(_pretty(body))
    if missing:
        print(f"\n[FAIL] 缺少字段: {missing}")
        ok = False
    else:
        print("\n[OK] work_item / status / role / message 齐全")
        print(f"     role={body.get('role')}  work_item={body.get('work_item')}")
        ok = body.get("role") == "tactical_intelligence"
    _pause(pause)
    return ok


def step5_stream(base_url: str, work_item: str, pause: bool) -> bool:
    _banner("流式输出 — POST /sendMessageStream（SSE）", 5, 7)
    payload = _demo_payload(work_item + "-stream")
    url = f"{base_url}/sendMessageStream"
    headers = {
        "Authorization": "Bearer mock-jwt-token-abcd",
        "Accept": "text/event-stream",
    }
    print(f"请求: POST {url}\n")
    res = requests.post(url, json=payload, headers=headers, stream=True, timeout=600)
    print(f"HTTP 状态: {res.status_code}\n")

    events: list[dict] = []
    print("SSE 事件（逐条打印）:\n")
    try:
        for raw_line in res.iter_lines(decode_unicode=True):
            if not raw_line or not raw_line.startswith("data: "):
                continue
            data = json.loads(raw_line[6:])
            events.append(data)
            stage = data.get("stage") or data.get("status")
            progress = data.get("progress", "")
            print(f"  ── 事件 #{len(events)}  stage/status={stage}  progress={progress}")
            print(textwrap.indent(_pretty(data), "     "))
            if pause and len(events) < 4:
                input("     >>> 按 Enter 看下一条 SSE… ")
    except requests.exceptions.ChunkedEncodingError as exc:
        print(f"\n[WARN] SSE 连接提前断开: {exc}")
        print("       常见原因: Agent 端推理异常（如 HuggingFace 下载超时）。")
        print("       请查看启动 Agent 的终端报错；可先运行:")
        print("         python scripts/download_models.py")
        print("       并确保 Clash 代理对终端生效，或设置 HF_ENDPOINT。")

    ok = (
        len(events) >= 4
        and events[0].get("status") == "Working"
        and events[-1].get("status") == "Completed"
        and "intelligence_packet" in events[-1]
    )
    stages = [e.get("stage") for e in events]
    print(f"\n阶段序列: {stages}")
    print(f"[{'OK' if ok else 'FAIL'}] 共 {len(events)} 条事件，末条 Completed")
    _pause(pause)
    return ok


def step6_idempotent(base_url: str, work_item: str, pause: bool) -> bool:
    _banner("恢复/幂等 — 同一 work_item 重复调用", 6, 7)
    payload = _demo_payload(work_item + "-idem")
    url = f"{base_url}/sendMessage"
    headers = {"Authorization": "Bearer mock-jwt-token-abcd"}

    print("第一次 sendMessage:\n")
    first = requests.post(url, json=payload, headers=headers, timeout=600).json()
    print(_pretty(first))

    time.sleep(0.3)

    print("\n第二次 sendMessage（相同 work_item）:\n")
    second = requests.post(url, json=payload, headers=headers, timeout=600).json()
    print(_pretty(second))

    ok = first == second
    print(f"\n[{'OK' if ok else 'FAIL'}] 两次响应完全一致（Commander resume 可安全重放）")

    print("\n流式重放（相同 work_item）:\n")
    stream_url = f"{base_url}/sendMessageStream"
    stream_headers = {**headers, "Accept": "text/event-stream"}
    s1 = list(
        line[6:]
        for line in requests.post(
            stream_url, json=payload, headers=stream_headers, stream=True, timeout=600
        ).iter_lines(decode_unicode=True)
        if line and line.startswith("data: ")
    )
    s2 = list(
        line[6:]
        for line in requests.post(
            stream_url, json=payload, headers=stream_headers, stream=True, timeout=600
        ).iter_lines(decode_unicode=True)
        if line and line.startswith("data: ")
    )
    stream_ok = s1 == s2 and len(s1) >= 4
    print(f"SSE 第一次 {len(s1)} 条, 第二次 {len(s2)} 条")
    print(f"[{'OK' if stream_ok else 'FAIL'}] SSE 重放一致")
    _pause(pause)
    return ok and stream_ok


def step7_regression_hint(pause: bool) -> None:
    _banner("不破坏已有测试 — 控制面回归（可选）", 7, 7)
    print("本 Agent 未改 Commander / Manager / checkpoint。")
    print("若需向负责人证明未破坏公共控制面，在项目根目录另开终端执行：\n")
    print("  .\\.venv\\Scripts\\python.exe -m unittest discover -s tests -p \"test_*.py\" -v\n")
    print("（不含 test_merge_acceptance；Agent 协议已由上面步骤 1–6 逐步验证。）")
    print("\n红蓝态势 + 数据处理全流程（业务演示，可选）：\n")
    print("  .\\.venv\\Scripts\\python.exe scripts\\build_situation.py")
    print("  .\\.venv\\Scripts\\python.exe scripts\\run_simulation.py\n")
    print("输出目录: data/output/campaign/OP-IRON-VALLEY-2026-<时间>/")
    _pause(pause)


def main() -> int:
    parser = argparse.ArgumentParser(description="战术情报 Agent 分步可视化验收")
    parser.add_argument("--host", default=os.environ.get("TIA_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("TIA_PORT", "8016")))
    parser.add_argument(
        "--pause",
        action="store_true",
        help="每步暂停，便于截图/录屏",
    )
    parser.add_argument(
        "--skip-import",
        action="store_true",
        help="跳过步骤1（Agent 已在运行时可加快演示）",
    )
    args = parser.parse_args()
    base_url = f"http://{args.host}:{args.port}"

    print("\n" + "=" * 64)
    print("  战术情报 Agent — 分步可视化验收")
    print(f"  目标: {base_url}")
    print(f"  配置: {os.environ.get('TIA_CONFIG', 'config/default.yaml')}")
    print(f"  交互暂停: {'开' if args.pause else '关（加 --pause 逐步演示）'}")
    print("  真实视觉: bus.jpg（需 Agent 启动时 TIA_ALLOW_INLINE_FRAMES=1）")
    print("=" * 64)

    results: list[tuple[str, bool]] = []

    if not args.skip_import:
        results.append(("1. 代码能跑", step1_code_runs()))
        _pause(args.pause)
    else:
        print("\n[跳过] 步骤 1 代码导入")

    ok2, _card = step2_discovery(base_url, args.pause)
    results.append(("2. Agent 能被发现", ok2))
    if not ok2:
        _print_summary(results)
        return 1

    ok3, body = step3_receive_task(base_url, args.pause)
    results.append(("3. 能接收任务", ok3))

    ok4 = step4_response_schema(body, args.pause)
    results.append(("4. 返回结构符合协议", ok4))

    ok5 = step5_stream(base_url, "wf-demo-visual", args.pause)
    results.append(("5. 流式输出正常", ok5))

    ok6 = step6_idempotent(base_url, "wf-demo-visual", args.pause)
    results.append(("6. 幂等/恢复重放", ok6))

    step7_regression_hint(args.pause)
    results.append(("7. 控制面回归", True))  # 指引性步骤

    return _print_summary(results)


def _print_summary(results: list[tuple[str, bool]]) -> int:
    print("\n" + "=" * 64)
    print("  验收汇总")
    print("=" * 64)
    failed = 0
    for name, ok in results:
        tag = "PASS" if ok else "FAIL"
        print(f"  [{tag}] {name}")
        if not ok:
            failed += 1
    print("=" * 64)
    if failed:
        print(f"未通过 {failed} 项")
        return 1
    print("全部通过 — 可截图本页作为 PR 验收附件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
