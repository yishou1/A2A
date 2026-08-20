"""AMOS analysis boundary with Gateway-first and direct diagnostic modes."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
import hashlib
import json
import secrets
import threading
import time
from typing import Any
import urllib.parse
import urllib.request

from amos_platform.agents.a2a.commander_client import CommanderClient
from amos_platform.agents.a2a.gateway_client import GatewayClient
from amos_platform.agents.a2a.mapper import build_commander_workflow_payload
from amos_platform.data.operational_catalog import (
    SKILL_ALGORITHM_BINDINGS,
    algorithm_classes,
    class_for_package,
    operational_functions,
)
from amos_platform.frontend_state.agent_state import build_agent_visible_state
from amos_platform.simulation.exchange_contract import chain_id_for


DEFAULT_COMMANDER_URL = "http://127.0.0.1:8021"
DEFAULT_GATEWAY_URL = "http://127.0.0.1:8030"
DEFAULT_WORKFLOW_FILE = "integrated_system/workflows/integrated_demo_workflow.bpel"
OBSERVE_WORKFLOW_FILE = "integrated_system/workflows/observe_workflow.bpel"
ORIENT_WORKFLOW_FILE = "integrated_system/workflows/orient_workflow.bpel"
DECIDE_WORKFLOW_FILE = "integrated_system/workflows/decide_workflow.bpel"
ACT_WORKFLOW_FILE = "integrated_system/workflows/act_workflow.bpel"
PHASE_WORKFLOW_FILES = {
    "FIND": OBSERVE_WORKFLOW_FILE,
    "FIX": OBSERVE_WORKFLOW_FILE,
    "TRACK": ORIENT_WORKFLOW_FILE,
    "TARGET": DECIDE_WORKFLOW_FILE,
    "ENGAGE": ACT_WORKFLOW_FILE,
    "ASSESS": ACT_WORKFLOW_FILE,
}
CHECKPOINT_WORKFLOW_FILES = {
    "MAR-CP-PERCEPTION": OBSERVE_WORKFLOW_FILE,
    "MAR-CP-ASSESS": ORIENT_WORKFLOW_FILE,
    "MAR-CP-PLAN": DECIDE_WORKFLOW_FILE,
    "MAR-CP-CLOSE": ACT_WORKFLOW_FILE,
}
ALLOWED_WORKFLOW_FILES = {
    DEFAULT_WORKFLOW_FILE,
    *PHASE_WORKFLOW_FILES.values(),
    *CHECKPOINT_WORKFLOW_FILES.values(),
}


def _configure_local_proxy_bypass() -> None:
    os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost,0.0.0.0")


class CommanderBridge:
    """Keep browser/API code independent from the selected A2A transport.

    Gateway is the deployment boundary.  Direct Commander access remains an
    explicit diagnostic mode for integration troubleshooting only.
    """

    def __init__(
        self,
        commander_url: str | None = None,
        gateway_url: str | None = None,
        mode: str | None = None,
    ) -> None:
        requested_mode = (mode or os.environ.get("A2A_BACKEND_MODE", "gateway")).strip().lower()
        if requested_mode not in {"gateway", "commander"}:
            raise ValueError("A2A_BACKEND_MODE must be 'gateway' or 'commander'")
        self.mode = requested_mode
        self.commander_url = commander_url or os.environ.get(
            "A2A_COMMANDER_URL", DEFAULT_COMMANDER_URL
        )
        self.gateway_url = gateway_url or os.environ.get(
            "A2A_GATEWAY_URL", DEFAULT_GATEWAY_URL
        )
        self._client: CommanderClient | GatewayClient | None = None
        self._algorithm_catalog_cache: dict[str, Any] | None = None
        self._algorithm_catalog_expires_at = 0.0
        self._algorithm_catalog_lock = threading.Lock()

    @property
    def backend_url(self) -> str:
        return self.gateway_url if self.mode == "gateway" else self.commander_url

    @property
    def client(self) -> CommanderClient | GatewayClient:
        _configure_local_proxy_bypass()
        if self._client is None:
            timeout = float(os.environ.get("A2A_REQUEST_TIMEOUT", "30"))
            if self.mode == "gateway":
                self._client = GatewayClient(
                    base_url=self.gateway_url,
                    token=os.environ.get("A2A_GATEWAY_TOKEN", ""),
                    timeout_sec=timeout,
                )
            else:
                self._client = CommanderClient(
                    base_url=self.commander_url,
                    token=os.environ.get("A2A_COMMANDER_TOKEN", ""),
                    timeout_sec=int(timeout),
                )
        return self._client

    @property
    def commander_client(self) -> CommanderClient:
        """Compatibility accessor; unavailable while Gateway mode is active."""
        _configure_local_proxy_bypass()
        if self.mode != "commander":
            raise RuntimeError("direct Commander client is disabled in Gateway mode")
        return self.client  # type: ignore[return-value]

    def health(self) -> dict[str, Any]:
        result = self.client.health()
        upstream_status = str(result.get("status") or "")
        if self.mode == "gateway" and upstream_status == "degraded":
            status = "degraded"
        elif result.get("error"):
            status = "offline"
        else:
            status = upstream_status or "ok"
        return {
            "status": status,
            "transport": self.mode,
            "endpoint": self.backend_url,
            "diagnostic_mode": self.mode == "commander",
            "upstream": result,
        }

    @staticmethod
    def _fetch_json(url: str, timeout: float = 2.0) -> dict[str, Any]:
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _runtime_is_ready(payload: dict[str, Any]) -> bool:
        status = str(payload.get("status") or "").lower()
        return payload.get("ok") is not False and status in {"ok", "ready", "healthy"}

    @staticmethod
    def _health_endpoint_from_predict_endpoint(url: str) -> str:
        endpoint = str(url or "").strip()
        if not endpoint:
            return ""
        parsed = urllib.parse.urlparse(endpoint)
        if not parsed.scheme or not parsed.netloc:
            return ""
        path = parsed.path.rstrip("/")
        if path.endswith("/predict"):
            path = path[: -len("/predict")] + "/health"
        else:
            path = path + "/health"
        return urllib.parse.urlunparse(parsed._replace(path=path, params="", query="", fragment=""))

    @classmethod
    def _runtime_health_endpoint(cls, item: dict[str, Any], detail: dict[str, Any]) -> str:
        entry = detail.get("entry") if isinstance(detail.get("entry"), dict) else {}
        card = entry.get("card") if isinstance(entry.get("card"), dict) else {}
        machine = card.get("machine_spec") if isinstance(card.get("machine_spec"), dict) else {}
        runtime = machine.get("runtime") if isinstance(machine.get("runtime"), dict) else {}
        health_endpoint = str(
            runtime.get("health_endpoint")
            or detail.get("health_endpoint")
            or item.get("health_endpoint")
            or ""
        ).strip()
        if health_endpoint:
            return health_endpoint
        return cls._health_endpoint_from_predict_endpoint(
            str(detail.get("predict_endpoint") or item.get("predict_endpoint") or "")
        )

    def algorithm_catalog(self, *, force: bool = False) -> dict[str, Any]:
        """Return active AlgoLib entries whose configured runtime is healthy."""
        now = time.monotonic()
        with self._algorithm_catalog_lock:
            if (
                not force
                and self._algorithm_catalog_cache is not None
                and now < self._algorithm_catalog_expires_at
            ):
                return json.loads(json.dumps(self._algorithm_catalog_cache))

            base_url = os.environ.get("ALGOLIB_BASE_URL", "http://127.0.0.1:8088").rstrip("/")
            checked_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            try:
                library_health = self._fetch_json(f"{base_url}/health")
                listing = self._fetch_json(f"{base_url}/algorithms?active_only=true")
                active = [item for item in listing.get("algorithms") or [] if isinstance(item, dict)]
            except Exception as exc:
                result = {
                    "status": "offline",
                    "source": "algolib-runtime",
                    "checked_at": checked_at,
                    "active_count": 0,
                    "runnable_count": 0,
                    "unavailable_count": 0,
                    "algorithms": [],
                    "algorithm_class_count": 20,
                    "algorithm_package_count": 0,
                    "algorithm_classes": algorithm_classes(onnx_runtime_available=False),
                    "operational_functions": operational_functions(),
                    "skill_bindings": SKILL_ALGORITHM_BINDINGS,
                    "onnx_runtime_available": False,
                    "onnx_runtime_note": "AlgoLib 不可达，无法验证 ONNX Runtime。",
                    "error": str(exc),
                }
                self._algorithm_catalog_cache = result
                self._algorithm_catalog_expires_at = now + 5.0
                return json.loads(json.dumps(result))

            def inspect_runtime(item: dict[str, Any]) -> dict[str, Any]:
                algorithm_id = str(item.get("algorithm_id") or "")
                version = str(item.get("version") or "")
                backend_type = str(item.get("backend_type") or "")
                runtime_status = "unavailable"
                health_payload: dict[str, Any] = {}
                model_loaded = None
                detail: dict[str, Any] = {}
                try:
                    detail_url = "/".join([
                        f"{base_url}/algorithms",
                        urllib.parse.quote(algorithm_id, safe=""),
                        urllib.parse.quote(version, safe=""),
                        urllib.parse.quote(backend_type, safe=""),
                    ])
                    detail = self._fetch_json(detail_url)
                    health_endpoint = self._runtime_health_endpoint(item, detail)
                    if health_endpoint:
                        health_payload = self._fetch_json(health_endpoint, timeout=1.5)
                        runtime_status = "ready" if self._runtime_is_ready(health_payload) else "unavailable"
                        model_loaded = health_payload.get("model_loaded")
                except Exception as exc:
                    health_payload = {"detail": str(exc)}
                entry = detail.get("entry") if isinstance(detail.get("entry"), dict) else {}
                card = entry.get("card") if isinstance(entry.get("card"), dict) else {}
                package_class = class_for_package(algorithm_id)
                onnx_package = algorithm_id.endswith("_onnx")
                return {
                    "algorithm_id": algorithm_id,
                    "display_name": item.get("display_name") or algorithm_id,
                    "version": version,
                    "backend_type": backend_type,
                    "task_family": item.get("task_family"),
                    "capabilities": list(item.get("capabilities") or []),
                    "summary": item.get("summary"),
                    "agent_card": item.get("agent_card") if isinstance(item.get("agent_card"), dict) else {},
                    "model_profile": item.get("model_profile") if isinstance(item.get("model_profile"), dict) else {},
                    "predict_endpoint": item.get("predict_endpoint"),
                    "runtime_status": runtime_status,
                    "model_loaded": model_loaded,
                    "health_status": health_payload.get("status"),
                    "operational_functions": list(card.get("operational_functions") or entry.get("operational_functions") or []),
                    "algorithm_class_id": package_class.get("algorithm_class_id") if package_class else None,
                    "algorithm_class_name": package_class.get("name") if package_class else None,
                    "onnx_model_provided": onnx_package,
                    "onnx_runtime_available": False,
                }

            worker_count = max(1, min(8, len(active)))
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                algorithms = list(executor.map(inspect_runtime, active))

            # ``onnx_text_classifier`` is a deterministic contract fixture,
            # not one of M01–M20.  It is intentionally retained in AlgoLib
            # registration but must not make the operator-facing business
            # runtime appear degraded when ONNX Runtime is disabled.
            business_algorithms = [item for item in algorithms if item["algorithm_id"] != "onnx_text_classifier"]
            runnable_count = sum(1 for item in business_algorithms if item["runtime_status"] == "ready")
            result = {
                "status": "ready" if self._runtime_is_ready(library_health) and runnable_count == len(business_algorithms) else "degraded",
                "source": "algolib-runtime",
                "checked_at": checked_at,
                "active_count": len(active),
                "runnable_count": runnable_count,
                "unavailable_count": len(business_algorithms) - runnable_count,
                "algorithms": algorithms,
                "algorithm_class_count": 20,
                "algorithm_package_count": len(business_algorithms),
                "registered_package_count": len(algorithms),
                "contract_fixture_package_count": len(algorithms) - len(business_algorithms),
                "algorithm_classes": algorithm_classes(onnx_runtime_available=False),
                "operational_functions": operational_functions(),
                "skill_bindings": SKILL_ALGORITHM_BINDINGS,
                "onnx_runtime_available": False,
                "onnx_runtime_note": "当前 AlgoLib 构建未启用 ONNX Runtime；ONNX 标识仅表示模型包已提供。",
            }
            self._algorithm_catalog_cache = result
            self._algorithm_catalog_expires_at = now + 10.0
            return json.loads(json.dumps(result))

    @staticmethod
    def _agent_visible_state(engine: Any) -> dict[str, Any]:
        if engine is None:
            return {}
        if hasattr(engine, "get_agent_visible_state"):
            return engine.get_agent_visible_state() or {}
        return build_agent_visible_state(engine.get_state() or {})

    def build_workflow_payload(
        self,
        scenario: dict[str, Any],
        support_data: dict[str, Any] | None = None,
        engine: Any = None,
        *,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Map only observations available at the current simulation time."""
        operator_snapshot = engine.get_operator_state() if engine is not None else {}
        payload = build_commander_workflow_payload(
            scenario,
            operator_snapshot or {},
            self._agent_visible_state(engine),
            workflow_mode=os.environ.get("A2A_WORKFLOW_MODE", "bpel"),
            workflow_file=os.environ.get("A2A_WORKFLOW_FILE", DEFAULT_WORKFLOW_FILE),
            support_data=support_data,
            options=options,
        )
        mission_input = payload.get("mission_input") if isinstance(payload.get("mission_input"), dict) else {}
        stage_transfer = (
            mission_input.get("stage_transfer")
            if isinstance(mission_input.get("stage_transfer"), dict)
            else {}
        )
        phase = str(stage_transfer.get("phase") or "").upper()
        checkpoint_id = str(stage_transfer.get("checkpoint_id") or "").upper()
        payload["workflow_file"] = (
            CHECKPOINT_WORKFLOW_FILES.get(checkpoint_id)
            or PHASE_WORKFLOW_FILES.get(phase)
            or DEFAULT_WORKFLOW_FILE
        )
        return payload

    def build_backend_submission(
        self,
        mission_payload: dict[str, Any],
        *,
        engine: Any,
        overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Translate one frozen AMOS snapshot to the selected transport."""
        overrides = overrides or {}
        configured_workflow_file = str(
            mission_payload.get("workflow_file")
            or os.environ.get("A2A_WORKFLOW_FILE", DEFAULT_WORKFLOW_FILE)
        )
        if configured_workflow_file not in ALLOWED_WORKFLOW_FILES:
            raise ValueError("workflow_file is not an AMOS-managed workflow")
        llm_enabled = str(os.environ.get("ENABLE_LLM", "false")).lower() in {
            "1", "true", "yes", "on",
        }
        default_request_timeout = float(
            os.environ.get("A2A_REQUEST_TIMEOUT", "180" if llm_enabled else "60")
        )
        requested_workflow_file = overrides.get("workflow_file")
        if requested_workflow_file and requested_workflow_file != configured_workflow_file:
            raise ValueError("workflow_file must match the configured AMOS workflow")
        if self.mode == "commander":
            allowed = {
                "workflow", "workflow_file", "workflow_id", "resume", "max_steps",
                "max_workers", "max_retries", "retry_backoff", "request_timeout",
                "attachments", "task_goal",
                "required_skills", "auto_decompose",
            }
            payload = {key: value for key, value in mission_payload.items() if key in allowed}
            payload.update({key: value for key, value in overrides.items() if key in allowed})
            payload["workflow_file"] = configured_workflow_file
            return payload

        run_id = str(engine.clock.get("run_id") or "")
        scenario_id = str(engine.clock.get("scenario_id") or "")
        allowed = {
            "workflow", "workflow_file", "max_steps", "max_workers",
            "max_activity_workers", "max_agent_workers", "max_retries",
            "retry_backoff", "request_timeout",
        }
        payload: dict[str, Any] = {
            "schema_version": "amos.commander.gateway.submit.v1",
            "run_id": run_id,
            "chain_id": chain_id_for(scenario_id),
            "workflow": mission_payload.get("workflow", "bpel"),
            "workflow_file": configured_workflow_file,
            "max_steps": mission_payload.get("max_steps", 10),
            "max_workers": mission_payload.get("max_workers", 4),
            "max_retries": mission_payload.get("max_retries", 1),
            "retry_backoff": mission_payload.get("retry_backoff", 0.2),
            "request_timeout": mission_payload.get(
                "request_timeout", default_request_timeout
            ),
        }
        payload.update({key: value for key, value in overrides.items() if key in allowed})
        return {key: value for key, value in payload.items() if value is not None}

    def submit_workflow(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.mode == "gateway":
            return self.client.submit_workflow(payload)  # type: ignore[arg-type]
        return self.client.submit_workflow(**payload)  # type: ignore[arg-type]

    def get_workflow(self, workflow_id: str) -> dict[str, Any]:
        return self.client.get_workflow(workflow_id)

    def get_submission_package(
        self,
        package_id: str,
        expected_checksum: str = "",
    ) -> tuple[dict[str, Any], bool]:
        """Fetch and independently verify the exact Gateway input package."""
        if self.mode != "gateway" or not package_id:
            return {}, False
        package = self.client.get_package(package_id)  # type: ignore[union-attr]
        if package.get("error"):
            return {}, False
        encoded = json.dumps(
            package,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        actual_checksum = hashlib.sha256(encoded).hexdigest()
        verified = bool(
            expected_checksum
            and secrets.compare_digest(actual_checksum, expected_checksum)
        )
        return package, verified

    def get_work_list(self, workflow_id: str) -> dict[str, Any]:
        return self.client.get_work_list(workflow_id)

    def get_workflow_trace(self, workflow_id: str) -> dict[str, Any]:
        return self.client.get_workflow_trace(workflow_id)

    def resume_workflow(self, workflow_id: str, **options: Any) -> dict[str, Any]:
        return self.client.resume_workflow(workflow_id, **options)


__all__ = [
    "CommanderBridge",
    "DEFAULT_COMMANDER_URL",
    "DEFAULT_GATEWAY_URL",
    "DEFAULT_WORKFLOW_FILE",
    "OBSERVE_WORKFLOW_FILE",
    "ORIENT_WORKFLOW_FILE",
    "DECIDE_WORKFLOW_FILE",
    "ACT_WORKFLOW_FILE",
    "PHASE_WORKFLOW_FILES",
    "CHECKPOINT_WORKFLOW_FILES",
]
