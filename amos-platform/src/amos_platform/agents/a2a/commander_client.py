"""A2A Commander workflow HTTP client."""

from __future__ import annotations

import json
import os
from typing import Any

import urllib.error
import urllib.request


_A2A_COMMANDER_URL = os.environ.get("A2A_COMMANDER_URL", "http://127.0.0.1:8021")
_A2A_COMMANDER_TOKEN = os.environ.get("A2A_COMMANDER_TOKEN", "")


class CommanderClient:
    """HTTP client for the A2A Commander Workflow Manager."""

    def __init__(
        self,
        base_url: str = "",
        token: str = "",
        timeout_sec: int = 30,
    ):
        self.base_url = (base_url or _A2A_COMMANDER_URL).rstrip("/")
        self.token = token or _A2A_COMMANDER_TOKEN
        self.timeout_sec = timeout_sec

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if extra:
            headers.update(extra)
        return headers

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self._url(path),
            data=data,
            headers=self._headers({"Content-Type": "application/json"}),
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8"))
            except Exception:
                detail = {"detail": str(exc)}
            return {"error": True, "code": exc.code, **detail}
        except Exception as exc:
            return {"error": True, "code": 503, "detail": str(exc)}

    def _get(self, path: str) -> dict[str, Any]:
        req = urllib.request.Request(
            self._url(path),
            headers=self._headers(),
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8"))
            except Exception:
                detail = {"detail": str(exc)}
            return {"error": True, "code": exc.code, **detail}
        except Exception as exc:
            return {"error": True, "code": 503, "detail": str(exc)}

    def health(self) -> dict[str, Any]:
        """GET /health - Commander health check."""
        return self._get("/health")

    def submit_workflow(
        self,
        workflow: str = "bpel",
        workflow_file: str | None = None,
        workflow_id: str | None = None,
        resume: bool = False,
        max_steps: int = 10,
        max_workers: int = 4,
        max_retries: int = 1,
        retry_backoff: float = 0.2,
        request_timeout: float = 5.0,
        mock_eval_score: int | None = None,
        mock_decision: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
        task_goal: str | None = None,
        required_skills: list[str] | None = None,
        auto_decompose: bool = False,
    ) -> dict[str, Any]:
        """POST /workflows - submit a new workflow."""
        body: dict[str, Any] = {
            "workflow": workflow,
            "max_steps": max_steps,
            "max_workers": max_workers,
            "max_retries": max_retries,
            "retry_backoff": retry_backoff,
            "request_timeout": request_timeout,
        }
        if workflow_file:
            body["workflow_file"] = workflow_file
        if workflow_id:
            body["workflow_id"] = workflow_id
        if resume:
            body["resume"] = True
        if mock_eval_score is not None:
            body["mock_eval_score"] = mock_eval_score
        if mock_decision:
            body["mock_decision"] = mock_decision
        if attachments:
            body["attachments"] = attachments
        if task_goal:
            body["task_goal"] = task_goal
        if required_skills:
            body["required_skills"] = required_skills
        if auto_decompose:
            body["auto_decompose"] = True

        return self._post("/workflows", body)

    def get_workflow(self, workflow_id: str) -> dict[str, Any]:
        """GET /workflows/{id} - get workflow status."""
        return self._get(f"/workflows/{workflow_id}")

    def get_work_list(self, workflow_id: str) -> dict[str, Any]:
        """GET /workflows/{id}/work-list - get planned activities."""
        return self._get(f"/workflows/{workflow_id}/work-list")

    def get_workflow_trace(self, workflow_id: str) -> dict[str, Any]:
        """GET /workflows/{id}/trace - get execution trace."""
        return self._get(f"/workflows/{workflow_id}/trace")

    def resume_workflow(self, workflow_id: str, **kwargs: Any) -> dict[str, Any]:
        """POST /workflows/{id}/resume - resume a paused/failed workflow."""
        body: dict[str, Any] = {k: v for k, v in kwargs.items() if v is not None}
        return self._post(f"/workflows/{workflow_id}/resume", body)

__all__ = ["CommanderClient"]
