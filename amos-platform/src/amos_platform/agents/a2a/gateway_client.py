"""HTTP client for the A2A Commander Gateway public contract."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


class GatewayClient:
    """Small dependency-free client used by the AMOS server boundary."""

    def __init__(self, base_url: str, token: str = "", timeout_sec: float = 30) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_sec = timeout_sec

    def _headers(self, *, json_body: bool = False) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if json_body:
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers=self._headers(json_body=body is not None),
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_sec) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8"))
            except Exception:
                detail = {"detail": str(exc)}
            return {"error": True, "code": exc.code, **detail}
        except Exception as exc:
            return {"error": True, "code": 503, "detail": str(exc)}

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/gateway/v1/health")

    def submit_workflow(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/gateway/v1/workflows", payload)

    def get_workflow(self, workflow_id: str) -> dict[str, Any]:
        return self._request("GET", f"/gateway/v1/workflows/{workflow_id}")

    def get_package(self, package_id: str) -> dict[str, Any]:
        return self._request("GET", f"/gateway/v1/packages/{package_id}")

    def get_work_list(self, workflow_id: str) -> dict[str, Any]:
        return self._request("GET", f"/gateway/v1/workflows/{workflow_id}/work-list")

    def get_workflow_trace(self, workflow_id: str) -> dict[str, Any]:
        return self._request("GET", f"/gateway/v1/workflows/{workflow_id}/trace")

    def resume_workflow(self, workflow_id: str, **_kwargs: Any) -> dict[str, Any]:
        return self._request("POST", f"/gateway/v1/workflows/{workflow_id}/resume", {})


__all__ = ["GatewayClient"]
