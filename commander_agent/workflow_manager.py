from __future__ import annotations

import os
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
from typing import Any, Callable, Optional

from commander_agent.agent_leases import AgentLeaseManager
from commander_agent.distributed_lock import RedisDistributedLock
from commander_agent.main import CommanderAgent, PROJECT_ROOT
from registry.nacos_manager import NacosRegistry
from workflow_payloads import normalize_attachments
from workflow_state_store import WorkflowStateStore, new_workflow_id, utc_now_iso


class CommanderWorkflowManager:
    """Resident control plane that advances multiple workflows concurrently."""

    def __init__(
        self,
        *,
        mode: str = "remote",
        state_dir: Optional[str] = None,
        max_workflows: int = 4,
        registry=None,
        commander_factory: Callable[..., CommanderAgent] = CommanderAgent,
    ):
        if mode not in {"local", "remote"}:
            raise ValueError("mode must be either 'remote' or 'local'")
        self.mode = mode
        self.state_dir = state_dir or os.environ.get(
            "A2A_STATE_DIR",
            os.path.join(PROJECT_ROOT, ".a2a_state", "workflows"),
        )
        self.max_workflows = max(1, int(max_workflows))
        self.state_store = WorkflowStateStore(self.state_dir)
        self.registry = None if mode == "local" else (registry or NacosRegistry())
        self.lease_manager = (
            AgentLeaseManager(
                self.registry,
                distributed_lock=RedisDistributedLock.from_env(),
            )
            if self.registry is not None
            else None
        )
        self.commander_factory = commander_factory
        self._executor = ThreadPoolExecutor(
            max_workers=self.max_workflows,
            thread_name_prefix="a2a-workflow",
        )
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._closed = False

    def submit_workflow(
        self,
        *,
        workflow: str = "bpel",
        workflow_file: Optional[str] = None,
        workflow_id: Optional[str] = None,
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
        attachments: Optional[list[dict]] = None,
    ) -> dict:
        if workflow not in {"bpel", "dynamic"}:
            raise ValueError("workflow must be either 'bpel' or 'dynamic'")
        workflow_id = workflow_id or new_workflow_id()
        with self._lock:
            if self._closed:
                raise RuntimeError("workflow manager is already shut down")
            existing = self._jobs.get(workflow_id)
            if existing and existing["status"] in {"queued", "running"}:
                raise ValueError(f"Workflow is already active: {workflow_id}")
            if resume and not self.state_store.exists(workflow_id):
                raise FileNotFoundError(f"Workflow checkpoint not found: {workflow_id}")

            now = utc_now_iso()
            job = {
                "workflow_id": workflow_id,
                "workflow": workflow,
                "workflow_file": workflow_file,
                "mode": self.mode,
                "status": "queued",
                "submitted_at": now,
                "started_at": None,
                "finished_at": None,
                "last_error": None,
                "state_path": str(self.state_store.state_path(workflow_id)),
            }
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
                normalize_attachments(attachments or []),
            )
            job["future"] = future
            return self._job_snapshot(job)

    def resume_workflow(self, workflow_id: str, **kwargs) -> dict:
        return self.submit_workflow(
            workflow_id=workflow_id,
            resume=True,
            **kwargs,
        )

    def get_workflow(self, workflow_id: str, include_checkpoint: bool = False) -> dict:
        with self._lock:
            job = self._jobs.get(workflow_id)
            if job:
                snapshot = self._job_snapshot(job)
            elif self.state_store.exists(workflow_id):
                snapshot = {
                    "workflow_id": workflow_id,
                    "status": "checkpoint_only",
                    "state_path": str(self.state_store.state_path(workflow_id)),
                }
            else:
                raise KeyError(f"Workflow not found: {workflow_id}")
        if include_checkpoint and self.state_store.exists(workflow_id):
            snapshot["checkpoint"] = self.state_store.load(workflow_id)
        return snapshot

    def list_workflows(self) -> list[dict]:
        with self._lock:
            return [
                self._job_snapshot(job)
                for job in sorted(
                    self._jobs.values(),
                    key=lambda item: item["submitted_at"],
                    reverse=True,
                )
            ]

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
        checkpoint = self.get_checkpoint(workflow_id)
        return checkpoint.get("context", {}).get("work_list", [])

    def get_trace(self, workflow_id: str) -> list[dict]:
        checkpoint = self.get_checkpoint(workflow_id)
        return checkpoint.get("context", {}).get("trace", [])

    def wait_for_workflow(self, workflow_id: str, timeout: Optional[float] = None) -> dict:
        with self._lock:
            job = self._jobs.get(workflow_id)
            if not job:
                raise KeyError(f"Workflow not found: {workflow_id}")
            future: Future = job["future"]
        future.result(timeout=timeout)
        return self.get_workflow(workflow_id, include_checkpoint=True)

    def shutdown(self, wait: bool = True) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._executor.shutdown(wait=wait)
        if self.lease_manager is not None:
            self.lease_manager.close()
        if self.registry is not None:
            self.registry.close()

    def _run_workflow(
        self,
        workflow_id: str,
        workflow: str,
        workflow_file: Optional[str],
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
