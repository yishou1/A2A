import sys
import os
import time
import re
import argparse
import threading
import queue
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from copy import deepcopy

# Ensure imports work from project root
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from registry.nacos_manager import NacosRegistry
from a2a_protocol.client import A2AClient
from a2a_protocol.messages import build_task_response
from bpel_workflow import BPELActivatity, BPELWorkflowCatalog
from commander_agent.agent_leases import AgentLeaseManager
from commander_agent.circuit_breaker import AgentCircuitBreaker
from commander_agent.distributed_lock import RedisDistributedLock
from commander_agent.error_classification import classify_agent_error
from local_runtime import LocalAgentRuntime
from observability import append_trace, exception_diagnostics, log_event
from workflow_state_store import WorkflowStateStore, new_workflow_id, utc_now_iso
from workflow_payloads import attachment_snapshot, merge_attachments, normalize_attachments
from closed_loop_agent.agent_results_mapping import build_standard_results_from_context
import json

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def load_env_file(path=os.path.join(PROJECT_ROOT, ".env")):
    if not os.path.exists(path):
        return

    with open(path, "r", encoding="utf-8") as env_file:
        for line in env_file:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value

class CommanderAgent:
    def __init__(
        self,
        mode: str = None,
        workflow: str = "dynamic",
        workflow_file: str = None,
        workflow_id: str = None,
        state_dir: str = None,
        resume: bool = False,
        mock_eval_score: int = None,
        mock_decision: str = None,
        max_workers: int = None,
        max_activity_workers: int = None,
        max_agent_workers: int = None,
        max_retries: int = None,
        retry_backoff: float = None,
        request_timeout: float = None,
        registry=None,
        lease_manager=None,
    ):
        load_env_file()
        self.mode = (mode or os.environ.get("A2A_COMMANDER_MODE", "remote")).lower()
        if self.mode not in {"remote", "local"}:
            raise ValueError("mode must be either 'remote' or 'local'")

        self.workflow = workflow
        self.workflow_file = workflow_file
        self.workflow_id = workflow_id or os.environ.get("A2A_WORKFLOW_ID") or new_workflow_id()
        legacy_max_workers = int(max_workers if max_workers is not None else os.environ.get("A2A_MAX_WORKERS", "4"))
        self.max_activity_workers = max(
            1,
            int(
                max_activity_workers
                if max_activity_workers is not None
                else os.environ.get("A2A_MAX_ACTIVITY_WORKERS", legacy_max_workers)
            ),
        )
        self.max_agent_workers = max(
            1,
            int(
                max_agent_workers
                if max_agent_workers is not None
                else os.environ.get("A2A_MAX_AGENT_WORKERS", legacy_max_workers)
            ),
        )
        # Backward-compatible alias for older tests/scripts that still inspect max_workers.
        self.max_workers = self.max_activity_workers
        self.max_retries = max(0, int(max_retries if max_retries is not None else os.environ.get("A2A_MAX_RETRIES", "1")))
        self.retry_backoff = float(retry_backoff if retry_backoff is not None else os.environ.get("A2A_RETRY_BACKOFF", "0.2"))
        self.request_timeout = float(request_timeout if request_timeout is not None else os.environ.get("A2A_REQUEST_TIMEOUT", "5"))
        self.lease_heartbeat_check_interval = float(os.environ.get("A2A_LEASE_HEARTBEAT_CHECK_INTERVAL", "1"))
        self.circuit_breaker = AgentCircuitBreaker(
            failure_threshold=int(os.environ.get("A2A_CIRCUIT_FAILURE_THRESHOLD", "3")),
            recovery_timeout=float(os.environ.get("A2A_CIRCUIT_RECOVERY_TIMEOUT", "30")),
        )
        self._checkpoint_lock = threading.RLock()
        self._last_task_responses = {}
        self._bpel_output_collection_writers = {}
        self.workflow_catalog = BPELWorkflowCatalog(PROJECT_ROOT)
        self.bpel_definition = None
        if self.workflow == "bpel" or self.workflow_file:
            self.bpel_definition = self.workflow_catalog.load(self.workflow_file)
            self.workflow = "bpel"
        default_state_dir = os.path.join(PROJECT_ROOT, ".a2a_state", "workflows")
        self.state_store = WorkflowStateStore(
            state_dir or os.environ.get("A2A_STATE_DIR", default_state_dir)
        )
        self.resume = resume
        self.registry = None if self.mode == "local" else (registry or NacosRegistry())
        self.lease_manager = lease_manager
        if self.mode == "remote" and self.lease_manager is None:
            self.lease_manager = AgentLeaseManager(
                self.registry,
                circuit_breaker=self.circuit_breaker,
                distributed_lock=RedisDistributedLock.from_env(),
            )
        elif self.lease_manager is not None:
            self.lease_manager.circuit_breaker = self.circuit_breaker
        self.local_runtime = LocalAgentRuntime() if self.mode == "local" else None
        self.mock_eval_score = mock_eval_score
        self.mock_decision = mock_decision
        self.api_key = os.environ.get("OPENAI_API_KEY", "")
        self.api_base = os.environ.get("OPENAI_API_BASE", "")
        self.model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        self.workflow_state = self._load_or_initialize_workflow_state()
        self.workflow_context = self.workflow_state["context"]
        print("Commander Agent online. Global Orchestration initiated.")
        print(f"Execution mode: {self.mode}")
        print(f"Workflow: {self.workflow} ({self.workflow_id})")
        if self.bpel_definition:
            print(f"BPEL definition: {self.bpel_definition.source_path}")
            print(f"Activity workers: {self.max_activity_workers}")
            print(f"Agent workers: {self.max_agent_workers}")
        print(f"Workflow state: {self.state_store.state_path(self.workflow_id)}")
        print(f"LLM model: {self.model}")
        if self.api_base:
            print(f"LLM base URL: {self.api_base}")
        self._trace(
            "commander_started",
            mode=self.mode,
            workflow=self.workflow,
            workflow_file=self.workflow_file,
            max_workers=self.max_workers,
            max_activity_workers=self.max_activity_workers,
            max_agent_workers=self.max_agent_workers,
            max_retries=self.max_retries,
        )

    def _trace(self, event_type: str, **fields):
        event = append_trace(
            self.workflow_context,
            event_type,
            workflow_id=self.workflow_id,
            **fields,
        )
        log_event(
            event_type,
            workflow_id=self.workflow_id,
            **fields,
        )
        return event

    def _remember_task_response(self, work_item: str, response: dict, *, role: str = None, target: str = None):
        if not work_item:
            return
        response_snapshot = deepcopy(response or {})
        if role is not None:
            response_snapshot.setdefault("role", role)
        if target is not None:
            response_snapshot.setdefault("target", target)
        with self._checkpoint_lock:
            self._last_task_responses[work_item] = response_snapshot
            agent_results = self.workflow_context.setdefault("agent_results", {})
            existing = agent_results.get(work_item)
            if existing:
                parallel_results = list(existing.get("parallel_results", [existing]))
                parallel_results.append(response_snapshot)
                merged = deepcopy(response_snapshot)
                merged["parallel_results"] = parallel_results
                agent_results[work_item] = merged
                self._last_task_responses[work_item] = merged
            else:
                agent_results[work_item] = response_snapshot

    def _task_response_for_context(self, context: dict):
        work_item = context.get("last_work_item")
        return self._task_response_for_work_item(work_item, context)

    def _task_response_for_work_item(self, work_item: str, context: dict = None):
        if not work_item:
            return None
        return (
            self._last_task_responses.get(work_item)
            or (context or {}).get("agent_results", {}).get(work_item)
        )

    def delegate_task(self, role_needed: str, task_payload: dict, stream: bool = False):
        print(f"\n--- STEP: Resolving next unit for role: {role_needed} ---")
        if self.mode == "local":
            return self.delegate_local_task(role_needed, task_payload, stream=stream)
        
        if self.lease_manager:
            return self._delegate_task_with_lease(role_needed, task_payload, stream=stream)

        # Compatibility path for registries without lease support.
        instances = self.registry.discover_service("A2A-Agent", {"role": role_needed, "status": "idle"})
        if not instances:
            print(f"[ERROR] No available agents found for role {role_needed}. Replanning needed!")
            return False

        last_error = None
        for index, target in enumerate(instances, start=1):
            ip = target.get("ip")
            port = target.get("port")
            print(f"[FOUND] Candidate {index}/{len(instances)} for {role_needed} at {ip}:{port}")
            success, error = self._delegate_remote_candidate(
                role_needed,
                target,
                task_payload,
                stream=stream,
            )
            if success:
                return True
            last_error = error
            if self._is_agent_unavailable_error(error):
                self._mark_agent_unavailable(target, role_needed, task_payload, error)
            print(f"[WARN] Candidate {ip}:{port} failed: {error}")
            if index < len(instances):
                error_info = classify_agent_error(error)
                self._trace(
                    "agent_failover_reassigning",
                    role=role_needed,
                    work_item=task_payload.get("work_item"),
                    failed_target=self._candidate_label(target),
                    remaining_candidates=len(instances) - index,
                    error=str(error),
                    **error_info.trace_fields(),
                )

        print(f"[ERROR] A2A communication failed after trying {len(instances)} candidates: {last_error}")
        return False

    def delegate_parallel_task(self, role_needed: str, task_payload: dict, stream: bool = False):
        print(f"\n--- PARALLEL STEP: Resolving all units for role: {role_needed} ---")
        if self.mode == "local":
            return self.delegate_local_task(role_needed, task_payload, stream=stream)

        if self.lease_manager:
            return self._delegate_parallel_task_with_lease(role_needed, task_payload, stream=stream)

        instances = self.registry.discover_service("A2A-Agent", {"role": role_needed, "status": "idle"})
        if not instances:
            print(f"[ERROR] No available agents found for parallel role {role_needed}.")
            return False

        print(f"[PARALLEL] Dispatching {len(instances)} {role_needed} instance(s).")
        with ThreadPoolExecutor(
            max_workers=min(self.max_agent_workers, len(instances)),
            thread_name_prefix=f"a2a-{role_needed}",
        ) as executor:
            futures = {
                executor.submit(
                    self._delegate_remote_candidate,
                    role_needed,
                    target,
                    task_payload,
                    stream,
                ): target
                for target in instances
            }
            results = []
            for future in as_completed(futures):
                target = futures[future]
                success, error = future.result()
                results.append((success, error, target))
                if not success:
                    if self._is_agent_unavailable_error(error):
                        self._mark_agent_unavailable(target, role_needed, task_payload, error)
                    print(f"[WARN] Parallel candidate {target.get('ip')}:{target.get('port')} failed: {error}")

        success_count = sum(1 for success, _, _ in results if success)
        blocking_failures = [
            (error, target)
            for success, error, target in results
            if not success and not self._is_agent_unavailable_error(error)
        ]
        print(f"[PARALLEL] Completed {success_count}/{len(instances)} {role_needed} assignment(s).")
        if blocking_failures:
            return False
        if success_count and success_count < len(instances):
            self._trace(
                "agent_parallel_degraded",
                role=role_needed,
                work_item=task_payload.get("work_item"),
                success_count=success_count,
                failed_count=len(instances) - success_count,
            )
        return success_count > 0

    def _delegate_task_with_lease(self, role_needed: str, task_payload: dict, stream: bool = False):
        work_item = task_payload.get("work_item", f"{self.workflow_id}:{role_needed}")
        attempted_keys = set()
        last_error = None
        while True:
            lease = self.lease_manager.acquire_one(
                role_needed,
                self.workflow_id,
                work_item,
                exclude_keys=attempted_keys,
            )
            if lease is None:
                if last_error is None:
                    print(f"[ERROR] No available agents found for role {role_needed}. Replanning needed!")
                else:
                    print(f"[ERROR] A2A communication failed after trying {len(attempted_keys)} candidates: {last_error}")
                return False

            target = lease.target
            attempted_keys.add(lease.instance_key)
            label = self._candidate_label(target)
            print(f"[LEASE] {self.workflow_id} acquired {role_needed} at {label}")
            success, error = self._delegate_leased_candidate(
                lease,
                role_needed,
                task_payload,
                stream=stream,
            )

            if success:
                return True
            last_error = error
            print(f"[WARN] Candidate {label} failed: {error}")
            error_info = classify_agent_error(error)
            self._trace(
                "agent_failover_reassigning",
                role=role_needed,
                work_item=work_item,
                failed_target=label,
                error=str(error),
                **error_info.trace_fields(),
            )

    def _delegate_parallel_task_with_lease(self, role_needed: str, task_payload: dict, stream: bool = False):
        work_item = task_payload.get("work_item", f"{self.workflow_id}:{role_needed}")
        leases = self.lease_manager.acquire_all(role_needed, self.workflow_id, work_item)
        if not leases:
            print(f"[ERROR] No available agents found for parallel role {role_needed}.")
            return False

        print(f"[PARALLEL] Dispatching {len(leases)} leased {role_needed} instance(s).")
        with ThreadPoolExecutor(
            max_workers=min(self.max_agent_workers, len(leases)),
            thread_name_prefix=f"a2a-{role_needed}",
        ) as executor:
            futures = {
                executor.submit(
                    self._delegate_leased_candidate,
                    lease,
                    role_needed,
                    task_payload,
                    stream,
                ): lease.target
                for lease in leases
            }
            results = []
            for future in as_completed(futures):
                target = futures[future]
                success, error = future.result()
                results.append((success, error, target))
                if not success:
                    print(f"[WARN] Parallel candidate {self._candidate_label(target)} failed: {error}")

        success_count = sum(1 for success, _, _ in results if success)
        blocking_failures = [
            (error, target)
            for success, error, target in results
            if not success and not self._is_agent_unavailable_error(error)
        ]
        print(f"[PARALLEL] Completed {success_count}/{len(leases)} leased {role_needed} assignment(s).")
        if blocking_failures:
            return False
        if success_count and success_count < len(leases):
            self._trace(
                "agent_parallel_degraded",
                role=role_needed,
                work_item=work_item,
                success_count=success_count,
                failed_count=len(leases) - success_count,
            )
        return success_count > 0

    def _delegate_leased_candidate(self, lease, role_needed: str, task_payload: dict, stream: bool = False):
        if self.lease_heartbeat_check_interval <= 0:
            success, error = self._delegate_remote_candidate(
                role_needed,
                lease.target,
                task_payload,
                stream=stream,
                lease=lease,
            )
            self._release_agent_lease(
                lease,
                available=success or not self._is_agent_unavailable_error(error),
                error=error,
            )
            return success, error

        result_queue = queue.Queue(maxsize=1)

        def run_candidate():
            try:
                result = self._delegate_remote_candidate(
                    role_needed,
                    lease.target,
                    task_payload,
                    stream=stream,
                    lease=lease,
                )
            except Exception as exc:
                result = (False, exc)
            try:
                result_queue.put_nowait(result)
            except queue.Full:
                pass

        thread = threading.Thread(
            target=run_candidate,
            name=f"a2a-lease-call-{lease.instance_key}",
            daemon=True,
        )
        thread.start()

        while True:
            try:
                success, error = result_queue.get(
                    timeout=self.lease_heartbeat_check_interval
                )
                self._release_agent_lease(
                    lease,
                    available=success or not self._is_agent_unavailable_error(error),
                    error=error,
                )
                return success, error
            except queue.Empty:
                if not self.lease_manager.is_current(lease):
                    error = RuntimeError(f"Lease no longer active for {lease.instance_key}")
                    return False, error
                if not self.lease_manager.is_lease_fresh(lease):
                    error = RuntimeError(f"heartbeat lost for {lease.instance_key}")
                    error_info = classify_agent_error(error)
                    self._trace(
                        "agent_heartbeat_lost",
                        role=role_needed,
                        work_item=lease.work_item,
                        target=lease.instance_key,
                        check_interval=self.lease_heartbeat_check_interval,
                        **error_info.trace_fields(),
                    )
                    self._release_agent_lease(
                        lease,
                        available=False,
                        error=error,
                    )
                    return False, error

    def _release_agent_lease(self, lease, *, available: bool = True, error=None):
        try:
            if available:
                previous_state = self.circuit_breaker.snapshot(lease.instance_key)["state"]
                self.circuit_breaker.record_success(lease.instance_key)
                self.lease_manager.release(
                    lease,
                    metadata_updates={"circuit_state": "closed", "circuit_failure_count": 0},
                    remove_keys=["circuit_opened_at_ts", "circuit_open_until_ts"],
                )
                if previous_state in {"open", "half_open"}:
                    self._trace(
                        "agent_circuit_closed",
                        role=lease.role,
                        work_item=lease.work_item,
                        target=lease.instance_key,
                    )
                print(f"[LEASE] Released {lease.role} at {lease.instance_key}")
                return
            error_info = classify_agent_error(error)
            circuit = self.circuit_breaker.record_failure(lease.instance_key)
            circuit_metadata = self.circuit_breaker.metadata(lease.instance_key)
            circuit_open = circuit["state"] == "open"
            self.lease_manager.release(
                lease,
                status="unavailable" if circuit_open else "idle",
                metadata_updates={
                    **circuit_metadata,
                    "unavailable_workflow_id": lease.workflow_id,
                    "unavailable_work_item": lease.work_item,
                    "unavailable_at": utc_now_iso(),
                    "unavailable_reason": str(error),
                    "unavailable_error_code": error_info.code,
                    "unavailable_error_category": error_info.category,
                },
            )
            self._trace(
                "agent_circuit_opened" if circuit_open else "agent_failure_recorded",
                role=lease.role,
                work_item=lease.work_item,
                target=lease.instance_key,
                error=str(error),
                circuit_state=circuit["state"],
                circuit_failure_count=circuit["failure_count"],
                circuit_open_until_ts=circuit["open_until_ts"],
                **error_info.trace_fields(),
            )
            state_label = "circuit open" if circuit_open else "failure recorded"
            print(f"[LEASE] Released {lease.role} at {lease.instance_key}; {state_label}")
        except Exception as exc:
            print(f"[WARN] Failed to mirror lease release for {lease.instance_key}: {exc}")

    @staticmethod
    def _candidate_label(target: dict):
        return f"{target.get('ip')}:{target.get('port')}"

    @staticmethod
    def _is_agent_unavailable_error(error) -> bool:
        return classify_agent_error(error).failover

    def _mark_agent_unavailable(self, target: dict, role: str, task_payload: dict, error) -> None:
        if self.registry is None:
            return
        label = self._candidate_label(target)
        error_info = classify_agent_error(error)
        try:
            self.registry.update_instance_metadata(
                "A2A-Agent",
                target,
                metadata_updates={
                    "status": "unavailable",
                    "unavailable_workflow_id": self.workflow_id,
                    "unavailable_work_item": task_payload.get("work_item"),
                    "unavailable_at": utc_now_iso(),
                    "unavailable_reason": str(error),
                    "unavailable_error_code": error_info.code,
                    "unavailable_error_category": error_info.category,
                },
                remove_keys=[
                    "lease_workflow_id",
                    "lease_work_item",
                    "lease_acquired_at",
                ],
            )
            self._trace(
                "agent_marked_unavailable",
                role=role,
                work_item=task_payload.get("work_item"),
                target=label,
                error=str(error),
                **error_info.trace_fields(),
            )
        except Exception as exc:
            print(f"[WARN] Failed to mark unavailable agent {label}: {exc}")

    def _lease_allows_response(self, lease, target_label: str, work_item: str, role: str) -> bool:
        if lease is None or self.lease_manager is None:
            return True
        if self.lease_manager.is_current(lease) and self.lease_manager.is_lease_fresh(lease):
            return True
        error_info = classify_agent_error("late response ignored after failover")
        self._trace(
            "agent_late_response_ignored",
            role=role,
            work_item=work_item,
            target=target_label,
            lease_instance=lease.instance_key,
            **error_info.trace_fields(),
        )
        return False

    def _delegate_remote_candidate(self, role_needed: str, target: dict, task_payload: dict, stream: bool = False, lease=None):
        ip = target.get("ip")
        port = target.get("port")
        label = self._candidate_label(target)
        work_item = task_payload.get("work_item", f"{self.workflow_id}:{role_needed}")
        retry_policy = task_payload.get("retry_policy", {}) or {}
        max_retries = int(retry_policy.get("max_retries", self.max_retries))
        timeout = float(retry_policy.get("timeout_seconds") or self.request_timeout)
        last_error = None

        for attempt in range(1, max_retries + 2):
            client = A2AClient(ip, port, timeout=timeout)
            call_started = time.perf_counter()
            try:
                self._trace(
                    "agent_call_attempt",
                    role=role_needed,
                    work_item=work_item,
                    target=label,
                    attempt=attempt,
                    max_retries=max_retries,
                )
                card = client.discover()
                print(f"[DISCOVERY] {label} Agent Card from '{card.get('name')}'")

                token = client.authenticate()
                print(f"[AUTH] {label} JWT Token: {token[:10]}...")

                if stream:
                    print(f"[STREAM] {label} receiving '{role_needed}' updates:")
                    events = []
                    for event_data in client.send_message_stream(task_payload):
                        data = json.loads(event_data)
                        events.append(data)
                        print(
                            f"   -> [{label}] [{data.get('status')}] "
                            f"{data.get('progress', '')} {data.get('message', '')}"
                        )
                    response = build_task_response(
                        workflow_id=task_payload.get("workflow_id"),
                        work_item=work_item,
                        agent=card.get("name", label),
                        role=role_needed,
                        command=task_payload.get("command"),
                        status="completed" if events and str(events[-1].get("status", "")).lower() == "completed" else "accepted",
                        output={},
                        metrics={
                            "stream_events": len(events),
                            "duration_ms": round((time.perf_counter() - call_started) * 1000, 3),
                        },
                        message=events[-1].get("message", "") if events else "",
                        extra={"stream_events": events, "target": label},
                    )
                    if not self._lease_allows_response(lease, label, work_item, role_needed):
                        return False, RuntimeError(f"late response ignored after failover: {label}")
                    self._remember_task_response(work_item, response, role=role_needed, target=label)
                    self._trace("agent_call_completed", role=role_needed, work_item=work_item, target=label, attempt=attempt)
                    return True, None

                res = client.send_message(task_payload)
                if not self._lease_allows_response(lease, label, work_item, role_needed):
                    return False, RuntimeError(f"late response ignored after failover: {label}")
                metrics = res.setdefault("metrics", {})
                metrics.setdefault("duration_ms", round((time.perf_counter() - call_started) * 1000, 3))
                self._remember_task_response(work_item, res, role=role_needed, target=label)
                print(f"[SEND] {label} Task Response: {res}")
                self._trace("agent_call_completed", role=role_needed, work_item=work_item, target=label, attempt=attempt)
                return True, None
            except Exception as exc:
                last_error = exc
                error_info = classify_agent_error(exc)
                self._trace(
                    "agent_call_failed",
                    role=role_needed,
                    work_item=work_item,
                    target=label,
                    attempt=attempt,
                    **exception_diagnostics(exc),
                    **error_info.trace_fields(),
                )
                if attempt <= max_retries:
                    time.sleep(self.retry_backoff * attempt)

        return False, last_error

    def delegate_local_task(self, role_needed: str, task_payload: dict, stream: bool = False):
        try:
            call_started = time.perf_counter()
            response, events = self.local_runtime.execute(role_needed, task_payload, stream=stream)
            metrics = response.setdefault("metrics", {})
            metrics.setdefault("duration_ms", round((time.perf_counter() - call_started) * 1000, 3))
            self._remember_task_response(task_payload.get("work_item"), response, role=role_needed, target="local")
            card = response.get("agent_card", {})
            print(f"[LOCAL DISCOVERY] Using local Agent Card from '{card.get('name')}'")
            print(f"[LOCAL AUTH] Obtained local token: {response.get('token')}")

            if stream:
                print(f"[LOCAL STREAM] Receiving task updates from '{role_needed}':")
                for data in events:
                    print(f"   -> [{data.get('status')}] {data.get('progress', '')} {data.get('message', '')}")
            else:
                print(f"[LOCAL SEND] Task Response: {response}")
            self._trace(
                "local_agent_call_completed",
                role=role_needed,
                work_item=task_payload.get("work_item"),
                stream=stream,
            )
            return True
        except Exception as e:
            print(f"[ERROR] Local task execution failed: {e}")
            self._trace(
                "local_agent_call_failed",
                role=role_needed,
                work_item=task_payload.get("work_item"),
                **exception_diagnostics(e),
            )
            return False

    def ask_llm(self, battle_log: list):
        log_str = "\n".join(battle_log)
        if self.mock_decision:
            return f"MOCK_LOCAL_DECISION: {self.mock_decision}"

        if self.mode == "local" or not self.api_key:
            score = self.mock_eval_score if self.mock_eval_score is not None else 40
            if score >= 60:
                return (
                    f"MOCK_LLM_DECISION: Destroy rate is {score}%. "
                    "ASSAULT. Beachhead defenses are sufficiently suppressed. "
                    "(Local/mock decision)"
                )
            return (
                f"MOCK_LLM_DECISION: Destroy rate is {score}%. "
                "The beachhead defenses are too strong. ABORT ASSAULT. "
                "Initiate RE-PLAN and call in bomber support. "
                "(Local/mock decision)"
            )
            
        try:
            from langchain_openai import ChatOpenAI
            from langchain_core.prompts import PromptTemplate

            llm_kwargs = {
                "api_key": self.api_key,
                "model": self.model,
            }
            if self.api_base:
                llm_kwargs["base_url"] = self.api_base

            llm = ChatOpenAI(**llm_kwargs)
            prompt = PromptTemplate.from_template("You are a battlefield commander AI analyzing report:\n{logs}\nAs an AI Commander, briefly decide whether to 'ASSAULT' or 'RE-PLAN'. Reason succinctly in one sentence.")
            chain = prompt | llm
            res = chain.invoke({"logs": log_str})
            return res.content
        except Exception as e:
            return f"LLM_ERROR: {str(e)}"

    def build_llm(self):
        if not self.api_key:
            return None

        from langchain_openai import ChatOpenAI

        llm_kwargs = {
            "api_key": self.api_key,
            "model": self.model,
        }
        if self.api_base:
            llm_kwargs["base_url"] = self.api_base
        return ChatOpenAI(**llm_kwargs)

    def initial_workflow_context(self):
        return {
            "workflow_id": self.workflow_id,
            "workflow_mode": self.mode,
            "workflow_name": self.workflow,
            "workflow_status": "running",
            "workflow_activatity": 0,
            "workflow_activity": 0,
            "current_activatity": None,
            "current_activity": None,
            "active_activatities": [],
            "active_activities": [],
            "last_work_item": None,
            "last_role": None,
            "last_error": None,
            "sector": "Sector_A",
            "coordinates": "120.5E, 35.1N",
            "recon_report": [],
            "strike_result": [],
            "eval_score": [],
            "commander_decision": [],
            "assault_result": [],
            "closed_loop_result": [],
            "replan_result": [],
            "battle_log": [],
            "completed_roles": [],
            "attachments": [],
            "agent_results": {},
            "trace": [],
            "work_list": self._initial_work_list(),
        }

    def _initial_work_list(self):
        if not self.bpel_definition:
            return []
        return self.bpel_definition.initial_work_list(self.workflow_id)

    @staticmethod
    def _migrate_legacy_context(context: dict):
        migrated = dict(context or {})
        legacy_map = {
            "workflow_step": "workflow_activatity",
            "workflow_activity": "workflow_activatity",
            "current_step": "current_activatity",
            "current_activity": "current_activatity",
            "active_activities": "active_activatities",
            "last_task_id": "last_work_item",
        }
        for legacy_key, new_key in legacy_map.items():
            if new_key not in migrated and legacy_key in migrated:
                migrated[new_key] = migrated[legacy_key]
            if legacy_key in {"workflow_step", "current_step", "last_task_id"}:
                migrated.pop(legacy_key, None)

        current_activatity = migrated.get("current_activatity")
        if isinstance(current_activatity, dict):
            current_activatity = dict(current_activatity)
            if "index" in current_activatity and "activatity_index" not in current_activatity:
                current_activatity["activatity_index"] = current_activatity.pop("index")
            migrated["current_activatity"] = current_activatity
        return migrated

    @staticmethod
    def _is_context_entry(value):
        return isinstance(value, dict) and "value" in value

    @classmethod
    def _make_context_entry(
        cls,
        value,
        *,
        activity_id: str = None,
        work_item: str = None,
        role: str = None,
        output: dict = None,
        created_at: str = None,
        status: str = None,
        error: str = None,
        duration_ms=None,
    ):
        if cls._is_context_entry(value):
            entry = deepcopy(value)
            entry.setdefault("created_at", created_at or utc_now_iso())
            entry.setdefault("status", status or "completed")
            entry.setdefault("error", error)
            entry.setdefault("duration_ms", duration_ms)
            return entry
        return {
            "activity_id": activity_id,
            "work_item": work_item,
            "role": role,
            "value": value,
            "output": deepcopy(output or {}),
            "status": status or "completed",
            "error": error,
            "duration_ms": duration_ms,
            "created_at": created_at or utc_now_iso(),
        }

    @classmethod
    def _context_entries(cls, context: dict, key: str):
        value = context.get(key)
        if value is None:
            return []
        if isinstance(value, list):
            return [cls._make_context_entry(item) for item in value]
        return [cls._make_context_entry(value)]

    @classmethod
    def _context_values(cls, context: dict, key: str):
        return [entry.get("value") for entry in cls._context_entries(context, key)]

    @classmethod
    def _latest_context_value(cls, context: dict, key: str):
        entries = cls._context_entries(context, key)
        if not entries:
            return None
        return entries[-1].get("value")

    @classmethod
    def _normalize_result_collection(cls, value):
        if value is None:
            return []
        if isinstance(value, list):
            return [cls._make_context_entry(item) for item in value]
        return [cls._make_context_entry(value)]

    def _normalize_context(self, context: dict):
        normalized = self.initial_workflow_context()
        normalized.update(self._migrate_legacy_context(context))
        for key in (
            "recon_report",
            "strike_result",
            "eval_score",
            "commander_decision",
            "assault_result",
            "replan_result",
        ):
            normalized[key] = self._normalize_result_collection(normalized.get(key))
        normalized["battle_log"] = list(normalized.get("battle_log", []))
        normalized["completed_roles"] = list(normalized.get("completed_roles", []))
        normalized["attachments"] = normalize_attachments(normalized.get("attachments", []))
        normalized["agent_results"] = dict(normalized.get("agent_results", {}) or {})
        normalized["trace"] = list(normalized.get("trace", []))
        normalized["work_list"] = list(normalized.get("work_list", []))
        if self.bpel_definition:
            existing_items = {
                item.get("activatity_id") or item.get("activity_id"): item
                for item in normalized["work_list"]
                if item.get("activatity_id") or item.get("activity_id")
            }
            normalized["work_list"] = [
                {**item, **existing_items.get(item["activatity_id"], {})}
                for item in self._initial_work_list()
            ]
        normalized["workflow_id"] = self.workflow_id
        normalized["workflow_mode"] = self.mode
        normalized["workflow_name"] = self.workflow
        normalized["workflow_status"] = normalized.get("workflow_status", "running")
        normalized["workflow_activatity"] = int(normalized.get("workflow_activatity", 0) or 0)
        normalized["workflow_activity"] = normalized["workflow_activatity"]
        normalized["current_activatity"] = normalized.get("current_activatity")
        normalized["current_activity"] = normalized["current_activatity"]
        normalized["active_activatities"] = list(normalized.get("active_activatities", []))
        normalized["active_activities"] = list(normalized["active_activatities"])
        normalized["last_work_item"] = normalized.get("last_work_item")
        normalized["last_role"] = normalized.get("last_role")
        normalized["last_error"] = normalized.get("last_error")
        return normalized

    def _recover_interrupted_activities(self, context: dict):
        if not self.bpel_definition:
            return []

        recovered = []
        for item in context.get("work_list", []):
            if item.get("status") != "running":
                continue
            item["status"] = "pending"
            item["error"] = "Recovered interrupted running activity from checkpoint"
            item.pop("finished_at", None)
            item["updated_at"] = utc_now_iso()
            recovered.append(item.get("activatity_id") or item.get("activity_id"))

        if recovered:
            context["active_activatities"] = []
            context["active_activities"] = []
            if (context.get("current_activatity") or {}).get("activatity_id") in recovered:
                context["current_activatity"] = None
                context["current_activity"] = None
            append_trace(
                context,
                "interrupted_activities_recovered",
                workflow_id=self.workflow_id,
                activity_ids=recovered,
            )
            log_event(
                "interrupted_activities_recovered",
                workflow_id=self.workflow_id,
                activity_ids=recovered,
            )
        return recovered

    def _default_workflow_state(self):
        context = self._normalize_context(self.initial_workflow_context())
        return {
            "workflow_id": self.workflow_id,
            "workflow": self.workflow,
            "mode": self.mode,
            "status": context["workflow_status"],
            "created_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
                "current_activatity": None,
                "current_activity": None,
                "last_error": None,
                "context": context,
            }

    def _load_or_initialize_workflow_state(self):
        if self.resume and self.state_store.exists(self.workflow_id):
            state = self.state_store.load(self.workflow_id)
            context = self._normalize_context(state.get("context", {}))
            self._recover_interrupted_activities(context)
            state["workflow_id"] = self.workflow_id
            state["workflow"] = self.workflow
            state["mode"] = self.mode
            state["status"] = state.get("status") or context["workflow_status"]
            state["current_activatity"] = (
                state.get("current_activatity")
                or state.get("current_activity")
                or state.pop("current_step", None)
                or context.get("current_activatity")
            )
            state["current_activity"] = state["current_activatity"]
            state["last_error"] = state.get("last_error") or context.get("last_error")
            state["context"] = context
            self.state_store.save(self.workflow_id, state)
            print(f"[STATE] Resumed workflow {self.workflow_id}")
            return state

        state = self._default_workflow_state()
        self.state_store.save(self.workflow_id, state)
        if self.resume:
            print(f"[STATE] Resume requested but no checkpoint found; started new workflow {self.workflow_id}")
        else:
            print(f"[STATE] Started new workflow {self.workflow_id}")
        return state

    def _save_workflow_checkpoint(
        self,
        context: dict,
        status: str = None,
        current_activatity: dict = None,
        last_error: str = None,
    ):
        with self._checkpoint_lock:
            normalized = self._normalize_context(context)
            if status is not None:
                normalized["workflow_status"] = status
            if current_activatity is not None:
                normalized["current_activatity"] = current_activatity
                normalized["current_activity"] = current_activatity
            if last_error is not None:
                normalized["last_error"] = last_error

            context.clear()
            context.update(normalized)
            normalized = context

            state = {
                "workflow_id": self.workflow_id,
                "workflow": self.workflow,
                "mode": self.mode,
                "status": normalized["workflow_status"],
                "created_at": self.workflow_state.get("created_at", utc_now_iso()),
                "updated_at": utc_now_iso(),
                "current_activatity": normalized.get("current_activatity"),
                "current_activity": normalized.get("current_activatity"),
                "last_error": normalized.get("last_error"),
                "context": normalized,
            }
            self.workflow_state = state
            self.workflow_context = normalized
            self.state_store.save(self.workflow_id, state)

    def merge_external_attachments(self, attachments: list[dict] | None):
        if not attachments:
            return self.workflow_context["attachments"]

        merged = merge_attachments(self.workflow_context.get("attachments", []), attachments)
        self.workflow_context["attachments"] = merged
        self._save_workflow_checkpoint(
            self.workflow_context,
            status=self.workflow_context.get("workflow_status", "running"),
            current_activatity=self.workflow_context.get("current_activatity"),
            last_error=self.workflow_context.get("last_error"),
        )
        return merged

    def _work_item_for_activatity(self, role: str, activatity_index: int):
        return f"{self.workflow_id}:{activatity_index}:{role}"

    @staticmethod
    def _context_snapshot(context: dict):
        return {
            "workflow_id": context.get("workflow_id"),
            "workflow_mode": context.get("workflow_mode"),
            "workflow_name": context.get("workflow_name"),
            "workflow_status": context.get("workflow_status"),
            "workflow_activatity": context.get("workflow_activatity"),
            "workflow_activity": context.get("workflow_activity", context.get("workflow_activatity")),
            "current_activatity": context.get("current_activatity"),
            "current_activity": context.get("current_activity", context.get("current_activatity")),
            "active_activatities": list(context.get("active_activatities", [])),
            "active_activities": list(context.get("active_activities", context.get("active_activatities", []))),
            "sector": context.get("sector"),
            "coordinates": context.get("coordinates"),
            "recon_report": context.get("recon_report"),
            "strike_result": context.get("strike_result"),
            "eval_score": context.get("eval_score"),
            "commander_decision": context.get("commander_decision"),
            "assault_result": context.get("assault_result"),
            "closed_loop_result": context.get("closed_loop_result"),
            "replan_result": context.get("replan_result"),
            "completed_roles": list(context.get("completed_roles", [])),
            "battle_log": list(context.get("battle_log", [])),
            "last_work_item": context.get("last_work_item"),
            "last_role": context.get("last_role"),
            "last_error": context.get("last_error"),
            "attachments": attachment_snapshot(context.get("attachments", [])),
            "work_list": deepcopy(context.get("work_list", [])),
            "agent_results": deepcopy(context.get("agent_results", {})),
            "trace_tail": deepcopy(context.get("trace", [])[-20:]),
        }

    @classmethod
    def build_closed_loop_results_from_context(cls, context: dict) -> dict:
        return build_standard_results_from_context(
            context,
            latest_value=cls._latest_context_value,
        )

    def build_task_payload(self, role: str, context: dict, activatity_index: int = None, **legacy_kwargs):
        if activatity_index is None:
            activatity_index = legacy_kwargs.pop("step_index", None)
        if activatity_index is None:
            raise ValueError("activatity_index is required")
        work_item = self._work_item_for_activatity(role, activatity_index)
        context_snapshot = self._context_snapshot(context)

        if role == "recon":
            return {
                "workflow_id": self.workflow_id,
                "workflow": self.workflow,
                "workflow_mode": self.mode,
                "work_item": work_item,
                "parent_work_item": context.get("last_work_item"),
                "activatity_index": activatity_index,
                "activatity_role": role,
                "command": "scan_beach_defenses",
                "input": {
                    "sector": context["sector"],
                },
                "context": context_snapshot,
                "attachments": attachment_snapshot(context.get("attachments", [])),
                "work_list": deepcopy(context.get("work_list", [])),
                "output_hint": "recon_report",
            }, False

        if role == "artillery":
            return {
                "workflow_id": self.workflow_id,
                "workflow": self.workflow,
                "workflow_mode": self.mode,
                "work_item": work_item,
                "parent_work_item": context.get("last_work_item"),
                "activatity_index": activatity_index,
                "activatity_role": role,
                "command": "suppress_beach_sector_A",
                "input": {
                    "coordinates": context["coordinates"],
                    "intensity": "high",
                    "recon_report": self._context_entries(context, "recon_report"),
                },
                "context": context_snapshot,
                "attachments": attachment_snapshot(context.get("attachments", [])),
                "work_list": deepcopy(context.get("work_list", [])),
                "output_hint": "strike_result",
            }, True

        if role == "evaluator":
            return {
                "workflow_id": self.workflow_id,
                "workflow": self.workflow,
                "workflow_mode": self.mode,
                "work_item": work_item,
                "parent_work_item": context.get("last_work_item"),
                "activatity_index": activatity_index,
                "activatity_role": role,
                "command": "evaluate_strike",
                "input": {
                    "target_coordinates": context["coordinates"],
                    "recon_report": self._context_entries(context, "recon_report"),
                    "strike_result": self._context_entries(context, "strike_result"),
                    "mock_eval_score": self.mock_eval_score if self.mock_eval_score is not None else 40,
                },
                "context": context_snapshot,
                "attachments": attachment_snapshot(context.get("attachments", [])),
                "work_list": deepcopy(context.get("work_list", [])),
                "output_hint": "eval_score",
            }, False

        if role == "assault":
            return {
                "workflow_id": self.workflow_id,
                "workflow": self.workflow,
                "workflow_mode": self.mode,
                "work_item": work_item,
                "parent_work_item": context.get("last_work_item"),
                "activatity_index": activatity_index,
                "activatity_role": role,
                "command": "capture_beachhead",
                "input": {
                    "coordinates": context["coordinates"],
                    "recon_report": self._context_entries(context, "recon_report"),
                    "strike_result": self._context_entries(context, "strike_result"),
                    "eval_score": self._context_entries(context, "eval_score"),
                    "commander_decision": self._context_entries(context, "commander_decision"),
                },
                "context": context_snapshot,
                "attachments": attachment_snapshot(context.get("attachments", [])),
                "work_list": deepcopy(context.get("work_list", [])),
                "output_hint": "assault_result",
            }, False

        if role == "closed_loop":
            dataset_paths = {}
            xbd_damage_csv = os.environ.get("CLOSED_LOOP_XBD_DAMAGE_CSV")
            sc2le_task_csv = os.environ.get("CLOSED_LOOP_SC2LE_TASK_CSV")
            if xbd_damage_csv:
                dataset_paths["xbd_damage_csv"] = xbd_damage_csv
            if sc2le_task_csv:
                dataset_paths["sc2le_task_csv"] = sc2le_task_csv

            input_data = {
                "target_count": int(os.environ.get("CLOSED_LOOP_TARGET_COUNT", "50")),
                "cycles": int(os.environ.get("CLOSED_LOOP_CYCLES", "3")),
                "results": self.build_closed_loop_results_from_context(context),
            }
            if dataset_paths:
                input_data["dataset_paths"] = dataset_paths

            return {
                "workflow_id": self.workflow_id,
                "workflow": self.workflow,
                "workflow_mode": self.mode,
                "work_item": work_item,
                "parent_work_item": context.get("last_work_item"),
                "activatity_index": activatity_index,
                "activatity_role": role,
                "command": "closed_loop_optimization",
                "input": input_data,
                "context": context_snapshot,
                "attachments": attachment_snapshot(context.get("attachments", [])),
                "work_list": deepcopy(context.get("work_list", [])),
                "output_hint": "closed_loop_result",
            }, False

        raise ValueError(f"Unsupported role: {role}")

    @staticmethod
    def _first_output_value(output: dict):
        for value in (output or {}).values():
            return value
        return None

    def _append_output_collection(
        self,
        context: dict,
        target_key: str,
        value,
        *,
        activity_id: str = None,
        work_item: str = None,
        role: str = None,
        output: dict = None,
        status: str = "completed",
        error: str = None,
        duration_ms=None,
    ):
        existing = context.get(target_key)
        if not isinstance(existing, list):
            existing = []
            context[target_key] = existing

        entry = self._make_context_entry(
            value,
            activity_id=activity_id,
            work_item=work_item,
            role=role,
            output=output or {target_key: value},
            status=status,
            error=error,
            duration_ms=duration_ms,
        )
        existing.append(entry)
        return entry

    @staticmethod
    def _default_output_key_for_role(role: str):
        return {
            "recon": "recon_report",
            "artillery": "strike_result",
            "evaluator": "eval_score",
            "commander": "commander_decision",
            "assault": "assault_result",
            "closed_loop": "closed_loop_result",
        }.get(role)

    @staticmethod
    def _response_duration_ms(response: dict):
        metrics = (response or {}).get("metrics", {}) or {}
        return metrics.get("duration_ms", metrics.get("latency_ms"))

    def apply_agent_result(
        self,
        role: str,
        success: bool,
        context: dict,
        work_item: str = None,
        output_key: str = None,
        output_collection: bool = True,
        activity_id: str = None,
    ):
        work_item = work_item or context.get("last_work_item")
        response = self._task_response_for_work_item(work_item, context) or self._task_response_for_context(context) or {}
        response_status = str(response.get("status") or ("completed" if success else "failed")).lower()
        response_error = response.get("error") or (None if success else context.get("last_error") or "Task failed")
        duration_ms = self._response_duration_ms(response)
        if not success:
            context["battle_log"].append(f"[{role} Error] Task failed or no available agent.")
            failed_output_key = output_key or self._default_output_key_for_role(role)
            if failed_output_key:
                self._append_output_collection(
                    context,
                    failed_output_key,
                    None,
                    activity_id=activity_id,
                    work_item=work_item,
                    role=role,
                    output=response.get("output", {}) or {},
                    status="failed",
                    error=response_error,
                    duration_ms=duration_ms,
                )
            self._trace(
                "agent_result_failed",
                role=role,
                work_item=work_item,
                output_key=failed_output_key,
                status="failed",
                error=response_error,
                duration_ms=duration_ms,
            )
            return

        output = response.get("output", {}) or {}

        if role == "recon":
            target_key = output_key or "recon_report"
            output_value = output.get(target_key)
            if output_value is None:
                output_value = self._first_output_value(output)
            if output_value is None:
                output_value = "Sector_A is heavily fortified with overlapping machine gun nests."
            self._append_output_collection(
                context,
                target_key,
                output_value,
                activity_id=activity_id,
                work_item=work_item,
                role=role,
                output=output,
                status=response_status,
                error=response_error,
                duration_ms=duration_ms,
            )
            context["battle_log"].append(f"[Recon Report] {output_value}")
        elif role == "artillery":
            target_key = output_key or "strike_result"
            output_value = output.get(target_key)
            if output_value is None:
                output_value = self._first_output_value(output)
            if output_value is None:
                output_value = "Suppression barrage executed on Sector_A."
            self._append_output_collection(
                context,
                target_key,
                output_value,
                activity_id=activity_id,
                work_item=work_item,
                role=role,
                output=output,
                status=response_status,
                error=response_error,
                duration_ms=duration_ms,
            )
            context["battle_log"].append(f"[Artillery Report] {output_value}")
        elif role == "evaluator":
            target_key = output_key or "eval_score"
            raw_score = output.get(target_key)
            if raw_score is None:
                raw_score = self._first_output_value(output)
            if raw_score is None:
                raw_score = self.mock_eval_score if self.mock_eval_score is not None else 40
            output_value = int(raw_score) if target_key == "eval_score" else raw_score
            self._append_output_collection(
                context,
                target_key,
                output_value,
                activity_id=activity_id,
                work_item=work_item,
                role=role,
                output=output,
                status=response_status,
                error=response_error,
                duration_ms=duration_ms,
            )
            context["battle_log"].append(
                f"[Eval Report] Effectiveness matches {output_value}% destruction rate."
            )
        elif role == "assault":
            target_key = output_key or "assault_result"
            output_value = output.get(target_key)
            if output_value is None:
                output_value = self._first_output_value(output)
            if output_value is None:
                output_value = "Assault unit captured the beachhead."
            self._append_output_collection(
                context,
                target_key,
                output_value,
                activity_id=activity_id,
                work_item=work_item,
                role=role,
                output=output,
                status=response_status,
                error=response_error,
                duration_ms=duration_ms,
            )
            context["battle_log"].append(f"[Assault Report] {output_value}")
        elif role == "closed_loop":
            target_key = output_key or "closed_loop_result"
            output_value = output.get(target_key)
            if output_value is None:
                output_value = self._first_output_value(output)
            if output_value is None:
                output_value = {
                    "status": "completed",
                    "message": "Closed-loop optimization completed, but no structured result was returned.",
                }
            self._append_output_collection(
                context,
                target_key,
                output_value,
                activity_id=activity_id,
                work_item=work_item,
                role=role,
                output=output,
                status=response_status,
                error=response_error,
                duration_ms=duration_ms,
            )
            result_payload = output_value if isinstance(output_value, dict) else {}
            output_data = result_payload.get("output_data", {}) if isinstance(result_payload, dict) else {}
            requirement_report = output_data.get("requirement_report", {})
            meets_requirements = output_data.get("meets_requirements")
            processed_targets = output_data.get("execution_control", {}).get("processed_targets")
            context["battle_log"].append(
                "[Closed Loop Report] "
                f"processed_targets={processed_targets}, "
                f"meets_requirements={meets_requirements}, "
                f"requirement_report={requirement_report}"
            )

        if role not in context["completed_roles"]:
            context["completed_roles"].append(role)
        self._trace(
            "agent_result_applied",
            role=role,
            work_item=work_item,
            output_key=output_key,
            output_collection=True,
            status=response_status,
            error=response_error,
            duration_ms=duration_ms,
            output=output,
        )

    def rule_next_step(self, context: dict):
        """Fast state-machine planner. Returns an action dict or None when rules are unsure."""
        if not self._context_entries(context, "recon_report"):
            return {"type": "agent", "role": "recon", "reason": "No recon report is available."}

        if not self._context_entries(context, "strike_result"):
            return {"type": "agent", "role": "artillery", "reason": "Recon is done but suppression has not run."}

        if not self._context_entries(context, "eval_score"):
            return {"type": "agent", "role": "evaluator", "reason": "Strike result needs evaluation."}

        if not self._context_entries(context, "commander_decision"):
            return {"type": "decision", "reason": "Evaluation is available; commander must decide."}

        decision = str(self._latest_context_value(context, "commander_decision") or "").upper()
        if "ASSAULT" in decision and "RE-PLAN" not in decision and not self._context_entries(context, "assault_result"):
            return {"type": "agent", "role": "assault", "reason": "Commander decision allows assault."}

        if "RE-PLAN" in decision or "ABORT" in decision:
            return {"type": "end", "reason": "Commander selected re-plan or abort."}

        if self._context_entries(context, "assault_result") and not self._context_entries(context, "closed_loop_result"):
            return {"type": "agent", "role": "closed_loop", "reason": "Assault is done; run final effect assessment and closed-loop optimization."}

        if self._context_entries(context, "assault_result"):
            return {"type": "end", "reason": "Assault phase completed."}

        return None

    def llm_next_step(self, context: dict):
        """Fallback planner used only when rule_next_step cannot decide."""
        if not self.api_key:
            print("[PLANNER] No OPENAI_API_KEY. Fallback defaults to end.")
            return {"type": "end", "reason": "LLM fallback unavailable because OPENAI_API_KEY is not set."}

        try:
            from langchain_core.prompts import PromptTemplate

            llm = self.build_llm()
            prompt = PromptTemplate.from_template(
                "You are an A2A workflow planner. Choose the next action from this set only:\n"
                "- recon\n- artillery\n- evaluator\n- assault\n- closed_loop\n- decision\n- end\n\n"
                "Rules:\n"
                "1. Return only one word from the set.\n"
                "2. Use end if the workflow should stop.\n"
                "3. Use decision if the commander should analyze battle_log.\n\n"
                "Workflow context JSON:\n{context_json}"
            )
            chain = prompt | llm
            response = chain.invoke({"context_json": json.dumps(context, ensure_ascii=False)})
            choice = response.content.strip().lower()
            print(f"[LLM FALLBACK] Suggested next action: {choice}")

            if choice in {"recon", "artillery", "evaluator", "assault", "closed_loop"}:
                return {"type": "agent", "role": choice, "reason": "LLM fallback selected an agent role."}
            if choice == "decision":
                return {"type": "decision", "reason": "LLM fallback selected commander decision."}
            if choice == "end":
                return {"type": "end", "reason": "LLM fallback selected end."}
            return {"type": "end", "reason": f"LLM fallback returned invalid action: {choice}"}
        except Exception as e:
            return {"type": "end", "reason": f"LLM fallback failed: {e}"}

    def get_next_step(self, context: dict):
        step = self.rule_next_step(context)
        if step:
            step["planner"] = "rule"
            return step

        step = self.llm_next_step(context)
        step["planner"] = "llm_fallback"
        return step

    def parse_commander_decision(self, decision: str):
        normalized = decision.upper()
        if "RE-PLAN" in normalized or "REPLAN" in normalized or "ABORT" in normalized:
            return "RE-PLAN"
        if re.search(r"\bASSAULT\b", normalized):
            return "ASSAULT"
        return decision

    def _work_list_item(self, context: dict, activatity_id: str):
        for item in context.get("work_list", []):
            if item.get("activatity_id") == activatity_id or item.get("activity_id") == activatity_id:
                return item
        raise KeyError(f"Unknown activatity: {activatity_id}")

    def _set_activatity_status(
        self,
        context: dict,
        activatity: BPELActivatity,
        status: str,
        error: str = None,
    ):
        with self._checkpoint_lock:
            item = self._work_list_item(context, activatity.activatity_id)
            item["status"] = status
            item["error"] = error
            item["updated_at"] = utc_now_iso()
            if status == "running":
                item.setdefault("started_at", item["updated_at"])
                if activatity.activatity_id not in context["active_activatities"]:
                    context["active_activatities"].append(activatity.activatity_id)
                context["active_activities"] = list(context["active_activatities"])
                context["workflow_activatity"] = int(context.get("workflow_activatity", 0) or 0) + 1
                context["workflow_activity"] = context["workflow_activatity"]
            elif status in {"completed", "failed", "skipped"}:
                item["finished_at"] = item["updated_at"]
                if activatity.activatity_id in context["active_activatities"]:
                    context["active_activatities"].remove(activatity.activatity_id)
                context["active_activities"] = list(context["active_activatities"])

            current_activatity = {
                "activatity_id": activatity.activatity_id,
                "activatity_index": item["activatity_index"],
                "activity_id": activatity.activatity_id,
                "activity_index": item["activatity_index"],
                "work_item": item["work_item"],
                "type": activatity.type,
                "role": activatity.role,
                "status": status,
            }
            context["current_activatity"] = current_activatity
            context["current_activity"] = current_activatity
            if activatity.type == "invoke":
                context["last_work_item"] = item["work_item"]
                context["last_role"] = activatity.role
            append_trace(
                context,
                "activity_status_changed",
                workflow_id=self.workflow_id,
                activity_id=activatity.activatity_id,
                work_item=item["work_item"],
                role=activatity.role,
                activity_type=activatity.type,
                status=status,
                error=error,
            )
            log_event(
                "activity_status_changed",
                workflow_id=self.workflow_id,
                activity_id=activatity.activatity_id,
                work_item=item["work_item"],
                role=activatity.role,
                activity_type=activatity.type,
                status=status,
                error=error,
            )
            self._save_workflow_checkpoint(
                context,
                status=context.get("workflow_status", "running"),
                current_activatity=current_activatity,
                last_error=error,
            )

    def _skip_activatity_tree(self, context: dict, activatity: BPELActivatity):
        item = self._work_list_item(context, activatity.activatity_id)
        if item.get("status") == "pending":
            self._set_activatity_status(context, activatity, "skipped")
        for child in activatity.children:
            self._skip_activatity_tree(context, child)

    @staticmethod
    def _context_key_for_bpel_variable(variable_name: str | None):
        return {
            "ReconReport": "recon_report",
            "StrikeCoordinates": "coordinates",
            "StrikeResult": "strike_result",
            "EvalScore": "eval_score",
            "CommanderDecision": "commander_decision",
            "AssaultResult": "assault_result",
            "ReplanResult": "replan_result",
            "Sector_A": "sector",
        }.get(variable_name, variable_name)

    @staticmethod
    def _result_collection_keys():
        return {
            "recon_report",
            "strike_result",
            "eval_score",
            "commander_decision",
            "assault_result",
            "closed_loop_result",
            "replan_result",
        }

    def _context_input_value(self, context: dict, key: str, default=None):
        if key in self._result_collection_keys():
            return self._context_entries(context, key)
        return context.get(key, default)

    def _output_keys_for_activatity_tree(self, activatity: BPELActivatity):
        keys = set()
        if activatity.type == "invoke":
            output_key = self._context_key_for_bpel_variable(activatity.output_variable)
            if output_key:
                keys.add(output_key)
        if activatity.type == "assign":
            assign_key = self._context_key_for_bpel_variable(activatity.assign_to)
            if assign_key:
                keys.add(assign_key)
        for child in activatity.children:
            keys.update(self._output_keys_for_activatity_tree(child))
        return keys

    def _input_keys_for_activatity_tree(self, activatity: BPELActivatity):
        keys = set()
        if activatity.type == "invoke":
            input_key = self._context_key_for_bpel_variable(activatity.input_variable)
            if input_key:
                keys.add(input_key)
        if activatity.type in {"switch", "case"} and activatity.condition:
            keys.update(self._condition_input_keys(activatity.condition))
        for child in activatity.children:
            keys.update(self._input_keys_for_activatity_tree(child))
        return keys

    def _condition_input_keys(self, condition: str | None):
        if not condition:
            return set()
        return {
            self._context_key_for_bpel_variable(variable)
            for variable in re.findall(r"getVariableData\(['\"]([^'\"]+)['\"]\)", condition)
        }

    def _direct_output_keys_for_activity(self, activatity: BPELActivatity):
        if activatity.type == "invoke":
            output_key = self._context_key_for_bpel_variable(activatity.output_variable)
            return {output_key} if output_key else set()
        if activatity.type == "assign":
            assign_key = self._context_key_for_bpel_variable(activatity.assign_to)
            return {assign_key} if assign_key else set()
        return set()

    def _direct_input_keys_for_activity(self, activatity: BPELActivatity):
        keys = set()
        if activatity.type == "invoke":
            input_key = self._context_key_for_bpel_variable(activatity.input_variable)
            if input_key:
                keys.add(input_key)
        if activatity.type in {"switch", "case"}:
            keys.update(self._condition_input_keys(activatity.condition))
        return keys

    def _output_writers_for_activatity_tree(self, activatity: BPELActivatity):
        writers = {}
        if activatity.type == "invoke":
            output_key = self._context_key_for_bpel_variable(activatity.output_variable)
            if output_key:
                writers.setdefault(output_key, []).append(activatity.activatity_id)
        if activatity.type == "assign":
            assign_key = self._context_key_for_bpel_variable(activatity.assign_to)
            if assign_key:
                writers.setdefault(assign_key, []).append(activatity.activatity_id)
        for child in activatity.children:
            for output_key, child_writers in self._output_writers_for_activatity_tree(child).items():
                writers.setdefault(output_key, []).extend(child_writers)
        return writers

    def _flow_output_collection_groups(self, flow_activatity: BPELActivatity):
        writers_by_key = {}
        for child in flow_activatity.children:
            for output_key, writers in self._output_writers_for_activatity_tree(child).items():
                writers_by_key.setdefault(output_key, []).extend(writers)

        return {
            output_key: writers
            for output_key, writers in writers_by_key.items()
            if len(writers) > 1
        }

    def _register_flow_output_collections(self, context: dict, collection_groups: dict):
        previous = {}
        with self._checkpoint_lock:
            for output_key, writers in collection_groups.items():
                for writer_id in writers:
                    previous[writer_id] = self._bpel_output_collection_writers.get(writer_id)
                    self._bpel_output_collection_writers[writer_id] = output_key
        return previous

    def _restore_flow_output_collections(self, previous: dict):
        with self._checkpoint_lock:
            for writer_id, previous_key in previous.items():
                if previous_key is None:
                    self._bpel_output_collection_writers.pop(writer_id, None)
                else:
                    self._bpel_output_collection_writers[writer_id] = previous_key

    def _output_collection_key_for_activity(self, activatity_id: str):
        with self._checkpoint_lock:
            return self._bpel_output_collection_writers.get(activatity_id)

    def _resolve_dependency_reference(
        self,
        reference: str,
        *,
        scope: list[BPELActivatity] | None = None,
        consumer: BPELActivatity = None,
    ):
        if not reference:
            return None
        if reference in self.bpel_definition.activatities_by_id:
            return reference

        candidates = scope or list(self.bpel_definition.activatities_by_id.values())
        matches = [
            activatity.activatity_id
            for activatity in candidates
            if activatity.name == reference
            or activatity.activatity_id == reference
            or activatity.activity_id == reference
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            consumer_name = consumer.name if consumer else "unknown"
            raise ValueError(
                f"Ambiguous dependsOn reference '{reference}' for activity '{consumer_name}'"
            )
        consumer_name = consumer.name if consumer else "unknown"
        raise ValueError(
            f"Unknown dependsOn reference '{reference}' for activity '{consumer_name}'"
        )

    def _flow_branch_dependencies(self, flow_activatity: BPELActivatity):
        branch_io = {}
        for child in flow_activatity.children:
            branch_io[child.activatity_id] = {
                "inputs": self._input_keys_for_activatity_tree(child),
                "outputs": self._output_keys_for_activatity_tree(child),
            }

        dependencies = {child.activatity_id: set() for child in flow_activatity.children}
        edges = []
        for consumer_id, consumer_io in branch_io.items():
            for producer_id, producer_io in branch_io.items():
                if consumer_id == producer_id:
                    continue
                shared_keys = consumer_io["inputs"] & producer_io["outputs"]
                for key in sorted(shared_keys):
                    dependencies[consumer_id].add(producer_id)
                    edges.append(
                        {
                            "consumer": consumer_id,
                            "producer": producer_id,
                            "variable": key,
                        }
                    )

        children = list(flow_activatity.children)
        for consumer in children:
            for reference in consumer.depends_on:
                producer_id = self._resolve_dependency_reference(
                    reference,
                    scope=children,
                    consumer=consumer,
                )
                dependencies[consumer.activatity_id].add(producer_id)
                edges.append(
                    {
                        "consumer": consumer.activatity_id,
                        "producer": producer_id,
                        "variable": None,
                        "source": "dependsOn",
                        "reference": reference,
                    }
                )

        return dependencies, edges

    def _bpel_dependency_graph(self):
        graph = {
            activatity.activatity_id: set()
            for activatity in self.bpel_definition.activatities_by_id.values()
        }

        def add_edge(producer: str, consumer: str):
            if producer and consumer and producer != consumer:
                graph.setdefault(producer, set()).add(consumer)

        writers_by_key = {}
        for activatity in self.bpel_definition.activatities_by_id.values():
            for output_key in self._direct_output_keys_for_activity(activatity):
                writers_by_key.setdefault(output_key, set()).add(activatity.activatity_id)

        for consumer in self.bpel_definition.activatities_by_id.values():
            for input_key in self._direct_input_keys_for_activity(consumer):
                for producer_id in writers_by_key.get(input_key, set()):
                    add_edge(producer_id, consumer.activatity_id)

        def visit(activatity: BPELActivatity):
            for reference in activatity.depends_on:
                producer_id = self._resolve_dependency_reference(
                    reference,
                    consumer=activatity,
                )
                add_edge(producer_id, activatity.activatity_id)

            if activatity.type in {"sequence", "case", "otherwise"}:
                previous = None
                for child in activatity.children:
                    if activatity.type in {"case", "otherwise"}:
                        add_edge(activatity.activatity_id, child.activatity_id)
                    if previous is not None:
                        add_edge(previous.activatity_id, child.activatity_id)
                    previous = child
                    visit(child)
                return

            if activatity.type == "flow":
                dependencies, _ = self._flow_branch_dependencies(activatity)
                for consumer_id, producer_ids in dependencies.items():
                    for producer_id in producer_ids:
                        add_edge(producer_id, consumer_id)
                for child in activatity.children:
                    visit(child)
                return

            if activatity.type == "switch":
                for child in activatity.children:
                    add_edge(activatity.activatity_id, child.activatity_id)
                    visit(child)
                return

            for child in activatity.children:
                visit(child)

        visit(self.bpel_definition.root_activatity)
        return graph

    @staticmethod
    def _downstream_activity_ids(graph: dict, activity_ids: set[str]):
        affected = set(activity_ids)
        queue_items = list(activity_ids)
        while queue_items:
            current = queue_items.pop(0)
            for child_id in graph.get(current, set()):
                if child_id not in affected:
                    affected.add(child_id)
                    queue_items.append(child_id)
        return affected

    def _descendant_activity_ids(self, activity_id: str):
        activatity = self.bpel_definition.activatities_by_id.get(activity_id)
        if not activatity:
            return set()
        descendants = set()

        def visit(node: BPELActivatity):
            for child in node.children:
                descendants.add(child.activatity_id)
                visit(child)

        visit(activatity)
        return descendants

    @staticmethod
    def _reset_work_list_item_for_resume(item: dict, *, reason: str):
        item["status"] = "pending"
        item["error"] = reason
        item.pop("started_at", None)
        item.pop("finished_at", None)
        item["updated_at"] = utc_now_iso()

    def _remove_outputs_for_activities(self, context: dict, affected_ids: set[str]):
        if not affected_ids:
            return {}

        affected_work_items = {
            item.get("work_item")
            for item in context.get("work_list", [])
            if (item.get("activatity_id") or item.get("activity_id")) in affected_ids
        }
        affected_work_items.discard(None)
        removed = {}

        for key in self._result_collection_keys():
            entries = self._context_entries(context, key)
            kept_entries = [
                entry
                for entry in entries
                if entry.get("activity_id") not in affected_ids
                and entry.get("work_item") not in affected_work_items
            ]
            if len(kept_entries) != len(entries):
                removed[key] = len(entries) - len(kept_entries)
                context[key] = kept_entries

        agent_results = context.get("agent_results", {}) or {}
        for work_item in affected_work_items:
            if work_item in agent_results:
                agent_results.pop(work_item, None)
                removed["agent_results"] = removed.get("agent_results", 0) + 1
        context["agent_results"] = agent_results
        return removed

    def _prepare_bpel_resume_context(self, context: dict):
        if not self.bpel_definition:
            return

        raw_failed_ids = {
            item.get("activatity_id") or item.get("activity_id")
            for item in context.get("work_list", [])
            if item.get("status") == "failed"
        }
        raw_failed_ids.discard(None)
        failed_ids = set()
        for activity_id in raw_failed_ids:
            activatity = self.bpel_definition.activatities_by_id.get(activity_id)
            if activatity and activatity.children and self._descendant_activity_ids(activity_id) & raw_failed_ids:
                continue
            failed_ids.add(activity_id)
        if not failed_ids:
            return

        graph = self._bpel_dependency_graph()
        affected_ids = self._downstream_activity_ids(graph, failed_ids)
        affected_ancestors = set()
        for activity_id in affected_ids:
            activatity = self.bpel_definition.activatities_by_id.get(activity_id)
            while activatity and activatity.parent_activatity:
                affected_ancestors.add(activatity.parent_activatity)
                activatity = self.bpel_definition.activatities_by_id.get(activatity.parent_activatity)
        reset_ids = affected_ids | affected_ancestors

        reset_reason = "Reset for DAG local recovery after upstream failure"
        for item in context.get("work_list", []):
            activity_id = item.get("activatity_id") or item.get("activity_id")
            if activity_id in reset_ids and item.get("status") in {"failed", "completed", "skipped", "running"}:
                self._reset_work_list_item_for_resume(item, reason=reset_reason)

        removed = self._remove_outputs_for_activities(context, affected_ids)
        if (context.get("current_activatity") or {}).get("activatity_id") in reset_ids:
            context["current_activatity"] = None
            context["current_activity"] = None
        context["active_activatities"] = []
        context["active_activities"] = []
        append_trace(
            context,
            "dag_resume_cleanup",
            workflow_id=self.workflow_id,
            failed_activity_ids=sorted(failed_ids),
            affected_activity_ids=sorted(affected_ids),
            reset_activity_ids=sorted(reset_ids),
            removed_outputs=removed,
        )
        log_event(
            "dag_resume_cleanup",
            workflow_id=self.workflow_id,
            failed_activity_ids=sorted(failed_ids),
            affected_activity_ids=sorted(affected_ids),
            reset_activity_ids=sorted(reset_ids),
            removed_outputs=removed,
        )

    def _execute_bpel_flow_activatity(self, activatity: BPELActivatity, context: dict):
        collection_groups = self._flow_output_collection_groups(activatity)
        dependencies, dependency_edges = self._flow_branch_dependencies(activatity)
        collection_previous = self._register_flow_output_collections(context, collection_groups)
        max_workers = min(self.max_activity_workers, max(1, len(activatity.children)))
        execution_mode = "dag" if dependency_edges else "parallel"
        self._trace(
            "flow_activity_started",
            activity_id=activatity.activatity_id,
            child_count=len(activatity.children),
            max_activity_workers=self.max_activity_workers,
            execution_mode=execution_mode,
            dependencies=dependency_edges,
            output_collections=collection_groups,
        )
        try:
            with ThreadPoolExecutor(
                max_workers=max_workers,
                thread_name_prefix="a2a-activity-flow",
            ) as executor:
                pending = {child.activatity_id: child for child in activatity.children}
                running = {}
                completed = {}
                results = []

                while pending or running:
                    while len(running) < max_workers:
                        ready = [
                            child
                            for child_id, child in pending.items()
                            if dependencies.get(child_id, set()).issubset(completed.keys())
                        ]
                        if not ready:
                            break
                        child = ready[0]
                        pending.pop(child.activatity_id)
                        future = executor.submit(self._execute_bpel_activatity, child, context)
                        running[future] = child
                        self._trace(
                            "flow_child_activity_scheduled",
                            activity_id=activatity.activatity_id,
                            child_activity_id=child.activatity_id,
                            child_type=child.type,
                            child_role=child.role,
                            dependencies=sorted(dependencies.get(child.activatity_id, set())),
                            execution_mode=execution_mode,
                        )

                    if not running:
                        unresolved = {
                            child_id: sorted(dependencies.get(child_id, set()))
                            for child_id in sorted(pending)
                        }
                        raise ValueError(
                            "BPEL flow dependency cycle or unresolved dependencies: "
                            f"{unresolved}"
                        )

                    done, _ = wait(running.keys(), return_when=FIRST_COMPLETED)
                    for future in done:
                        child = running.pop(future)
                        result = future.result()
                        completed[child.activatity_id] = bool(result)
                        results.append(bool(result))
                        self._trace(
                            "flow_child_activity_finished",
                            activity_id=activatity.activatity_id,
                            child_activity_id=child.activatity_id,
                            child_type=child.type,
                            child_role=child.role,
                            success=bool(result),
                            execution_mode=execution_mode,
                        )

                success = bool(results) and all(results)
        finally:
            self._restore_flow_output_collections(collection_previous)

        self._trace(
            "flow_activity_finished",
            activity_id=activatity.activatity_id,
            success=success,
            execution_mode=execution_mode,
        )
        return success

    def _build_bpel_task_payload(self, activatity: BPELActivatity, context: dict):
        with self._checkpoint_lock:
            item = self._work_list_item(context, activatity.activatity_id)
            parent_item = (
                self._work_list_item(context, activatity.parent_activatity)
                if activatity.parent_activatity
                else None
            )
            input_key = self._context_key_for_bpel_variable(activatity.input_variable)
            input_payload = {}
            if input_key:
                input_payload[input_key] = self._context_input_value(context, input_key, activatity.input_variable)
            if activatity.role == "evaluator":
                input_payload["mock_eval_score"] = self.mock_eval_score if self.mock_eval_score is not None else 40

            return {
                "workflow_id": self.workflow_id,
                "workflow": self.workflow,
                "workflow_mode": self.mode,
                "work_item": item["work_item"],
                "parent_work_item": parent_item.get("work_item") if parent_item else None,
                "activatity_id": activatity.activatity_id,
                "activatity_index": item["activatity_index"],
                "activatity_role": activatity.role,
                "command": activatity.command,
                "input": input_payload,
                "context": self._context_snapshot(context),
                "attachments": attachment_snapshot(context.get("attachments", [])),
                "work_list": deepcopy(context.get("work_list", [])),
                "output_hint": self._context_key_for_bpel_variable(activatity.output_variable),
                "retry_policy": {
                    "max_retries": activatity.retry_count if activatity.retry_count is not None else self.max_retries,
                    "timeout_seconds": activatity.timeout_seconds or self.request_timeout,
                    "failure_policy": activatity.failure_policy,
                },
            }, activatity.role == "artillery"

    def _evaluate_bpel_condition(self, condition: str | None, context: dict):
        if not condition:
            return False

        match = re.search(
            r"getVariableData\(['\"](?P<variable>[^'\"]+)['\"]\)\s*"
            r"(?P<operator><=|>=|==|!=|<|>)\s*(?P<expected>-?\d+(?:\.\d+)?)",
            condition,
        )
        if not match:
            raise ValueError(f"Unsupported BPEL condition: {condition}")

        context_key = self._context_key_for_bpel_variable(match.group("variable"))
        actual_value = self._latest_context_value(context, context_key) if context_key in self._result_collection_keys() else context.get(context_key)
        actual = float(actual_value)
        expected = float(match.group("expected"))
        return {
            "<": actual < expected,
            "<=": actual <= expected,
            ">": actual > expected,
            ">=": actual >= expected,
            "==": actual == expected,
            "!=": actual != expected,
        }[match.group("operator")]

    def _execute_bpel_invoke(self, activatity: BPELActivatity, context: dict):
        if activatity.role == "commander":
            decision = self.ask_llm(context["battle_log"])
            with self._checkpoint_lock:
                parsed_decision = self.parse_commander_decision(decision)
                self._append_output_collection(
                    context,
                    "commander_decision",
                    parsed_decision,
                    activity_id=activatity.activatity_id,
                    work_item=self._work_list_item(context, activatity.activatity_id).get("work_item"),
                    role="commander",
                    output={"commander_decision": parsed_decision, "raw_decision": decision},
                    status="completed",
                )
                context["battle_log"].append(f"[Commander Decision] {decision}")
            return True

        if not activatity.role:
            raise ValueError(f"No role mapping for partnerLink={activatity.partner_link}")

        payload, stream = self._build_bpel_task_payload(activatity, context)
        if activatity.dispatch_mode == "parallel":
            success = self.delegate_parallel_task(activatity.role, payload, stream=stream)
        else:
            success = self.delegate_task(activatity.role, payload, stream=stream)
        with self._checkpoint_lock:
            output_key = self._context_key_for_bpel_variable(activatity.output_variable)
            collection_key = self._output_collection_key_for_activity(activatity.activatity_id)
            self.apply_agent_result(
                activatity.role,
                success,
                context,
                work_item=payload.get("work_item"),
                output_key=output_key,
                output_collection=collection_key == output_key,
                activity_id=activatity.activatity_id,
            )
        return success

    def _execute_bpel_activatity(self, activatity: BPELActivatity, context: dict):
        item = self._work_list_item(context, activatity.activatity_id)
        if item.get("status") in {"completed", "skipped"}:
            return True

        self._set_activatity_status(context, activatity, "running")
        try:
            if activatity.type in {"sequence", "case", "otherwise"}:
                success = all(
                    self._execute_bpel_activatity(child, context)
                    for child in activatity.children
                )
            elif activatity.type == "flow":
                success = self._execute_bpel_flow_activatity(activatity, context)
            elif activatity.type == "assign":
                context_key = self._context_key_for_bpel_variable(activatity.assign_to)
                with self._checkpoint_lock:
                    if context_key in self._result_collection_keys():
                        self._append_output_collection(
                            context,
                            context_key,
                            activatity.assign_from,
                            activity_id=activatity.activatity_id,
                            work_item=item.get("work_item"),
                            role=activatity.role,
                            output={context_key: activatity.assign_from},
                            status="completed",
                        )
                    else:
                        context[context_key] = activatity.assign_from
                success = True
            elif activatity.type == "invoke":
                success = self._execute_bpel_invoke(activatity, context)
            elif activatity.type == "switch":
                selected = None
                for child in activatity.children:
                    if child.type == "case" and self._evaluate_bpel_condition(child.condition, context):
                        selected = child
                        break
                    if child.type == "otherwise":
                        selected = child
                for child in activatity.children:
                    if child is not selected:
                        self._skip_activatity_tree(context, child)
                success = bool(selected) and self._execute_bpel_activatity(selected, context)
            elif activatity.type == "throw":
                raise RuntimeError(activatity.fault_name or "BPEL workflow fault")
            else:
                raise ValueError(f"Unsupported BPEL activatity type: {activatity.type}")
        except Exception as exc:
            diagnostics = exception_diagnostics(exc)
            with self._checkpoint_lock:
                context["last_error"] = str(exc)
                context["last_error_details"] = diagnostics
                self._trace(
                    "activity_exception_captured",
                    activity_id=activatity.activatity_id,
                    work_item=item.get("work_item"),
                    role=activatity.role,
                    **diagnostics,
                )
            if activatity.failure_policy == "skip":
                with self._checkpoint_lock:
                    context["workflow_status"] = "running"
                self._set_activatity_status(context, activatity, "skipped", str(exc))
                return True
            with self._checkpoint_lock:
                context["workflow_status"] = "paused"
            self._set_activatity_status(context, activatity, "failed", str(exc))
            if activatity.failure_policy == "fail":
                raise
            return False

        if not success and activatity.failure_policy == "skip":
            self._set_activatity_status(
                context,
                activatity,
                "skipped",
                f"Activity failed and was skipped by failure_policy: {activatity.activatity_id}",
            )
            return True
        if not success and activatity.failure_policy == "fail":
            raise RuntimeError(context.get("last_error") or f"Activity failed: {activatity.activatity_id}")

        failure_error = context.get("last_error") or f"Activatity failed: {activatity.activatity_id}"
        self._set_activatity_status(
            context,
            activatity,
            "completed" if success else "failed",
            None if success else failure_error,
        )
        return success

    def run_bpel_workflow(self):
        if not self.bpel_definition:
            raise ValueError("No BPEL workflow was loaded")

        context = self.workflow_context
        if context.get("workflow_status") == "completed":
            print(f"[WORKFLOW] Workflow {self.workflow_id} already completed. Nothing to resume.")
            return context

        print(f"\n=== BPEL WORKFLOW: {self.bpel_definition.process_name} ===")
        print(f"[WORKFLOW] Loaded from {self.bpel_definition.source_path}")
        print(f"[WORKFLOW] work_list entries={len(context.get('work_list', []))}")
        self._prepare_bpel_resume_context(context)
        with self._checkpoint_lock:
            context["workflow_status"] = "running"
            context["last_error"] = None
            context["last_error_details"] = None
            self._trace(
                "workflow_started",
                workflow_type="bpel",
                process_name=self.bpel_definition.process_name,
                workflow_file=str(self.bpel_definition.source_path),
            )
            self._save_workflow_checkpoint(context, status="running")

        success = self._execute_bpel_activatity(self.bpel_definition.root_activatity, context)
        with self._checkpoint_lock:
            context["workflow_status"] = "completed" if success else "paused"
            self._trace(
                "workflow_finished",
                workflow_type="bpel",
                status=context["workflow_status"],
                last_error=context.get("last_error"),
            )
            self._save_workflow_checkpoint(
                context,
                status=context["workflow_status"],
                current_activatity=context.get("current_activatity"),
                last_error=context.get("last_error"),
            )

        print("\n================= WORKFLOW CONTEXT =================")
        print(json.dumps(context, ensure_ascii=False, indent=2))
        print("====================================================")
        return context

    def run_dynamic_battle_scenario(self, max_steps: int = 10):
        context = self.workflow_context

        if context.get("workflow_status") == "completed":
            print(f"[WORKFLOW] Workflow {self.workflow_id} already completed. Nothing to resume.")
            print("\n================= WORKFLOW CONTEXT =================")
            print(json.dumps(context, ensure_ascii=False, indent=2))
            print("====================================================")
            return context

        print("\n=== DYNAMIC WORKFLOW: RULE STATE MACHINE + LLM FALLBACK ===")
        print(f"[WORKFLOW] Resuming from workflow_activatity={context.get('workflow_activatity', 0)}")
        self._trace(
            "workflow_started",
            workflow_type="dynamic",
            start_activity=int(context.get("workflow_activatity", 0) or 0) + 1,
        )

        start_activatity = int(context.get("workflow_activatity", 0) or 0) + 1
        for activatity_index in range(start_activatity, start_activatity + max_steps):
            step = self.get_next_step(context)
            current_activatity = {
                "activatity_index": activatity_index,
                "activity_index": activatity_index,
                "planner": step.get("planner"),
                "type": step.get("type"),
                "role": step.get("role"),
                "reason": step.get("reason"),
            }
            context["workflow_activatity"] = activatity_index
            context["workflow_activity"] = activatity_index
            context["current_activatity"] = current_activatity
            context["current_activity"] = current_activatity
            context["workflow_status"] = "running"
            self._trace(
                "dynamic_activity_started",
                activity_index=activatity_index,
                role=step.get("role"),
                action=step.get("type"),
                reason=step.get("reason"),
            )
            self._save_workflow_checkpoint(context, status="running", current_activatity=current_activatity)

            print(
                f"\n=== ACTIVATITY {activatity_index}: planner={step.get('planner')} "
                f"action={step.get('type')} reason={step.get('reason')} ==="
            )

            if step["type"] == "agent":
                role = step["role"]
                payload, stream = self.build_task_payload(role, context, activatity_index)
                context["last_work_item"] = payload["work_item"]
                context["last_role"] = role
                self._save_workflow_checkpoint(context, status="running", current_activatity=current_activatity)

                success = self.delegate_task(role, payload, stream=stream)
                if not success:
                    error_message = f"Agent execution failed for role={role}"
                    context["last_error"] = error_message
                    context["workflow_status"] = "paused"
                    self._save_workflow_checkpoint(
                        context,
                        status="paused",
                        current_activatity=current_activatity,
                        last_error=error_message,
                    )
                    print("[WORKFLOW] Agent execution failed. Stop current workflow.")
                    break

                self.apply_agent_result(role, success, context)
                context["last_error"] = None
                self._save_workflow_checkpoint(context, status="running", current_activatity=current_activatity)
                continue

            if step["type"] == "decision":
                print("[PLANNER] Commander is analyzing workflow context and battle log...")
                context["last_role"] = "commander"
                context["last_work_item"] = f"{self.workflow_id}:{activatity_index}:decision"
                self._save_workflow_checkpoint(context, status="running", current_activatity=current_activatity)

                decision = self.ask_llm(context["battle_log"])
                parsed_decision = self.parse_commander_decision(decision)
                self._append_output_collection(
                    context,
                    "commander_decision",
                    parsed_decision,
                    activity_id=str(activatity_index),
                    work_item=context["last_work_item"],
                    role="commander",
                    output={"commander_decision": parsed_decision, "raw_decision": decision},
                    status="completed",
                )
                context["battle_log"].append(f"[Commander Decision] {decision}")
                self._save_workflow_checkpoint(context, status="running", current_activatity=current_activatity)
                print("\n================= COMMANDER ORDER =================")
                print(decision)
                print("===================================================")
                continue

            if step["type"] == "end":
                reason = (step.get("reason") or "").lower()
                final_status = "paused" if ("re-plan" in reason or "abort" in reason) else "completed"
                context["workflow_status"] = final_status
                self._trace(
                    "workflow_finished",
                    workflow_type="dynamic",
                    status=final_status,
                    reason=step.get("reason"),
                )
                self._save_workflow_checkpoint(context, status=final_status, current_activatity=current_activatity)
                print(f"[WORKFLOW] End: {step.get('reason')}")
                break
        else:
            context["workflow_status"] = "paused"
            self._trace(
                "workflow_finished",
                workflow_type="dynamic",
                status="paused",
                reason=f"Reached max_steps={max_steps}",
            )
            self._save_workflow_checkpoint(context, status="paused")
            print(f"[WORKFLOW] Reached max_steps={max_steps}. Stop current workflow.")

        print("\n================= WORKFLOW CONTEXT =================")
        print(json.dumps(context, ensure_ascii=False, indent=2))
        print("====================================================")
        return context

    def run_battle_scenario(self):
        battle_log = []
        
        print("\n=== PHASE 1: RECONNAISSANCE ===")
        recon_task = {
            "command": "scan_beach_defenses",
            "sector": "Sector_A"
        }
        self.delegate_task("recon", recon_task)
        battle_log.append("[Recon Report] Sector_A is heavily fortified with overlapping machine gun nests.")

        print("\n=== PHASE 2: ARTILLERY STRIKE (STREAMING) ===")
        strike_task = {
            "command": "suppress_beach_sector_A",
            "coordinates": "120.5E, 35.1N",
            "intensity": "high"
        }
        success = self.delegate_task("artillery", strike_task, stream=True)
        battle_log.append("[Artillery Report] Suppression barrage executed on Sector_A.")
        
        if success:
            print("\n=== PHASE 3: EVALUATE OUTCOME ===")
            eval_task = {
                "command": "evaluate_strike",
                "target_coordinates": "120.5E, 35.1N"
            }
            eval_success = self.delegate_task("evaluator", eval_task)
            battle_log.append("[Eval Report] Effectiveness matches 40% destruction rate. Defenses still operational.")
            
            print("\n=== PHASE 4: LLM COMMANDER DECISION ===")
            print("[PLANNER] AI Commander is analyzing battle logs to decide next move...")
            decision = self.ask_llm(battle_log)
            print("\n================= LLM COMMANDER ORDER =================")
            print(decision)
            print("========================================================")

def parse_args():
    parser = argparse.ArgumentParser(description="A2A Commander Agent")
    parser.add_argument(
        "--mode",
        choices=["remote", "local"],
        default=os.environ.get("A2A_COMMANDER_MODE", "remote"),
        help="remote uses Nacos + HTTP A2A; local runs an in-process workflow simulation.",
    )
    parser.add_argument(
        "--workflow",
        choices=["bpel", "dynamic", "legacy"],
        default="bpel",
        help="bpel dynamically loads a workflow definition; dynamic uses the rule state-machine; legacy runs the fixed scenario.",
    )
    parser.add_argument(
        "--workflow-file",
        default=None,
        help="BPEL file path, filename, stem, or process name. Defaults to the first discovered .bpel workflow.",
    )
    parser.add_argument(
        "--list-workflows",
        action="store_true",
        help="List discovered BPEL workflow definitions and exit.",
    )
    parser.add_argument(
        "--workflow-id",
        default=None,
        help="Reuse an existing workflow checkpoint id to resume a previous run.",
    )
    parser.add_argument(
        "--state-dir",
        default=None,
        help="Directory used to persist workflow checkpoints.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from an existing workflow checkpoint if it exists.",
    )
    parser.add_argument(
        "--serve-recovery-api",
        action="store_true",
        help="Start the workflow recovery HTTP API instead of executing a workflow immediately.",
    )
    parser.add_argument(
        "--serve-workflow-manager",
        action="store_true",
        help="Start the resident multi-workflow manager HTTP API.",
    )
    parser.add_argument(
        "--recovery-host",
        default="127.0.0.1",
        help="Host used by the recovery HTTP API.",
    )
    parser.add_argument(
        "--recovery-port",
        type=int,
        default=8020,
        help="Port used by the recovery HTTP API.",
    )
    parser.add_argument(
        "--manager-host",
        default="127.0.0.1",
        help="Host used by the resident workflow manager HTTP API.",
    )
    parser.add_argument(
        "--manager-port",
        type=int,
        default=8021,
        help="Port used by the resident workflow manager HTTP API.",
    )
    parser.add_argument(
        "--max-workflows",
        type=int,
        default=4,
        help="Maximum number of workflows executed concurrently by the resident manager.",
    )
    parser.add_argument("--max-steps", type=int, default=10)
    parser.add_argument(
        "--max-workers",
        type=int,
        default=None,
        help="Backward-compatible worker limit used for both activity and Agent workers when the split limits are omitted.",
    )
    parser.add_argument(
        "--max-activity-workers",
        type=int,
        default=None,
        help="Maximum number of concurrently executed BPEL flow activatities inside one workflow.",
    )
    parser.add_argument(
        "--max-agent-workers",
        type=int,
        default=None,
        help="Maximum number of concurrently dispatched same-role Agent instances for one activity.",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=int(os.environ.get("A2A_MAX_RETRIES", "1")),
        help="Maximum retries per remote Agent candidate.",
    )
    parser.add_argument(
        "--retry-backoff",
        type=float,
        default=float(os.environ.get("A2A_RETRY_BACKOFF", "0.2")),
        help="Base seconds to wait between retry attempts.",
    )
    parser.add_argument(
        "--request-timeout",
        type=float,
        default=float(os.environ.get("A2A_REQUEST_TIMEOUT", "5")),
        help="HTTP timeout in seconds for A2A discovery/auth/sendMessage.",
    )
    parser.add_argument(
        "--mock-eval-score",
        type=int,
        default=None,
        help="Local/mock evaluation score used by evaluator and no-key LLM decision.",
    )
    parser.add_argument(
        "--mock-decision",
        choices=["ASSAULT", "RE-PLAN"],
        default=None,
        help="Force commander decision in local/mock runs.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    if args.list_workflows:
        catalog = BPELWorkflowCatalog(PROJECT_ROOT)
        definitions = [catalog.load(str(path)) for path in catalog.discover()]
        print("Available BPEL workflows:")
        for definition in definitions:
            print(f"- {definition.process_name}: {definition.source_path}")
        raise SystemExit(0)

    if args.serve_recovery_api:
        import uvicorn

        from commander_agent.recovery_api import build_recovery_app

        app = build_recovery_app(
            default_mode=args.mode,
            default_workflow=args.workflow,
            default_state_dir=args.state_dir,
        )
        uvicorn.run(app, host=args.recovery_host, port=args.recovery_port)
        raise SystemExit(0)

    if args.serve_workflow_manager:
        import uvicorn

        from commander_agent.manager_api import build_workflow_manager_app

        app = build_workflow_manager_app(
            mode=args.mode,
            state_dir=args.state_dir,
            max_workflows=args.max_workflows,
        )
        uvicorn.run(app, host=args.manager_host, port=args.manager_port)
        raise SystemExit(0)

    cmd = CommanderAgent(
        mode=args.mode,
        workflow=args.workflow,
        workflow_file=args.workflow_file,
        workflow_id=args.workflow_id,
        state_dir=args.state_dir,
        resume=args.resume,
        mock_eval_score=args.mock_eval_score,
        mock_decision=args.mock_decision,
        max_workers=args.max_workers,
        max_activity_workers=args.max_activity_workers,
        max_agent_workers=args.max_agent_workers,
        max_retries=args.max_retries,
        retry_backoff=args.retry_backoff,
        request_timeout=args.request_timeout,
    )

    if args.workflow == "legacy":
        cmd.run_battle_scenario()
    elif args.workflow == "bpel":
        cmd.run_bpel_workflow()
    else:
        cmd.run_dynamic_battle_scenario(max_steps=args.max_steps)
