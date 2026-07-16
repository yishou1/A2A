from __future__ import annotations

import threading

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from typing import Callable, Optional

from commander_agent.agent_leases import AgentLeaseManager
from commander_agent.main import CommanderAgent, PROJECT_ROOT
from registry.nacos_manager import NacosRegistry
from telemetry import traced_method
from workflow_payloads import normalize_attachments
from workflow_state_store import WorkflowStateStore, new_workflow_id, utc_now_iso


class WorkflowManager:
    def __init__(
        self,
        *,
        mode: str = "local",
        state_dir: str | None = None,
        max_workflows: int = 4,
        commander_factory: Callable[..., CommanderAgent] = CommanderAgent,
        registry=None,
        lease_manager=None,
    ):
        self.mode = mode
        self.state_dir = state_dir
        self.max_workflows = max(1, int(max_workflows))
        self.commander_factory = commander_factory
        self.registry = registry if registry is not None else (
            None if mode == "local" else NacosRegistry()
        )
        self.lease_manager = lease_manager if lease_manager is not None else (
            None if mode == "local" else AgentLeaseManager(self.registry)
        )
        default_state_dir = f"{PROJECT_ROOT}/.a2a_state/workflows"
        self.state_store = WorkflowStateStore(state_dir or default_state_dir)
        self._executor = ThreadPoolExecutor(max_workers=self.max_workflows)
        self._lock = threading.RLock()
        self._jobs: dict[str, dict] = {}
        self._closed = False

    def submit_workflow(self, **kwargs) -> dict:
        workflow_id = kwargs.get("workflow_id")
        with self._lock:
            if self._closed:
                raise RuntimeError("workflow manager is already shut down")
            if workflow_id:
                existing = self._jobs.get(workflow_id)
                if existing and existing.get("status") in {"queued", "running"}:
                    raise ValueError(f"workflow {workflow_id} is already active")
        if kwargs.get("resume") and workflow_id and not self.state_store.exists(workflow_id):
            raise FileNotFoundError(f"Workflow checkpoint not found: {workflow_id}")
        return self.start_workflow(**kwargs)

    def resume_workflow(self, workflow_id: str, **kwargs) -> dict:
        return self.submit_workflow(workflow_id=workflow_id, resume=True, **kwargs)

    def start_workflow(
        self,
        *,
        workflow: str = "bpel",
        workflow_file: str | None = None,
        workflow_id: str | None = None,
        resume: bool = False,
        max_steps: int = 10,
        max_workers: Optional[int] = None,
        max_activity_workers: Optional[int] = None,
        max_agent_workers: Optional[int] = None,
        max_retries: int = 1,
        retry_backoff: float = 0.2,
        request_timeout: float = 5.0,
        mock_eval_score: Optional[int] = None,
        mock_decision: Optional[str] = None,
        initial_context: dict | None = None,
        attachments: list[dict] | None = None,
    ) -> dict:
        workflow_id = workflow_id or new_workflow_id()
        attachments = normalize_attachments(attachments or [])
        job = {
            "workflow_id": workflow_id,
            "workflow": workflow,
            "workflow_file": workflow_file,
            "mode": self.mode,
            "status": "queued",
            "created_at": utc_now_iso(),
            "started_at": None,
            "finished_at": None,
            "last_error": None,
            "attachments": deepcopy(attachments),
        }
        with self._lock:
            self._jobs[workflow_id] = job
        future = self._executor.submit(
            self._run_workflow,
            workflow_id,
            workflow,
            workflow_file,
            resume,
            max_steps,
            max_workers,
            max_activity_workers,
            max_agent_workers,
            max_retries,
            retry_backoff,
            request_timeout,
            mock_eval_score,
            mock_decision,
            deepcopy(initial_context or {}),
            attachments,
        )
        with self._lock:
            self._jobs[workflow_id]["future"] = future
        return self.get_workflow(workflow_id)

    def list_workflows(self) -> list[dict]:
        with self._lock:
            return [self._job_snapshot(job) for job in self._jobs.values()]

    def get_workflow(self, workflow_id: str, include_checkpoint: bool = False) -> dict:
        with self._lock:
            job = self._jobs.get(workflow_id)
            if job is not None:
                result = self._job_snapshot(job)
            elif self.state_store.exists(workflow_id):
                result = {
                    "workflow_id": workflow_id,
                    "status": "checkpoint_only",
                    "state_path": str(self.state_store.state_path(workflow_id)),
                }
            else:
                raise KeyError(workflow_id)
        if include_checkpoint and self.state_store.exists(workflow_id):
            result["checkpoint"] = self.state_store.load(workflow_id)
        return result

    def list_agent_leases(self) -> list[dict]:
        if self.lease_manager is None:
            return []
        return self.lease_manager.list_leases()

    def list_agents(self) -> list[dict]:
        if self.registry is None:
            return []
        return self.registry.discover_service("A2A-Agent")

    def get_checkpoint(self, workflow_id: str) -> dict:
        if not self.state_store.exists(workflow_id):
            raise KeyError(f"Workflow checkpoint not found: {workflow_id}")
        return self.state_store.load(workflow_id)

    def get_work_list(self, workflow_id: str) -> list[dict]:
        return self.get_checkpoint(workflow_id).get("context", {}).get("work_list", [])

    def get_trace(self, workflow_id: str) -> list[dict]:
        return self.get_checkpoint(workflow_id).get("context", {}).get("trace", [])

    def wait_for_workflow(self, workflow_id: str, timeout: float | None = None) -> dict:
        with self._lock:
            if workflow_id not in self._jobs:
                raise KeyError(workflow_id)
            future = self._jobs[workflow_id].get("future")
        if future is not None:
            future.result(timeout=timeout)
        result = self.get_workflow(workflow_id)
        if self.state_store.exists(workflow_id):
            result["checkpoint"] = self.state_store.load(workflow_id)
        return result

    def shutdown(self, wait: bool = True) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._executor.shutdown(wait=wait)
        if self.lease_manager is not None:
            close = getattr(self.lease_manager, "close", None)
            if callable(close):
                close()
        if self.registry is not None:
            close = getattr(self.registry, "close", None)
            if callable(close):
                close()

    @traced_method(
        "a2a.workflow.run",
        lambda self, workflow_id, workflow, *args, **kwargs: {
            "a2a.workflow_id": str(workflow_id),
            "a2a.workflow_type": str(workflow),
            "a2a.mode": str(self.mode),
        },
    )
    def _run_workflow(
        self,
        workflow_id: str,
        workflow: str,
        workflow_file: str | None,
        resume: bool,
        max_steps: int,
        max_workers: Optional[int],
        max_activity_workers: Optional[int],
        max_agent_workers: Optional[int],
        max_retries: int,
        retry_backoff: float,
        request_timeout: float,
        mock_eval_score: Optional[int],
        mock_decision: Optional[str],
        initial_context: dict,
        attachments: list[dict],
    ) -> None:
        self._update_job(workflow_id, status="running", started_at=utc_now_iso())
        try:
            commander = self.commander_factory(
                mode=self.mode,
                workflow=workflow,
                workflow_file=workflow_file,
                workflow_id=workflow_id,
                state_dir=self.state_dir,
                resume=resume,
                mock_eval_score=mock_eval_score,
                mock_decision=mock_decision,
                max_workers=max_workers,
                max_activity_workers=max_activity_workers,
                max_agent_workers=max_agent_workers,
                max_retries=max_retries,
                retry_backoff=retry_backoff,
                request_timeout=request_timeout,
                initial_context=initial_context,
                registry=self.registry,
                lease_manager=self.lease_manager,
            )
            if attachments:
                commander.merge_external_attachments(attachments)
            if workflow == "bpel":
                context = commander.run_bpel_workflow()
            else:
                context = commander.run_dynamic_battle_scenario(max_steps=max_steps)
            self._update_job(
                workflow_id,
                status=context.get("workflow_status", "completed"),
                finished_at=utc_now_iso(),
                current_activity=context.get("current_activity") or context.get("current_activatity"),
                last_error=context.get("last_error"),
                trace_count=len(context.get("trace", [])),
                result=deepcopy(context.get("workflow_result")),
            )
        except Exception as exc:
            self._update_job(
                workflow_id,
                status="failed",
                finished_at=utc_now_iso(),
                last_error=str(exc),
            )
            raise
        finally:
            if self.lease_manager is not None:
                self.lease_manager.release_workflow(workflow_id)

    def _update_job(self, workflow_id: str, **updates) -> None:
        with self._lock:
            self._jobs[workflow_id].update(updates)

    @staticmethod
    def _job_snapshot(job: dict) -> dict:
        return {
            key: deepcopy(value)
            for key, value in job.items()
            if key != "future"
        }


CommanderWorkflowManager = WorkflowManager
