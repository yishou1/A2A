#!/usr/bin/env python3
import argparse
import contextlib
import json
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from a2a_protocol.client import A2AClient
from registry.nacos_manager import NacosRegistry
from langchain_openai import ChatOpenAI


DEFAULT_PHASES = [
    (
        "recon",
        {
            "command": "scan_beach_defenses",
            "sector": "Sector_A",
        },
    ),
    (
        "artillery",
        {
            "command": "suppress_beach_sector_A",
            "coordinates": "120.5E, 35.1N",
            "intensity": "high",
        },
    ),
    (
        "evaluator",
        {
            "command": "evaluate_strike",
            "target_coordinates": "120.5E, 35.1N",
        },
    ),
    (
        "assault",
        {
            "command": "capture_beachhead",
            "coordinates": "120.5E, 35.1N",
        },
    ),
]


class Timer:
    def measure(self, fn: Callable[[], Any]) -> tuple[Any, float]:
        start = time.perf_counter()
        result = fn()
        return result, time.perf_counter() - start


def ms(seconds: Optional[float]) -> Optional[float]:
    if seconds is None:
        return None
    return round(seconds * 1000, 3)


def percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((len(ordered) - 1) * pct))
    return ordered[index]


def load_env_file(path: Path = PROJECT_ROOT / ".env") -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


class A2ATimingProbe:
    def __init__(self, nacos_addr: str, service_name: str, llm_route: bool = False):
        self.registry = NacosRegistry(server_addresses=nacos_addr)
        self.service_name = service_name
        self.timer = Timer()
        self.llm_route = llm_route
        self.llm = self._build_llm() if llm_route else None

    def run_phase(self, role: str, payload: Dict[str, Any], stream: bool) -> Dict[str, Any]:
        phase_started = time.perf_counter()
        result: Dict[str, Any] = {
            "role": role,
            "resolved_role": role,
            "ok": False,
            "target": None,
            "events": [],
            "timings_ms": {
                "llm_route_decision": None,
                "route_discovery": None,
                "agent_card_discovery": None,
                "authentication": None,
                "task_submit_ack": None,
                "task_time_to_first_event": None,
                "task_stream_completion": None,
                "phase_total": None,
            },
            "error": None,
        }

        try:
            resolved_role = role
            if self.llm_route:
                resolved_role, llm_route_seconds = self.timer.measure(
                    lambda: self.resolve_role_with_llm(role, payload)
                )
                result["resolved_role"] = resolved_role
                result["timings_ms"]["llm_route_decision"] = ms(llm_route_seconds)

            instances, route_seconds = self.timer.measure(
                lambda: self.registry.discover_service(
                    self.service_name, {"role": resolved_role, "status": "idle"}
                )
            )
            result["timings_ms"]["route_discovery"] = ms(route_seconds)

            if not instances:
                raise RuntimeError(f"No healthy idle agent found for role '{resolved_role}'")

            target = instances[0]
            ip = target.get("ip")
            port = target.get("port")
            result["target"] = {
                "ip": ip,
                "port": port,
                "metadata": target.get("metadata", {}),
            }

            client = A2AClient(ip, port)
            _, card_seconds = self.timer.measure(client.discover)
            result["timings_ms"]["agent_card_discovery"] = ms(card_seconds)

            _, auth_seconds = self.timer.measure(client.authenticate)
            result["timings_ms"]["authentication"] = ms(auth_seconds)

            ack_payload = dict(payload)
            ack_payload["work_item"] = f"timing-{role}-ack"
            ack, ack_seconds = self.timer.measure(lambda: client.send_message(ack_payload))
            result["task_ack"] = ack
            result["timings_ms"]["task_submit_ack"] = ms(ack_seconds)

            if stream:
                stream_payload = dict(payload)
                stream_payload["work_item"] = f"timing-{role}-stream"
                stream_started = time.perf_counter()
                first_event_seconds = None
                for event_data in client.send_message_stream(stream_payload):
                    event_elapsed = time.perf_counter() - stream_started
                    if first_event_seconds is None:
                        first_event_seconds = event_elapsed
                    result["events"].append(
                        {
                            "elapsed_ms": ms(event_elapsed),
                            "data": self._parse_event(event_data),
                        }
                    )

                result["timings_ms"]["task_time_to_first_event"] = ms(first_event_seconds)
                result["timings_ms"]["task_stream_completion"] = ms(
                    time.perf_counter() - stream_started
                )

            result["ok"] = True
        except Exception as exc:
            result["error"] = str(exc)
        finally:
            result["timings_ms"]["phase_total"] = ms(time.perf_counter() - phase_started)

        return result

    @staticmethod
    def _parse_event(event_data: str) -> Any:
        try:
            return json.loads(event_data)
        except json.JSONDecodeError:
            return event_data

    def _build_llm(self):
        load_env_file()
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is required for --llm-route")

        kwargs = {
            "api_key": api_key,
            "model": os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
            "temperature": 0,
        }
        api_base = os.environ.get("OPENAI_API_BASE", "")
        if api_base:
            kwargs["base_url"] = api_base
        return ChatOpenAI(**kwargs)

    def resolve_role_with_llm(self, expected_role: str, payload: Dict[str, Any]) -> str:
        prompt = (
            "You are an A2A routing controller. Choose exactly one target role "
            "for the task. Valid roles are: recon, artillery, evaluator, assault.\n"
            "Return only the role name, no punctuation, no explanation.\n\n"
            f"Expected benchmark role: {expected_role}\n"
            f"Task payload JSON: {json.dumps(payload, ensure_ascii=False)}"
        )
        response = self.llm.invoke(prompt)
        content = getattr(response, "content", str(response)).strip().lower()
        for role in ["recon", "artillery", "evaluator", "assault"]:
            if role in content:
                return role
        raise RuntimeError(f"LLM returned an invalid route role: {content!r}")


def summarize(iteration_results: List[List[Dict[str, Any]]]) -> Dict[str, Any]:
    by_role: Dict[str, Dict[str, List[float]]] = {}
    for run in iteration_results:
        for phase in run:
            role = phase["role"]
            by_role.setdefault(role, {})
            for key, value in phase["timings_ms"].items():
                if value is None:
                    continue
                by_role[role].setdefault(key, []).append(float(value))

    summary: Dict[str, Any] = {}
    for role, timing_groups in by_role.items():
        summary[role] = {}
        for key, values in timing_groups.items():
            summary[role][key] = {
                "avg_ms": round(statistics.mean(values), 3),
                "min_ms": round(min(values), 3),
                "p50_ms": round(percentile(values, 0.50), 3),
                "p95_ms": round(percentile(values, 0.95), 3),
                "max_ms": round(max(values), 3),
            }
    return summary


def print_table(results: List[Dict[str, Any]]) -> None:
    headers = [
        "role",
        "resolved",
        "target",
        "llm_route",
        "route",
        "card",
        "auth",
        "ack",
        "first_event",
        "complete",
        "phase_total",
    ]
    rows = []
    for phase in results:
        target = phase.get("target") or {}
        target_label = "-"
        if target:
            target_label = f"{target.get('ip')}:{target.get('port')}"
        timings = phase["timings_ms"]
        rows.append(
            [
                phase["role"],
                phase.get("resolved_role", phase["role"]),
                target_label,
                timings["llm_route_decision"],
                timings["route_discovery"],
                timings["agent_card_discovery"],
                timings["authentication"],
                timings["task_submit_ack"],
                timings["task_time_to_first_event"],
                timings["task_stream_completion"],
                timings["phase_total"],
            ]
        )

    widths = [
        max(len(str(item)) for item in [header] + [row[index] for row in rows])
        for index, header in enumerate(headers)
    ]
    print(" | ".join(header.ljust(widths[index]) for index, header in enumerate(headers)))
    print("-+-".join("-" * width for width in widths))
    for row in rows:
        print(" | ".join(str(item).ljust(widths[index]) for index, item in enumerate(row)))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Measure A2A route discovery, agent calls, task completion and total scenario time."
    )
    parser.add_argument("--nacos", default="127.0.0.1:8848", help="Nacos server address")
    parser.add_argument("--service", default="A2A-Agent", help="Nacos service name")
    parser.add_argument(
        "--roles",
        default="recon,artillery,evaluator",
        help="Comma-separated roles to test. Available: recon, artillery, evaluator, assault",
    )
    parser.add_argument("--iterations", type=int, default=1, help="Number of test runs")
    parser.add_argument(
        "--no-stream",
        action="store_true",
        help="Only measure routing, discovery, authentication and /sendMessage ack",
    )
    parser.add_argument(
        "--llm-route",
        action="store_true",
        help="Use the configured LLM to choose the target role before Nacos discovery",
    )
    parser.add_argument("--json", action="store_true", help="Print raw JSON result")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payloads = dict(DEFAULT_PHASES)
    roles = [role.strip() for role in args.roles.split(",") if role.strip()]
    unknown_roles = [role for role in roles if role not in payloads]
    if unknown_roles:
        print(f"Unknown roles: {', '.join(unknown_roles)}", file=sys.stderr)
        return 2

    probe = A2ATimingProbe(
        nacos_addr=args.nacos,
        service_name=args.service,
        llm_route=args.llm_route,
    )
    all_runs: List[List[Dict[str, Any]]] = []
    total_started = time.perf_counter()

    for index in range(args.iterations):
        if not args.json:
            print(f"\n=== Timing iteration {index + 1}/{args.iterations} ===")
        if args.json:
            with contextlib.redirect_stdout(sys.stderr):
                run_results = [
                    probe.run_phase(role, payloads[role], stream=not args.no_stream)
                    for role in roles
                ]
        else:
            run_results = [
                probe.run_phase(role, payloads[role], stream=not args.no_stream)
                for role in roles
            ]
        all_runs.append(run_results)
        if not args.json:
            print_table(run_results)
            errors = [phase for phase in run_results if phase["error"]]
            for phase in errors:
                print(f"[{phase['role']}] ERROR: {phase['error']}")

    total_ms = ms(time.perf_counter() - total_started)
    output = {
        "iterations": args.iterations,
        "roles": roles,
        "llm_route_enabled": args.llm_route,
        "stream_enabled": not args.no_stream,
        "total_elapsed_ms": total_ms,
        "runs": all_runs,
        "summary": summarize(all_runs),
    }

    if args.json:
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print("\n=== Summary ===")
        print(json.dumps(output["summary"], ensure_ascii=False, indent=2))
        print(f"\nTotal elapsed: {total_ms} ms")

    return 1 if any(phase["error"] for run in all_runs for phase in run) else 0


if __name__ == "__main__":
    raise SystemExit(main())
