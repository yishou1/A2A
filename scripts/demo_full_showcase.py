from __future__ import annotations

import argparse
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass
class StepResult:
    title: str
    status: str
    seconds: float


def parse_args():
    parser = argparse.ArgumentParser(description="Run the full A2A group-meeting showcase")
    parser.add_argument(
        "--skip-enhanced",
        action="store_true",
        help="Skip Nacos-backed demos 9 and 10.",
    )
    parser.add_argument(
        "--include-redis",
        action="store_true",
        help="Run Redis distributed-lock tests when Redis is available.",
    )
    parser.add_argument(
        "--details",
        action="store_true",
        help="Pass --details to scripts that support verbose output.",
    )
    parser.add_argument(
        "--show-nacos-ui",
        action="store_true",
        help="Pause Nacos-backed demos at frontend-observable metadata states.",
    )
    parser.add_argument(
        "--ui-hold-seconds",
        type=float,
        default=8.0,
        help="Seconds to pause at each Nacos UI inspection point.",
    )
    parser.add_argument(
        "--ui-wait-enter",
        action="store_true",
        help="Wait for Enter at each Nacos UI inspection point.",
    )
    return parser.parse_args()


def progress(message: str):
    print(message, flush=True)


def run_command(title: str, command: list[str], *, allow_skip=False) -> StepResult:
    progress(f"\n\n## {title}")
    progress("[RUN] " + " ".join(command))
    started = time.perf_counter()
    completed = subprocess.run(command, cwd=PROJECT_ROOT)
    seconds = time.perf_counter() - started
    if completed.returncode == 0:
        progress(f"[OK] {title} ({seconds:.1f}s)")
        return StepResult(title, "OK", seconds)
    if allow_skip:
        progress(f"[SKIP] {title} returned {completed.returncode} ({seconds:.1f}s)")
        return StepResult(title, "SKIP", seconds)
    raise subprocess.CalledProcessError(completed.returncode, command)


def port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) == 0


def maybe_details(args) -> list[str]:
    return ["--details"] if args.details else []


def maybe_nacos_ui(args) -> list[str]:
    if not args.show_nacos_ui:
        return []
    result = ["--show-nacos-ui", "--ui-hold-seconds", str(args.ui_hold_seconds)]
    if args.ui_wait_enter:
        result.append("--ui-wait-enter")
    return result


def main():
    args = parse_args()
    py = sys.executable
    results: list[StepResult] = []

    progress("=== A2A FULL SHOWCASE ===")
    progress("这条总控演示会把分散能力串成一条完整故事线。")

    results.append(
        run_command(
            "1. 环境与 BPEL 列表",
            [py, "commander_agent/main.py", "--list-workflows"],
        )
    )
    results.append(
        run_command(
            "2. BPEL 成功分支：Recon -> Artillery -> Evaluator -> Assault",
            [
                py,
                "commander_agent/main.py",
                "--mode",
                "local",
                "--workflow",
                "bpel",
                "--workflow-file",
                "beachhead_workflow",
                "--mock-eval-score",
                "75",
                *maybe_details(args),
            ],
        )
    )
    results.append(
        run_command(
            "3. BPEL 低评分分支：触发重规划并保存 paused checkpoint",
            [
                py,
                "commander_agent/main.py",
                "--mode",
                "local",
                "--workflow",
                "bpel",
                "--workflow-file",
                "beachhead_workflow",
                "--mock-eval-score",
                "40",
                *maybe_details(args),
            ],
        )
    )
    results.append(
        run_command(
            "4. 一体化三层并发：workflow + activity + same-role Agent",
            [py, "scripts/demo_integrated_concurrency.py", *maybe_details(args)],
        )
    )
    results.append(
        run_command(
            "5. Checkpoint 断点恢复：重启后从 activity=2 继续",
            [py, "scripts/demo_resume_after_restart.py", "--reset", *maybe_details(args)],
        )
    )
    results.append(
        run_command(
            "6. Commander 宕机接管：心跳检测 + failover Commander + resume",
            [
                py,
                "scripts/demo_commander_failover_resume.py",
                "--reset",
                "--heartbeat-interval",
                "1",
                "--crash-after-seconds",
                "2",
                *maybe_details(args),
            ],
        )
    )
    results.append(
        run_command(
            "7. Agent 故障重指派：调用失败、熔断、运行中心跳丢失、晚到响应忽略",
            [py, "scripts/demo_agent_failover_reassignment.py", "--reset", *maybe_details(args)],
        )
    )

    if not args.skip_enhanced:
        results.append(
            run_command(
                "8. 启动增强演示基础设施：Nacos + auth mock",
                [
                    "powershell",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    "scripts/start_demo_infra.ps1",
                ],
            )
        )
        results.append(
            run_command(
                "9. 异常韧性：自动重试、同角色 failover、熔断、traceback",
                [
                    py,
                    "scripts/demo_exception_resilience.py",
                    "--startup-timeout",
                    "60",
                    *maybe_details(args),
                    *maybe_nacos_ui(args),
                ],
            )
        )
        results.append(
            run_command(
                "10. 真实 Nacos 心跳超时 failover",
                [
                    py,
                    "scripts/demo_real_heartbeat_failover.py",
                    "--startup-timeout",
                    "60",
                    "--unhealthy-timeout",
                    "45",
                    *maybe_details(args),
                    *maybe_nacos_ui(args),
                ],
            )
        )
    else:
        results.append(StepResult("8-10. Nacos 增强演示", "SKIP", 0.0))

    if args.include_redis:
        if port_open("127.0.0.1", 6379):
            results.append(
                run_command(
                    "11. Redis 分布式锁测试",
                    [py, "-m", "unittest", "tests.test_distributed_agent_lock"],
                    allow_skip=True,
                )
            )
        else:
            progress("\n\n## 11. Redis 分布式锁测试")
            progress("[SKIP] 127.0.0.1:6379 不可用。当前电脑没有 Docker/Redis 服务。")
            results.append(StepResult("11. Redis 分布式锁测试", "SKIP", 0.0))

    progress("\n\n=== SHOWCASE SUMMARY ===")
    for result in results:
        progress(f"[{result.status:<4}] {result.title} ({result.seconds:.1f}s)")
    if any(result.status not in {"OK", "SKIP"} for result in results):
        raise SystemExit(1)
    progress("[DONE] Full showcase finished.")


if __name__ == "__main__":
    main()
